"""A deterministic secrets scanner and redactor.

Every snippet that leaves the analyzer (graph JSON, decision state, report) passes through
``redact``. Detected secrets are replaced with ``[REDACTED:<kind>]`` and never stored,
logged, or sent anywhere. The patterns favor recall: a false redaction costs a little
context, while a missed secret leaks it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
        ),
    ),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA|ANVA)[A-Z0-9]{16}\b")),
    ("aws_secret_key", re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{22,255})\b"),
    ),
    ("stripe_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{10,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("url_credentials", re.compile(r"(?<=://)[^/\s:@'\"]+:[^/\s@'\"]+(?=@)")),
    ("sendgrid_key", re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b")),
    ("twilio_key", re.compile(r"\bSK[0-9a-fA-F]{32}\b")),
]

# Assignments of string literals to names that look secret-bearing.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<name>[A-Za-z_][A-Za-z0-9_]*(?:secret|passw(?:or)?d|pwd|token|api_?key|apikey|
        private_?key|access_?key|client_?secret|auth|credential|signing_?key)[A-Za-z0-9_]*)
    (?P<sep>["']?\s*(?:=|:|,)\s*)
    (?P<quote>["'])(?P<value>[^"'\n]{8,})(?P=quote)
    """
)
_PLACEHOLDER = re.compile(
    r"(?i)^(replace[-_ ]?me|changeme|your[-_].*|x+|\*+|<.*>|\$\{.*\}|example|test|dummy|none|null)$"
)


@dataclass(frozen=True)
class SecretMatch:
    kind: str
    start: int
    end: int


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {c: value.count(c) for c in set(value)}
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def find_secrets(text: str) -> list[SecretMatch]:
    matches: list[SecretMatch] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            if kind == "aws_secret_key" and shannon_entropy(m.group(0)) < 4.0:
                continue
            matches.append(SecretMatch(kind, m.start(), m.end()))
    for m in _ASSIGNMENT.finditer(text):
        value = m.group("value")
        if _PLACEHOLDER.match(value) or " " in value.strip():
            continue
        if shannon_entropy(value) >= 3.0 or any(ch.isdigit() for ch in value):
            matches.append(SecretMatch("assigned_secret", m.start("value"), m.end("value")))
    matches.sort(key=lambda s: (s.start, -s.end))
    merged: list[SecretMatch] = []
    for match in matches:
        if merged and match.start < merged[-1].end:
            last = merged[-1]
            merged[-1] = SecretMatch(last.kind, last.start, max(last.end, match.end))
        else:
            merged.append(match)
    return merged


def redact(text: str) -> str:
    out: list[str] = []
    cursor = 0
    for match in find_secrets(text):
        out.append(text[cursor : match.start])
        out.append(f"[REDACTED:{match.kind}]")
        cursor = match.end
    out.append(text[cursor:])
    return "".join(out)


def contains_secret(text: str) -> bool:
    return bool(find_secrets(text))


def redact_value(value: Any) -> Any:
    """Recursively redact every string inside a JSON-like value."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {k: redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return value


class SecretInPayloadError(PermissionError):
    pass


def payload_guard(state: Mapping[str, Any]) -> None:
    """Raise if any string in a payload still contains a secret. Wired into providers."""

    def walk(value: Any, path: str) -> None:
        if isinstance(value, str):
            found = find_secrets(value)
            if found:
                raise SecretInPayloadError(f"secret ({found[0].kind}) at {path}; redact first")
        elif isinstance(value, Mapping):
            for k, v in value.items():
                walk(v, f"{path}.{k}")
        elif isinstance(value, list | tuple):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    walk(state, "state")
