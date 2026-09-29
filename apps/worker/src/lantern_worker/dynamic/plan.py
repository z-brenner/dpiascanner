"""What to run in the sandbox, in priority order.

1. The repository's tests, if present.
2. Its declared run script (``dynamic.run`` in ``lantern.yml``).
3. Otherwise start the application and exercise its routes with synthetic requests.

Tests rarely carry Katz's canary values, so they can reveal which hosts an application
contacts but can seldom verify a sink. Modes therefore run in this order and the verifier
moves to the next one only while reachable network sinks remain unverified and the time
budget allows. A repository that declares nothing gets whichever modes can be detected.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from lantern_analysis.model import DataFlowGraph

# Placeholder in a server command for the port the backend assigns (also exported as PORT).
PORT = "{port}"
NPM_DEFAULT_TEST = 'echo "Error: no test specified" && exit 1'
_PY_APP = re.compile(r"^(\w+)\s*=\s*(FastAPI|Starlette|Flask|Quart|Litestar)\(", re.M)
_SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "tests", "test", "dist", "build"})


@dataclass(frozen=True)
class Endpoint:
    env: str
    url: str
    rewrite: str = "prefix"


@dataclass
class DynamicSettings:
    """The ``dynamic`` section of lantern.yml."""

    run: str | None = None
    start: str | None = None
    port: int = 8000
    tests: bool = True
    env: dict[str, str] = field(default_factory=dict)
    endpoints: list[Endpoint] = field(default_factory=list)

    @classmethod
    def load(cls, repo: Path) -> DynamicSettings:
        path = repo / "lantern.yml"
        if not path.exists():
            return cls()
        data = (yaml.safe_load(path.read_text("utf-8")) or {}).get("dynamic") or {}
        endpoints = []
        for env, spec in (data.get("endpoints") or {}).items():
            if isinstance(spec, dict):
                endpoints.append(
                    Endpoint(str(env), str(spec["url"]), str(spec.get("rewrite", "prefix")))
                )
            else:
                endpoints.append(Endpoint(str(env), str(spec)))
        return cls(
            run=data.get("run"),
            start=data.get("start"),
            port=int(data.get("port", 8000)),
            tests=bool(data.get("tests", True)),
            env={str(k): str(v) for k, v in (data.get("env") or {}).items()},
            endpoints=endpoints,
        )


@dataclass(frozen=True)
class Step:
    mode: str  # tests | script | routes
    argv: tuple[str, ...]  # for routes: the command that starts the server
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "argv": list(self.argv), "detail": self.detail}


def _walk(repo: Path, suffix: str) -> list[Path]:
    out = []
    for path in sorted(repo.rglob(f"*{suffix}")):
        rel = path.relative_to(repo)
        if not any(part in _SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1]):
            out.append(path)
    return out


def detect_language(repo: Path) -> str:
    if (repo / "package.json").exists():
        return "typescript"
    return "python"


def _python_tests(repo: Path) -> bool:
    if (repo / "pytest.ini").exists() or (repo / "conftest.py").exists():
        return True
    pyproject = repo / "pyproject.toml"
    if pyproject.exists() and "[tool.pytest" in pyproject.read_text("utf-8", "replace"):
        return True
    return any(
        (repo / d).is_dir() and any((repo / d).rglob("test_*.py")) for d in ("tests", "test")
    )


def _package_json(repo: Path) -> dict[str, Any]:
    path = repo / "package.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text("utf-8"))
    return data if isinstance(data, dict) else {}


def python_server_command(repo: Path) -> tuple[str, ...] | None:
    for path in _walk(repo, ".py"):
        match = _PY_APP.search(path.read_text("utf-8", "replace"))
        if not match:
            continue
        module = ".".join(path.relative_to(repo).with_suffix("").parts)
        target = f"{module}:{match.group(1)}"
        if match.group(2) in ("Flask", "Quart"):
            return ("python", "-m", "flask", "--app", target, "run", "--port", PORT)
        return ("python", "-m", "uvicorn", target, "--host", "127.0.0.1", "--port", PORT)
    return None


def plan_steps(repo: Path, settings: DynamicSettings, language: str | None = None) -> list[Step]:
    language = language or detect_language(repo)
    steps: list[Step] = []
    if settings.tests:
        if language == "python" and _python_tests(repo):
            steps.append(
                Step("tests", ("python", "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"))
            )
        elif language == "typescript":
            test = (_package_json(repo).get("scripts") or {}).get("test")
            if test and test.strip() != NPM_DEFAULT_TEST:
                steps.append(Step("tests", ("npm", "test", "--silent"), detail=test))
    if settings.run:
        steps.append(Step("script", tuple(shlex.split(settings.run))))
    start: tuple[str, ...] | None = None
    if settings.start:
        start = tuple(shlex.split(settings.start))
    elif language == "python":
        start = python_server_command(repo)
    elif (_package_json(repo).get("scripts") or {}).get("start"):
        start = ("npm", "start", "--silent")
    if start:
        steps.append(Step("routes", start))
    return steps


def static_routes(graph: DataFlowGraph) -> list[dict[str, Any]]:
    """Routes from the graph, each with the request fields its sources read."""
    fields: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for node in graph.nodes.values():
        entry = node.attrs.get("entry") or {}
        if node.kind != "source" or entry.get("kind") != "route":
            continue
        kind = str(node.attrs.get("source_kind", ""))
        location = "form" if kind == "form" else "query" if "query" in kind else "body"
        if kind.startswith("http_body") or kind in ("form",) or "query" in kind:
            key = (node.file, str(entry.get("method") or "GET"), str(entry.get("path") or ""))
            item = {"name": str(node.attrs.get("field", "")), "location": location}
            if item not in fields.setdefault(key, []):
                fields[key].append(item)
    routes = []
    for entry in graph.summary.get("entry_points_detail", []):
        if entry.get("kind") != "route":
            continue
        key = (
            str(entry.get("file", "")),
            str(entry.get("method") or "GET"),
            str(entry.get("path") or ""),
        )
        routes.append(
            {
                "method": key[1],
                "path": key[2],
                "file": entry.get("file", ""),
                "fields": fields.get(key, []),
            }
        )
    return routes
