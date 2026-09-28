"""Run the Semgrep rule packs and return typed matches with stable rule ids.

Semgrep runs offline (``--metrics=off``, no registry rules, no version check). Metavariable
values travel in the rule message as ``key=value`` pairs, because Semgrep's JSON output does
not include metavariable bindings.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from lantern_analysis.model import Span

RULE_LANGUAGES = {"python": "python", "typescript": "typescript"}
_MESSAGE_KV = re.compile(r"(\w+)=((?:\"[^\"]*\")|(?:'[^']*')|\S+)")


class SemgrepError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuleMatch:
    rule_id: str
    kind: str  # source | sink | transform
    family: str
    span: Span
    values: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.span.file, self.span.start_byte, self.span.end_byte)


def rule_files(language: str) -> list[Path]:
    root = resources.files("lantern_analysis") / "rules" / RULE_LANGUAGES[language]
    return sorted(Path(str(p)) for p in root.iterdir() if str(p).endswith(".yaml"))


def rule_ids(language: str) -> dict[str, dict[str, Any]]:
    """Every rule id in a language pack with its metadata."""
    import yaml

    out: dict[str, dict[str, Any]] = {}
    for path in rule_files(language):
        for rule in yaml.safe_load(path.read_text())["rules"]:
            out[rule["id"]] = dict(rule.get("metadata", {}))
    return out


def _semgrep_binary() -> str:
    found = shutil.which("semgrep")
    if found:
        return found
    candidate = Path(os.sys.executable).parent / "semgrep"  # type: ignore[attr-defined]
    if candidate.exists():
        return str(candidate)
    raise SemgrepError("semgrep is not installed; it is a required dependency of lantern-analysis")


def _parse_values(message: str) -> dict[str, str]:
    values = {}
    for key, raw in _MESSAGE_KV.findall(message):
        value = raw
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _strip_prefix(check_id: str, known: Iterable[str]) -> str:
    for rid in known:
        if check_id == rid or check_id.endswith("." + rid):
            return rid
    return check_id


def run_semgrep(
    root: Path,
    files: Sequence[str],
    language: str,
    timeout_s: int = 600,
    jobs: int | None = None,
) -> list[RuleMatch]:
    """Run one language's rule pack over ``files`` (paths relative to ``root``)."""
    if not files:
        return []
    known = rule_ids(language)
    configs: list[str] = []
    for path in rule_files(language):
        configs += ["--config", str(path)]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("SEMGREP_APP")}
    env["SEMGREP_SEND_METRICS"] = "off"
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as target_list:
        target_list.write("\n".join(files))
    try:
        cmd = [
            _semgrep_binary(),
            "scan",
            *configs,
            "--metrics=off",
            "--disable-version-check",
            "--json",
            "--quiet",
            "--no-git-ignore",
            "--timeout",
            "30",
            "--max-target-bytes",
            "2000000",
            f"--jobs={jobs or max(1, (os.cpu_count() or 2) - 1)}",
            *files,
        ]
        proc = subprocess.run(  # noqa: S603 - fixed argv, no shell
            cmd, cwd=root, capture_output=True, text=True, timeout=timeout_s, env=env, check=False
        )
    finally:
        os.unlink(target_list.name)
    if proc.returncode not in (0, 1) or not proc.stdout.strip():
        raise SemgrepError(f"semgrep failed ({proc.returncode}): {proc.stderr[-2000:]}")
    data = json.loads(proc.stdout)
    matches: list[RuleMatch] = []
    for result in data.get("results", []):
        rid = _strip_prefix(result["check_id"], known)
        meta = known.get(rid, dict(result["extra"].get("metadata", {})))
        start, end = result["start"], result["end"]
        matches.append(
            RuleMatch(
                rule_id=rid,
                kind=str(meta.get("kind", "unknown")),
                family=str(meta.get("family", "unknown")),
                span=Span(
                    file=result["path"],
                    start_line=start["line"],
                    end_line=end["line"],
                    start_byte=start["offset"],
                    end_byte=end["offset"],
                ),
                values=_parse_values(result["extra"].get("message", "")),
                metadata=meta,
            )
        )
    matches.sort(key=lambda m: (m.span.file, m.span.start_byte, m.span.end_byte, m.rule_id))
    return matches
