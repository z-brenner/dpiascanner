"""Synthetic canary values and their detection in outbound traffic.

The sandbox feeds these values into the application and looks for them in every recorded
request. Detection covers what SDKs actually do to a value on its way out: case changes,
URL encoding (``+`` and ``@`` in an email become ``%2B`` and ``%40``), base64 (mixpanel-node
sends its payload as base64 in a query parameter), compression (Sentry envelopes may be
gzipped), JSON unicode escapes, digit-only phone numbers, and the unsalted hashes that ad and
analytics platforms use for "hashed" identifiers (MD5, SHA-1, SHA-256 of the normalized
value). A hash of a canary is still a hit: an unsalted hash of an email address or phone
number is pseudonymised, not anonymised, data (GDPR Art. 4(5), Recital 26), and the
platforms that receive it match it against their own records, which is why it is sent.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import gzip
import hashlib
import json
import re
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any
from urllib.parse import quote, quote_plus, unquote_plus

import yaml

MAX_SCAN_BYTES = 1 << 20
MAX_LAYERS = 3
_B64_TOKEN = re.compile(rb"[A-Za-z0-9+/_-]{16,}={0,2}")
_UNICODE_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")
_HEX_ENCODINGS = frozenset({"md5", "sha1", "sha256"})


@dataclass(frozen=True, order=True)
class CanaryHit:
    kind: str  # the canary name: email, phone, ssn, ...
    encoding: str  # how it appeared: plain, url, base64>plain, gzip>sha256, ...
    location: str  # body, query, path, header:<name>

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "encoding": self.encoding, "location": self.location}


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _normalized_forms(kind: str, value: str) -> list[str]:
    """Forms that platforms hash. Meta and Google both specify trim + lowercase for email and
    E.164 digits for phone before SHA-256."""
    forms = {value, value.strip().lower()}
    if kind.startswith("phone"):
        digits = _digits(value)
        forms.update({digits, "+" + digits})
    return sorted(forms)


@dataclass(frozen=True)
class _Needle:
    kind: str
    encoding: str
    text: str
    case_insensitive: bool


class CanarySet:
    """A named set of canary values and the needles derived from them."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self.values = {k: str(v) for k, v in values.items()}
        self._needles = self._build_needles()

    @classmethod
    def default(cls) -> CanarySet:
        return _default()

    def env(self) -> dict[str, str]:
        """LANTERN_CANARY_<NAME> variables, for run scripts that read canaries from the env."""
        return {f"LANTERN_CANARY_{k.upper()}": v for k, v in self.values.items()}

    def _build_needles(self) -> list[_Needle]:
        needles: dict[tuple[str, str, bool], _Needle] = {}

        def add(kind: str, encoding: str, text: str, ci: bool = False) -> None:
            if len(text) < 6:
                return
            key = (kind, text.lower() if ci else text, ci)
            needles.setdefault(key, _Needle(kind, encoding, text, ci))

        for kind, value in self.values.items():
            add(kind, "plain", value)
            add(kind, "case-insensitive", value, True)
            add(kind, "url", quote(value, safe=""))
            add(kind, "url", quote_plus(value))
            b64 = base64.b64encode(value.encode()).decode()
            add(kind, "base64", b64.rstrip("="))
            add(kind, "base64", base64.urlsafe_b64encode(value.encode()).decode().rstrip("="))
            if kind.startswith("phone"):
                digits = _digits(value)
                add(kind, "digits", digits)
                add(kind, "digits", digits[1:] if digits.startswith("1") else digits)
            for form in _normalized_forms(kind, value):
                data = form.encode()
                add(kind, "md5", hashlib.md5(data, usedforsecurity=False).hexdigest(), True)
                add(kind, "sha1", hashlib.sha1(data, usedforsecurity=False).hexdigest(), True)
                add(kind, "sha256", hashlib.sha256(data).hexdigest(), True)
                sha_b64 = base64.b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
                add(kind, "sha256-base64", sha_b64)
        return list(needles.values())

    def scan_text(self, text: str, location: str, prefix: str = "") -> set[CanaryHit]:
        hits: set[CanaryHit] = set()
        lowered = text.lower()
        for needle in self._needles:
            haystack = lowered if needle.case_insensitive else text
            probe = needle.text.lower() if needle.case_insensitive else needle.text
            if probe in haystack:
                hits.add(CanaryHit(needle.kind, prefix + needle.encoding, location))
        return _drop_implied(hits)

    def scan(self, data: bytes | str, location: str) -> set[CanaryHit]:
        """Scan raw bytes and every decoded layer beneath them."""
        raw = data.encode("utf-8", "replace") if isinstance(data, str) else data
        hits: set[CanaryHit] = set()
        for prefix, layer in _layers(raw[:MAX_SCAN_BYTES]):
            text = layer.decode("utf-8", "replace")
            hits |= self.scan_text(text, location, prefix)
        return _drop_implied(hits)


