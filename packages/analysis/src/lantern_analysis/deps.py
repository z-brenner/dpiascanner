"""Profile a third-party package's own source for outbound network calls (one level deep).

Injected into ``lantern_registry.resolver.resolve_unregistered`` as the PackageAnalyzer.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from lantern_analysis.detect import Detector
from lantern_analysis.ir import Literal, function_calls, stmt_exprs, walk_expr
from lantern_analysis.lexicon import Lexicon
from lantern_analysis.project import Project
from lantern_analysis.repo import RepoSettings, discover
from lantern_analysis.rules import run_semgrep
from lantern_registry import Registry, load_registry
from lantern_registry.resolver import ProfileResult

URL = re.compile(r"^(?:https?|wss?)://([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?::\d+)?(?:/|$)")
NETWORK_FAMILIES = frozenset({"http", "registry", "queue"})


def profile_package(
    package_dir: Path, language: str, import_name: str, registry: Registry | None = None
) -> ProfileResult:
    settings = RepoSettings()
    files = {k: v for k, v in discover(package_dir, settings).items() if k == language}
    if not files:
        return ProfileResult()
    project = Project.load(package_dir, files)
    matches = run_semgrep(package_dir, files[language], language)
    detection = Detector(project, matches, Lexicon.load(), registry or load_registry()).run()
    sink_functions = {
        sink.fid
        for sink in detection.sinks.values()
        if sink.family in NETWORK_FAMILIES or sink.registry
    }
    callers: dict[str, set[str]] = {}
    for fn in project.functions.values():
        for call in function_calls(fn):
            target = project.resolve_call(fn, call)
            if target.fid:
                callers.setdefault(target.fid, set()).add(fn.fid)
            if target.kind == "constructor" and target.cid:
                init = project.constructor_of(target.cid)
                if init:
                    callers.setdefault(init, set()).add(fn.fid)
    reaching: set[str] = set()
    queue = deque(sink_functions)
    while queue:
        fid = queue.popleft()
        if fid in reaching:
            continue
        reaching.add(fid)
        queue.extend(callers.get(fid, ()))
    methods = sorted(
        {
            project.functions[fid].name
            for fid in reaching
            if project.functions[fid].kind in ("function", "method")
            and not project.functions[fid].name.startswith("_")
        }
    )
    hosts: set[str] = set()
    for fn in project.functions.values():
        for stmt in fn.body:
            for root in stmt_exprs(stmt):
                for e in walk_expr(root):
                    if isinstance(e, Literal) and isinstance(e.value, str):
                        m = URL.match(e.value.strip())
                        if m:
                            hosts.add(m.group(1).lower())
    locations = [
        {
            "file": sink.call.span.file,
            "line": sink.call.span.start_line,
            "rule_ids": sink.rule_ids,
            "function": project.functions[sink.fid].qualname,
        }
        for sink in detection.sinks.values()
        if sink.fid in sink_functions
    ]
    return ProfileResult(network_methods=methods, endpoints=sorted(hosts), sink_locations=locations)
