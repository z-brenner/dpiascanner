"""Cross-file configuration: collect region, bucket, endpoint, and environment settings from
config files and attach them to the sinks that reference them. Conflicting values for the same
normalized key are attached together and flagged.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lantern_analysis.detect import SinkSpec
from lantern_analysis.ir import Attr, Expr, Literal, Name, walk_expr
from lantern_analysis.lexicon import tokenize
from lantern_analysis.project import Global, Project
from lantern_analysis.secrets import redact

INTERESTING = re.compile(
    r"(region|bucket|endpoint|host|url|uri|dsn|zone|location|datacenter|residency|base)", re.I
)
SECRETISH = re.compile(r"(secret|password|passwd|token|api_?key|private|credential|auth)", re.I)
SKIP_FILES = frozenset(
    {
        "package.json",
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "tsconfig.json",
        "composer.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "MANIFEST.yaml",
        "lantern.yml",
        ".eslintrc.json",
        "renovate.json",
        "tslint.json",
    }
)
SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "venv",
        "dist",
        "build",
        "generated",
        "tests",
        "test",
        ".github",
    }
)
REGION_KEY = re.compile(r"(^|_)region$")


def normalize_key(key: str) -> str:
    return "_".join(tokenize(key))


@dataclass(frozen=True)
class ConfigValue:
    key: str  # normalized
    raw_key: str
    value: str
    file: str
    line: int

    def to_dict(self, conflict: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "raw_key": self.raw_key,
            "value": self.value,
            "file": self.file,
            "line": self.line,
        }
        if conflict:
            data["conflict"] = True
        return data


def _line_of(text: str, needle: str) -> int:
    for number, line in enumerate(text.splitlines(), 1):
        if needle in line:
            return number
    return 1


def _flatten(data: Any, prefix: str = "") -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    if isinstance(data, dict):
        for key, value in data.items():
            out.extend(_flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(data, list):
        for item in data:
            out.extend(_flatten(item, prefix))
    elif isinstance(data, str | int | float | bool):
        out.append((prefix, data))
    return out


def collect_config(root: Path) -> list[ConfigValue]:
    values: list[ConfigValue] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in SKIP_DIRS for part in rel.split("/")[:-1]) or path.name in SKIP_FILES:
            continue
        name = path.name
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        if len(text) > 500_000:
            continue
        pairs: list[tuple[str, Any, int]] = []
        if name.startswith(".env") or name.endswith(".env"):
            for number, line in enumerate(text.splitlines(), 1):
                m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
                if m:
                    pairs.append((m.group(1), m.group(2).strip().strip("'\""), number))
        elif name == "Dockerfile" or name.startswith("Dockerfile."):
            for number, line in enumerate(text.splitlines(), 1):
                m = re.match(r"^\s*(?:ENV|ARG)\s+([A-Za-z_][A-Za-z0-9_]*)[=\s]+(.*)$", line)
                if m:
                    pairs.append((m.group(1), m.group(2).strip().strip("'\""), number))
        elif name.endswith((".yaml", ".yml", ".json")):
            try:
                data = yaml.safe_load(text) if not name.endswith(".json") else json.loads(text)
            except (yaml.YAMLError, ValueError):
                continue
            for key, value in _flatten(data):
                leaf = key.split(".")[-1]
                pairs.append((key, value, _line_of(text, leaf)))
        elif name.endswith((".tf", ".tfvars", ".hcl")):
            for number, line in enumerate(text.splitlines(), 1):
                m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"([^"]*)"', line)
                if m:
                    pairs.append((m.group(1), m.group(2), number))
        else:
            continue
        for raw_key, value, line_no in pairs:
            if not INTERESTING.search(raw_key):
                continue
            shown = "[REDACTED]" if SECRETISH.search(raw_key) else redact(str(value))
            values.append(ConfigValue(normalize_key(raw_key), raw_key, shown, rel, line_no))
    return values


def sink_identifiers(project: Project, sink: SinkSpec) -> set[str]:
    """Normalized identifiers a sink's call, and its receiver's definition, refer to."""
    fn = project.functions[sink.fid]
    roots: list[Expr] = [sink.call]
    receiver = sink.call.func.base if isinstance(sink.call.func, Attr) else None
    while isinstance(receiver, Attr):
        receiver = receiver.base
    if isinstance(receiver, Name):
        sym = project.lookup(fn, receiver.id)
        if isinstance(sym, Global) or type(sym).__name__ == "Local":
            roots.extend(project.values_of(sym))
    ids: set[str] = set()
    for root in roots:
        for e in walk_expr(root):
            if isinstance(e, Attr):
                ids.add(normalize_key(e.attr))
            elif isinstance(e, Name):
                ids.add(normalize_key(e.id))
            elif (
                isinstance(e, Literal)
                and isinstance(e.value, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", e.value)
            ):
                ids.add(normalize_key(e.value))
    return {i for i in ids if i}


def attach_config(
    project: Project, sink: SinkSpec, config: list[ConfigValue]
) -> tuple[list[dict[str, Any]], bool]:
    ids = sink_identifiers(project, sink)
    matched = [c for c in config if c.key in ids]
    is_cloud = sink.registry is not None and sink.registry.destination_class == "cloud_provider"
    if is_cloud and not any(REGION_KEY.search(c.key) for c in matched):
        matched += [c for c in config if c.key in ("region", "aws_region", "aws_default_region")]
    by_key: dict[str, set[str]] = defaultdict(set)
    for c in matched:
        by_key[c.key].add(c.value)
    conflicting = {k for k, v in by_key.items() if len(v) > 1}
    return [c.to_dict(c.key in conflicting) for c in matched], bool(conflicting)
