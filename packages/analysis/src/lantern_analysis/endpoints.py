"""Expected network destinations for sinks, from static evidence.

Sources of evidence, in order: the registry entry's documented endpoints, URL literals in the
call's arguments (following names to module-level constants), configuration values attached
to the sink, code defaults of environment reads that the sink's arguments refer to
(``os.environ.get("WEATHER_API_URL", "https://...")``, ``process.env.X ?? "https://..."``),
and hosts found in a profiled dependency's source. Dynamic verification matches observed
requests against these.
"""

from __future__ import annotations

import re
from typing import Any

from lantern_analysis.configres import normalize_key, sink_identifiers
from lantern_analysis.detect import SinkSpec
from lantern_analysis.ir import Attr, Call, Compound, Literal, Name, stmt_exprs, walk_expr
from lantern_analysis.project import Global, Project

URL = re.compile(
    r"^(?:https?|wss?)://(?:[^@/\s]+@)?([A-Za-z0-9.-]+\.[A-Za-z]{2,}|localhost)(?::\d+)?(?:[/?#]|$)"
)


def host_of(value: str) -> str | None:
    m = URL.match(value.strip())
    return m.group(1).lower() if m else None


def _env_defaults(project: Project) -> dict[str, list[str]]:
    """normalized env var name -> default URL literals found in code."""
    out: dict[str, list[str]] = {}
    for fn in project.functions.values():
        for stmt in fn.body:
            for root in stmt_exprs(stmt):
                for e in walk_expr(root):
                    key: str | None = None
                    default: str | None = None
                    if isinstance(e, Call) and len(e.args) >= 2:
                        head = e.func.text
                        if head.endswith(("environ.get", "getenv")) and isinstance(
                            e.args[0], Literal
                        ):
                            key = str(e.args[0].value)
                            if isinstance(e.args[1], Literal) and isinstance(e.args[1].value, str):
                                default = e.args[1].value
                    elif isinstance(e, Compound) and e.op == "boolop" and len(e.parts) == 2:
                        left, right = e.parts
                        if (
                            isinstance(left, Attr)
                            and left.base.text in ("process.env", "import.meta.env")
                            and isinstance(right, Literal)
                            and isinstance(right.value, str)
                        ):
                            key, default = left.attr, right.value
                    if key and default and host_of(default):
                        out.setdefault(normalize_key(key), []).append(default)
    return out


class EndpointResolver:
    def __init__(self, project: Project) -> None:
        self.project = project
        self.env_defaults = _env_defaults(project)

    def _literal_urls(self, sink: SinkSpec) -> list[str]:
        fn = self.project.functions[sink.fid]
        urls: list[str] = []
        for arg in [*sink.call.args, *(v for _, v in sink.call.kwargs)]:
            for e in walk_expr(arg):
                if isinstance(e, Literal) and isinstance(e.value, str) and host_of(e.value):
                    urls.append(e.value)
                elif isinstance(e, Name):
                    sym = self.project.lookup(fn, e.id)
                    if isinstance(sym, Global):
                        for value in self.project.values_of(sym):
                            if (
                                isinstance(value, Literal)
                                and isinstance(value.value, str)
                                and host_of(value.value)
                            ):
                                urls.append(value.value)
        return urls

    def resolve(self, sink: SinkSpec, config: list[dict[str, Any]]) -> dict[str, Any]:
        hosts: dict[str, str] = {}
        if sink.registry is not None:
            for endpoint in sink.registry.endpoints:
                hosts.setdefault(endpoint.host.lower(), "registry")
        for url in self._literal_urls(sink):
            host = host_of(url)
            if host:
                hosts.setdefault(host, "literal")
        for item in config:
            host = host_of(str(item.get("value", "")))
            if host:
                hosts.setdefault(host, f"config:{item.get('file')}")
        for ident in sink_identifiers(self.project, sink):
            for url in self.env_defaults.get(ident, []):
                host = host_of(url)
                if host:
                    hosts.setdefault(host, "env-default")
        if sink.dependency is not None:
            for host in sink.dependency.endpoints:
                hosts.setdefault(host.lower(), "dependency-source")
        return {"hosts": sorted(hosts), "evidence": dict(sorted(hosts.items()))}
