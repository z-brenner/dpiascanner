"""Recorded outbound requests, reduced to what Lantern keeps.

Both recorders (the mitmproxy addon in the sandbox and ``fixtures/mock-server``) write the
same raw JSONL capture: method, host, path, query, headers, and the base64 body. The raw
capture exists only inside the run directory. ``load_capture`` scans it for canaries, keeps
host, method, path, redacted headers, the body hash, and body field names, and deletes the
raw file. Bodies and query values are never kept.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

from lantern_analysis.secrets import redact
from lantern_worker.dynamic.canaries import CanaryHit, CanarySet, body_field_names

# Header values kept verbatim. Everything else is recorded as present, value redacted.
SAFE_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "host",
        "transfer-encoding",
        "user-agent",
    }
)
MAX_PATH = 512


@dataclass
class ObservedRequest:
    host: str
    method: str
    path: str
    scheme: str
    headers: dict[str, str]
    query_keys: list[str]
    body_sha256: str
    body_size: int
    body_field_names: list[str]
    canary_hits: list[CanaryHit] = field(default_factory=list)
    ts: float = 0.0
    recorder: str = ""

    @property
    def canary_kinds(self) -> list[str]:
        return sorted({h.kind for h in self.canary_hits})

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "method": self.method,
            "path": self.path,
            "scheme": self.scheme,
            "headers": self.headers,
            "query_keys": self.query_keys,
            "body_sha256": self.body_sha256,
            "body_size": self.body_size,
            "body_field_names": self.body_field_names,
            "canary_hits": [h.to_dict() for h in self.canary_hits],
            "ts": self.ts,
            "recorder": self.recorder,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObservedRequest:
        values = dict(data)
        values["canary_hits"] = [CanaryHit(**h) for h in values.get("canary_hits", [])]
        return cls(**values)


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in sorted(headers.items()):
        name = key.lower()
        out[name] = redact(str(value))[:200] if name in SAFE_HEADERS else "[REDACTED]"
    return out


def _query_keys(query: str) -> list[str]:
    return sorted({k for k, _ in parse_qsl(query, keep_blank_values=True)})


def observe(record: dict[str, Any], canaries: CanarySet) -> ObservedRequest:
    """Reduce one raw capture record. The body and query values are dropped here."""
    body = base64.b64decode(record.get("body_b64", "") or "")
    headers = {str(k).lower(): str(v) for k, v in (record.get("headers") or {}).items()}
    query = str(record.get("query", "") or "")
    path = str(record.get("path", "/") if record.get("path") is not None else "/")
    hits: set[CanaryHit] = set()
    hits |= canaries.scan(body, "body")
    if query:
        hits |= canaries.scan(query, "query")
    hits |= canaries.scan(path, "path")
    for name, value in headers.items():
        if value != "[REDACTED]" and name not in ("content-length", "host"):
            hits |= canaries.scan(value, f"header:{name}")
    query_fields = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        decoded = _maybe_b64_json(value)
        if decoded is not None:
            query_fields += [f"{key}:{n}" for n in body_field_names(decoded, "application/json")]
    return ObservedRequest(
        host=str(record.get("host", "")).lower().split(":")[0],
        method=str(record.get("method", "")).upper(),
        path=redact(path)[:MAX_PATH],
        scheme=str(record.get("scheme", "") or ("https" if record.get("tls") else "http")),
        headers=_redact_headers(headers),
        query_keys=sorted({*_query_keys(query), *query_fields}),
        body_sha256=str(record.get("body_sha256", "")),
        body_size=len(body),
        body_field_names=body_field_names(body, headers.get("content-type", "")),
        canary_hits=sorted(hits),
        ts=float(record.get("ts", 0.0)),
        recorder=str(record.get("recorder", "")),
    )


def _maybe_b64_json(value: str) -> bytes | None:
    if len(value) < 8:
        return None
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), validate=False)
    except ValueError:
        return None
    return decoded if decoded.lstrip()[:1] in (b"{", b"[") else None


def read_capture(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_capture(path: Path, canaries: CanarySet, delete: bool = True) -> list[ObservedRequest]:
    """Reduce a raw capture file and delete it, so request bodies never outlive the run."""
    try:
        return [observe(r, canaries) for r in read_capture(path)]
    finally:
        if delete:
            path.unlink(missing_ok=True)
