"""Exercise a running application's routes with synthetic requests carrying canary values.

Standard library only: in the Docker sandbox this file is mounted into a helper container
that shares the application's network namespace and runs it as a script. Route discovery
prefers the application's own OpenAPI document (FastAPI, most NestJS and Spring apps serve
one), which gives complete paths and request schemas. Without one, it falls back to the
routes static analysis found, with the request fields its sources read. Router prefixes
that static analysis could not resolve are guessed from the route's file name
(``routes/users.py`` -> ``/users``); that guess is a heuristic and is reported as such.

    python routes.py plan.json > results.json      (or: ... routes.py - < plan.json)
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

OPENAPI_PATHS = ("/openapi.json", "/swagger.json", "/api-docs", "/v3/api-docs", "/docs/json")
MAX_ROUTES = 200

_FIELD_KINDS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"e[-_]?mail")),
    ("phone", re.compile(r"phone|mobile|msisdn|\btel\b|telephone|cell")),
    ("ssn", re.compile(r"ssn|social[-_]?security|national[-_]?id|tax[-_]?id|\btin\b")),
    ("dob", re.compile(r"dob|birth|birthday")),
    ("lat", re.compile(r"^lat$|latitude")),
    ("lng", re.compile(r"^(lng|lon|long)$|longitude")),
    ("condition", re.compile(r"condition|diagnos|symptom|medical|health")),
    ("payment_method", re.compile(r"payment[-_]?method|card[-_]?token|(^|_)pm[-_]?id")),
    (
        "name",
        re.compile(
            r"^((full|first|last|given|family|middle|legal|display|customer|contact|patient)"
            r"[-_]?)?name$|^surname$"
        ),
    ),
]
_FORMAT_KINDS = {"email": "email", "idn-email": "email", "date": "dob", "phone": "phone"}


def kind_for_field(name: str, fmt: str = "") -> str | None:
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()
    for kind, pattern in _FIELD_KINDS:
        if pattern.search(snake):
            return kind
    return _FORMAT_KINDS.get(fmt.lower())


@dataclass
class RouteRequest:
    method: str
    path: str
    body_kind: str  # json | form | none
    body: dict[str, Any]
    query: dict[str, str]
    origin: str  # openapi | static
    fallback_paths: list[str] = field(default_factory=list)  # tried in order after a 404


@dataclass
class RouteResult:
    method: str
    path: str
    origin: str
    status: int
    elapsed_ms: int
    error: str = ""


@dataclass
class DriveReport:
    discovery: str
    routes: list[RouteResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"discovery": self.discovery, "routes": [asdict(r) for r in self.routes]}


class ValueFactory:
    """Canary values for fields, and plausible filler for everything else."""

    def __init__(self, canaries: Mapping[str, str]) -> None:
        self.canaries = dict(canaries)

    def for_field(self, name: str, schema: Mapping[str, Any] | None = None) -> Any:
        schema = schema or {}
        kind = kind_for_field(name, str(schema.get("format", "")))
        if kind and kind in self.canaries:
            value: Any = self.canaries[kind]
            if kind in ("lat", "lng") and schema.get("type") != "string":
                return float(value)
            return value
        return self.filler(schema, name)

    def filler(self, schema: Mapping[str, Any], name: str = "") -> Any:
        if schema.get("enum"):
            return schema["enum"][0]
        if "default" in schema:
            return schema["default"]
        kind = schema.get("type")
        if kind == "integer" or (not kind and name.lower().endswith(("_id", "id"))):
            return 1
        if kind == "number":
            return 1.0
        if kind == "boolean":
            return True
        if kind == "array":
            return [self.from_schema(schema.get("items", {}), name)]
        if kind == "object":
            return self.from_schema(schema, name)
        if schema.get("format") == "date-time":
            return "2024-01-01T00:00:00Z"
        if schema.get("format") == "uuid":
            return "00000000-0000-4000-8000-000000000001"
        return "lantern"

    def from_schema(self, schema: Mapping[str, Any], name: str = "") -> Any:
        if schema.get("type") == "object" or "properties" in schema:
            props = schema.get("properties", {})
            return {k: self.for_field(k, v) for k, v in props.items()}
        return self.for_field(name, schema)


def _resolve(spec: Mapping[str, Any], schema: Any, depth: int = 0) -> Any:
    """Inline $ref, allOf, anyOf, and oneOf, to a bounded depth."""
    if depth > 8 or not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        target: Any = spec
        for part in str(schema["$ref"]).lstrip("#/").split("/"):
            target = target.get(part, {}) if isinstance(target, dict) else {}
        return _resolve(spec, target, depth + 1)
    out = dict(schema)
    for key in ("anyOf", "oneOf"):
        if key in out:
            options = [o for o in out.pop(key) if isinstance(o, dict) and o.get("type") != "null"]
            if options:
                out.update(_resolve(spec, options[0], depth + 1))
    if "allOf" in out:
        for part in out.pop("allOf"):
            resolved = _resolve(spec, part, depth + 1)
            props = {**out.get("properties", {}), **resolved.get("properties", {})}
            out.update({k: v for k, v in resolved.items() if k != "properties"})
            out["properties"] = props
    if "properties" in out:
        out["properties"] = {k: _resolve(spec, v, depth + 1) for k, v in out["properties"].items()}
    if "items" in out:
        out["items"] = _resolve(spec, out["items"], depth + 1)
    return out


def _fill_path(path: str, params: Mapping[str, Any]) -> str:
    def sub(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        return urllib.parse.quote(str(params.get(name, 1)), safe="")

    return re.sub(r"\{([^}]+)\}|:([A-Za-z_][A-Za-z0-9_]*)", sub, path)


def requests_from_openapi(spec: Mapping[str, Any], values: ValueFactory) -> list[RouteRequest]:
    out: list[RouteRequest] = []
    for path, item in sorted((spec.get("paths") or {}).items()):
        if not isinstance(item, dict):
            continue
        for method, op in sorted(item.items()):
            if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            if not isinstance(op, dict):
                continue
            params = [
                _resolve(spec, p) for p in [*item.get("parameters", []), *op.get("parameters", [])]
            ]
            path_values: dict[str, Any] = {}
            query: dict[str, str] = {}
            for p in params:
                schema = _resolve(spec, p.get("schema", {}))
                value = values.for_field(str(p.get("name", "")), schema)
                if p.get("in") == "path":
                    path_values[p["name"]] = value
                elif p.get("in") == "query":
                    query[p["name"]] = str(value)
            body_kind, body = "none", {}
            content = ((op.get("requestBody") or {}).get("content")) or {}
            for ctype, kind in (
                ("application/json", "json"),
                ("application/x-www-form-urlencoded", "form"),
                ("multipart/form-data", "form"),
            ):
                if ctype in content:
                    schema = _resolve(spec, content[ctype].get("schema", {}))
                    filled = values.from_schema(schema)
                    body_kind, body = kind, filled if isinstance(filled, dict) else {}
                    break
            out.append(
                RouteRequest(
                    method.upper(), _fill_path(path, path_values), body_kind, body, query, "openapi"
                )
            )
    return out[:MAX_ROUTES]


def requests_from_static(
    routes: list[Mapping[str, Any]], values: ValueFactory
) -> list[RouteRequest]:
    out: list[RouteRequest] = []
    for route in routes:
        method = str(route.get("method") or "GET").upper()
        path = str(route.get("path") or "")
        fields = route.get("fields", [])
        body: dict[str, Any] = {}
        query: dict[str, str] = {}
        form = False
        for f in fields:
            name, where = str(f.get("name", "")), str(f.get("location", "body"))
            value = values.for_field(name)
            if where == "query":
                query[name] = str(value)
            elif where in ("body", "form"):
                body[name] = value
                form = form or where == "form"
        kind = "none" if not body else ("form" if form else "json")
        path = path if path.startswith("/") else "/" + path
        stem = PurePosixPath(str(route.get("file", ""))).stem
        fallbacks = []
        if (
            stem
            and stem not in ("main", "app", "index", "server", "routes", "__init__")
            and not path.startswith(f"/{stem}")
        ):
            fallbacks.append(_fill_path(f"/{stem}{path}".rstrip("/") or "/", {}))
        out.append(
            RouteRequest(method, _fill_path(path, {}), kind, body, query, "static", fallbacks)
        )
    return out[:MAX_ROUTES]


def _send(base_url: str, req: RouteRequest, timeout: float) -> RouteResult:
    result = _send_one(base_url, req, req.path, req.origin, timeout)
    for path in req.fallback_paths:
        if result.status not in (404, 405):
            break
        result = _send_one(base_url, req, path, "static+prefix-guess", timeout)
    return result


def _send_one(
    base_url: str, req: RouteRequest, path: str, origin: str, timeout: float
) -> RouteResult:
    url = base_url.rstrip("/") + path
    if req.query:
        url += "?" + urllib.parse.urlencode(req.query)
    data: bytes | None = None
    headers = {"User-Agent": "lantern-route-driver/1", "Accept": "application/json"}
    if req.body_kind == "json":
        data = json.dumps(req.body).encode()
        headers["Content-Type"] = "application/json"
    elif req.body_kind == "form":
        data = urllib.parse.urlencode({k: str(v) for k, v in req.body.items()}).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=data, method=req.method, headers=headers)  # noqa: S310
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            status, error = int(resp.status), ""
            resp.read()
    except urllib.error.HTTPError as exc:
        status, error = int(exc.code), ""
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        status, error = 0, type(exc).__name__
    elapsed = int((time.monotonic() - started) * 1000)
    return RouteResult(req.method, path, origin, status, elapsed, error)


def wait_for(base_url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=2):  # noqa: S310
                return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    return False


def fetch_openapi(base_url: str) -> dict[str, Any] | None:
    for path in OPENAPI_PATHS:
        try:
            with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=5) as resp:  # noqa: S310
                data = json.loads(resp.read())
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("paths"), dict):
            return data
    return None


def drive(plan: Mapping[str, Any]) -> DriveReport:
    base_url = str(plan["base_url"])
    values = ValueFactory(plan.get("canaries", {}))
    if not wait_for(base_url, float(plan.get("startup_timeout", 30))):
        return DriveReport(discovery="server-did-not-start")
    spec = fetch_openapi(base_url)
    if spec is not None:
        requests, discovery = requests_from_openapi(spec, values), "openapi"
    else:
        requests, discovery = requests_from_static(plan.get("static_routes", []), values), "static"
    report = DriveReport(discovery=discovery)
    per_request = float(plan.get("request_timeout", 10))
    deadline = time.monotonic() + float(plan.get("budget_s", 120))
    # Twice through: the second pass reaches branches that need state from the first
    # (a duplicate signup, an export of a user created a moment ago).
    for _ in range(int(plan.get("passes", 2))):
        for req in requests:
            if time.monotonic() > deadline:
                return report
            report.routes.append(_send(base_url, req, per_request))
    return report


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else "-"
    if source == "-":
        plan = json.loads(sys.stdin.read())
    else:
        with open(source, encoding="utf-8") as fh:
            plan = json.load(fh)
    print(json.dumps(drive(plan).to_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
