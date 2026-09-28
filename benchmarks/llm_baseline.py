"""LLM baseline: give a model the concatenated repository and ask for personal data flows.

    ANTHROPIC_API_KEY=... uv run python benchmarks/llm_baseline.py [--model claude-opus-5-5]
    uv run python benchmarks/llm_baseline.py --replay benchmarks/results/llm-<model>/

The prompt is in prompts/llm_baseline.md. Raw replies are saved next to the scored results so
a run can be audited and re-scored (``--replay``) without calling the API again. Scoring is
identical to the pipeline's (scoring.py). The API host is LANTERN_BASELINE_BASE_URL (default
https://api.anthropic.com); ANTHROPIC_BASE_URL is deliberately ignored.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scoring import BENCHMARK_FIXTURES, FIXTURES, Predicted, aggregate, score_fixture

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
PROMPT = HERE / "prompts" / "llm_baseline.md"
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs"}
CONFIG_SUFFIXES = {".yaml", ".yml", ".json", ".toml", ".cfg", ".ini", ".prisma"}
CONFIG_NAMES = {
    ".env.example",
    "crontab",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "Dockerfile",
}
EXCLUDED_NAMES = {
    "MANIFEST.yaml",
    "README.md",
    "lantern.yml",
    "package-lock.json",
    "pnpm-lock.yaml",
    "uv.lock",
}
EXCLUDED_DIRS = {
    "node_modules",
    ".venv",
    "__pycache__",
    "generated",
    ".pytest_cache",
    "tests",
    "test",
    "testing",
    "dist",
    "build",
}
MAX_FILE_BYTES = 60_000


def repo_files(root: Path) -> list[tuple[str, str]]:
    settings = (
        yaml.safe_load((root / "lantern.yml").read_text())
        if (root / "lantern.yml").exists()
        else {}
    )
    excluded_globs = list(((settings or {}).get("analysis") or {}).get("exclude") or [])
    out = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        name = path.name
        if name in EXCLUDED_NAMES or any(fnmatch.fnmatch(str(rel), g) for g in excluded_globs):
            continue
        if path.suffix in CODE_SUFFIXES | CONFIG_SUFFIXES or name in CONFIG_NAMES:
            text = path.read_text("utf-8", "replace")
            if len(text) <= MAX_FILE_BYTES:
                out.append((str(rel), text))
    return out


def build_prompt(root: Path) -> tuple[str, str]:
    template = PROMPT.read_text()
    system = template.split("## System", 1)[1].split("## User", 1)[0].strip()
    user = template.split("## User", 1)[1].strip()
    files = "\n\n".join(f"=== {rel} ===\n{text}" for rel, text in repo_files(root))
    return system, user.replace("{files}", files)


def call_model(system: str, user: str, model: str, key: str) -> str:
    base = os.environ.get("LANTERN_BASELINE_BASE_URL", "https://api.anthropic.com")
    response = httpx.post(
        f"{base.rstrip('/')}/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        json={
            "model": model,
            "max_tokens": 16000,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=600,
    )
    response.raise_for_status()
    return "".join(
        b.get("text", "") for b in response.json().get("content", []) if b.get("type") == "text"
    )


def parse_reply(text: str) -> list[Predicted]:
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", body, re.S)
    if fence:
        body = fence.group(1)
    elif "{" in body:
        body = body[body.index("{") : body.rindex("}") + 1]
    try:
        data = json.loads(body)
    except ValueError:
        return []
    out = []
    for flow in data.get("flows", []) if isinstance(data, dict) else []:
        try:
            src, snk = flow["source"], flow["sink"]
            out.append(
                Predicted(
                    source_file=str(src.get("file", "")),
                    source_line=int(src["line"]) if src.get("line") is not None else None,
                    field=str(src.get("field")) if src.get("field") else None,
                    sink_file=str(snk["file"]),
                    sink_line=int(snk["line"]),
                    reachable=flow.get("reachable")
                    if isinstance(flow.get("reachable"), bool)
                    else None,
                    resolution=str(flow.get("resolution")) if flow.get("resolution") else None,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--model", default=os.environ.get("LANTERN_BASELINE_MODEL", "claude-opus-5-5")
    )
    parser.add_argument(
        "--replay", type=Path, help="score saved replies instead of calling the API"
    )
    args = parser.parse_args(argv)
    slug = re.sub(r"[^A-Za-z0-9.-]", "-", args.model)
    replies_dir = args.replay or RESULTS / f"llm-{slug}"
    result_path = RESULTS / f"llm-{slug}.json"
    RESULTS.mkdir(exist_ok=True)
    key = os.environ.get("ANTHROPIC_API_KEY")
    if args.replay is None and not key:
        result_path.write_text(
            json.dumps(
                {
                    "system": f"LLM baseline ({args.model})",
                    "kind": "llm",
                    "status": "skipped",
                    "reason": "ANTHROPIC_API_KEY not set",
                },
                indent=2,
            )
            + "\n"
        )
        print("ANTHROPIC_API_KEY not set; recorded the baseline as not run.")
        return 0
    replies_dir.mkdir(parents=True, exist_ok=True)
    scores = []
    for spec in BENCHMARK_FIXTURES:
        reply_path = replies_dir / f"{spec.name}.txt"
        started = time.monotonic()
        if args.replay is None:
            system, user = build_prompt(FIXTURES / spec.name)
            reply_path.write_text(call_model(system, user, args.model, key or ""))
        duration = time.monotonic() - started if args.replay is None else 0.0
        scores.append(score_fixture(spec, parse_reply(reply_path.read_text()), duration))
    result = {
        "system": f"LLM baseline ({args.model})",
        "kind": "llm",
        "status": "completed",
        "aggregate": aggregate(scores).to_dict(),
        "fixtures": [s.to_dict() for s in scores],
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    a = result["aggregate"]
    print(
        f"{result['system']}: recall {a['recall_planted']:.0%}, "
        f"clean FP {a['clean_false_positives']}"
    )
    return 0


def prompt_preview(fixture: str) -> dict[str, Any]:
    system, user = build_prompt(FIXTURES / fixture)
    return {
        "system": system,
        "chars": len(user),
        "files": [rel for rel, _ in repo_files(FIXTURES / fixture)],
    }


if __name__ == "__main__":
    raise SystemExit(main())