def _drop_implied(hits: set[CanaryHit]) -> set[CanaryHit]:
    """A plain hit implies the case-insensitive and digit variants at the same location."""
    plain = {(h.kind, h.location) for h in hits if h.encoding.rsplit(">", 1)[-1] == "plain"}
    return {
        h
        for h in hits
        if h.encoding.rsplit(">", 1)[-1] not in ("case-insensitive", "digits")
        or (h.kind, h.location) not in plain
    }


def _inflate(data: bytes) -> tuple[str, bytes] | None:
    try:
        if data[:2] == b"\x1f\x8b":
            return "gzip>", gzip.decompress(data)[:MAX_SCAN_BYTES]
        if data[:1] == b"\x78":
            return "deflate>", zlib.decompress(data)[:MAX_SCAN_BYTES]
    except (OSError, EOFError, zlib.error):
        return None
    return None


def _decode_layer(data: bytes) -> Iterator[tuple[str, bytes]]:
    inflated = _inflate(data)
    if inflated is not None:
        yield inflated
    text = data.decode("utf-8", "replace")
    if "%" in text or "+" in text:
        unquoted = unquote_plus(text)
        if unquoted != text:
            yield "url>", unquoted.encode()
    if "\\u" in text:
        unescaped = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text)
        yield "json>", unescaped.encode()
    for match in _B64_TOKEN.finditer(data):
        token = match.group(0)
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(token + b"=" * (-len(token) % 4))
            except (binascii.Error, ValueError):
                continue
            if _printable(decoded) or decoded[:2] == b"\x1f\x8b":
                yield "base64>", decoded
                break


def _printable(data: bytes) -> bool:
    if len(data) < 8:
        return False
    sample = data[:512]
    ok = sum(32 <= b < 127 or b in (9, 10, 13) for b in sample)
    return ok / len(sample) > 0.9


def _layers(data: bytes) -> Iterator[tuple[str, bytes]]:
    seen: set[bytes] = set()
    frontier: list[tuple[str, bytes]] = [("", data)]
    for _ in range(MAX_LAYERS):
        next_frontier: list[tuple[str, bytes]] = []
        for prefix, layer in frontier:
            digest = hashlib.sha256(layer).digest()
            if digest in seen:
                continue
            seen.add(digest)
            yield prefix, layer
            next_frontier.extend((prefix + p, d) for p, d in _decode_layer(layer))
        frontier = next_frontier
        if not frontier:
            break


def _key_paths(value: Any, prefix: str, depth: int, out: set[str], limit: int) -> None:
    if len(out) >= limit or depth > 5:
        return
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.add(path)
            _key_paths(child, path, depth + 1, out, limit)
    elif isinstance(value, list):
        for child in value[:20]:
            _key_paths(child, f"{prefix}[]", depth + 1, out, limit)


def body_field_names(body: bytes, content_type: str, limit: int = 200) -> list[str]:
    """Dotted key paths in a JSON, NDJSON, form, or multipart body. Values are not kept.

    A form value that is itself JSON (or base64 JSON) contributes ``<field>:<key path>``.
    """
    names: set[str] = set()
    ctype = content_type.lower()
    text = body.decode("utf-8", "replace")
    stripped = text.strip()
    parsed_any = False
    for chunk in [stripped] if "\n" not in stripped else [stripped, *stripped.splitlines()]:
        chunk = chunk.strip()
        if not chunk or chunk[0] not in "{[":
            continue
        try:
            _key_paths(json.loads(chunk), "", 0, names, limit)
            parsed_any = True
        except ValueError:
            continue
        if chunk == stripped:
            break
    is_form = "x-www-form-urlencoded" in ctype or re.fullmatch(r"[^\s=&]+=[^\s]*", stripped)
    if not parsed_any and is_form:
        for pair in stripped.split("&"):
            key, _, value = (unquote_plus(p) for p in pair.partition("="))
            if not key:
                continue
            names.add(key)
            nested = _json_value(value)
            if nested is not None:
                inner: set[str] = set()
                _key_paths(nested, "", 1, inner, limit)
                names.update(f"{key}:{n}" for n in inner)
    if "multipart/form-data" in ctype:
        names.update(re.findall(r'name="([^"]{1,100})"', text))
    return sorted(names)[:limit]


def _json_value(value: str) -> Any:
    """A form value that is itself JSON, or base64 of JSON (Mixpanel's ``data=``)."""
    candidates = [value]
    if len(value) >= 8 and re.fullmatch(r"[A-Za-z0-9+/_=-]+", value):
        with contextlib.suppress(binascii.Error, ValueError, UnicodeDecodeError):
            candidates.append(base64.b64decode(value + "=" * (-len(value) % 4)).decode())
    for candidate in candidates:
        if candidate.lstrip()[:1] in ("{", "["):
            try:
                return json.loads(candidate)
            except ValueError:
                continue
    return None


@cache
def _default() -> CanarySet:
    raw = (resources.files("lantern_worker.dynamic") / "canaries.yaml").read_text("utf-8")
    return CanarySet({str(k): str(v) for k, v in yaml.safe_load(raw).items()})
