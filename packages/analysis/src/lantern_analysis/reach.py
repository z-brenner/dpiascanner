"""Reachability from entry points over the static call graph.

Roots: module top-level code (it runs on import), ``__main__`` blocks, framework-registered
functions (routes, exception handlers, tasks, CLI commands, error middleware), and, for
libraries, exported functions. Edges: resolved calls, constructor calls, and functions used
as values (callbacks, ``Depends(f)``, ``before_send=f``). A method call on a value of
unknown type conservatively reaches every project method with that name.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field

from lantern_analysis.detect import Detection, EntryPoint
from lantern_analysis.ir import Attr, Call, FuncRef, Name, stmt_exprs, walk_expr
from lantern_analysis.project import FuncSym, Project


@dataclass
class Reachability:
    reachable: set[str] = field(default_factory=set)
    roots: dict[str, EntryPoint] = field(default_factory=dict)
    library_mode: bool = False

    def is_reachable(self, fid: str) -> bool:
        return fid in self.reachable


def _library_mode(project: Project, detection: Detection, setting: str) -> bool:
    if setting in ("true", "false"):
        return setting == "true"
    app_entries = [e for e in detection.entries.values() if e.kind not in ("module",)]
    if app_entries:
        return False
    package_json = project.root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text())
        except ValueError:
            data = {}
        return bool(data.get("main") or data.get("exports") or data.get("bin")) and not data.get(
            "private", False
        )
    return (project.root / "pyproject.toml").is_file() or (project.root / "setup.py").is_file()


def compute_reachability(
    project: Project, detection: Detection, library_mode: str = "auto"
) -> Reachability:
    result = Reachability(library_mode=_library_mode(project, detection, library_mode))
    roots = dict(detection.entries)
    if result.library_mode:
        for fn in project.functions.values():
            module = project.modules[fn.module]
            public = fn.kind == "function" and not fn.name.startswith("_") and fn.parent is None
            if public and (module.language == "python" or fn.exported):
                roots.setdefault(fn.fid, EntryPoint(fn.fid, "library"))
    result.roots = roots

    methods_by_name: dict[str, list[str]] = {}
    for fn in project.functions.values():
        if fn.kind == "method":
            methods_by_name.setdefault(fn.name, []).append(fn.fid)

    graph: dict[str, set[str]] = {}
    for fn in project.functions.values():
        edges: set[str] = set()
        call_funcs: set[int] = set()
        exprs = [e for stmt in fn.body for root in stmt_exprs(stmt) for e in walk_expr(root)]
        exprs += [e for d in fn.decorators for e in walk_expr(d)]
        exprs += [e for p in fn.params if p.default is not None for e in walk_expr(p.default)]
        for e in exprs:
            if isinstance(e, Call):
                call_funcs.add(id(e.func))
                target = project.resolve_call(fn, e)
                if target.fid:
                    edges.add(target.fid)
                if target.kind == "constructor" and target.cid:
                    init = project.constructor_of(target.cid)
                    if init:
                        edges.add(init)
                if target.kind == "unknown" and target.method:
                    edges.update(methods_by_name.get(target.method, []))
        for e in exprs:
            if isinstance(e, FuncRef):
                edges.add(e.fid)
            elif isinstance(e, Name | Attr) and id(e) not in call_funcs:
                sym = project.expr_symbol(fn, e)
                if isinstance(sym, FuncSym):
                    edges.add(sym.fid)
        # Nested functions and methods of classes defined here are reachable if referenced;
        # a class body's methods are reachable when the class is constructed (handled above).
        graph[fn.fid] = edges

    queue = deque(roots)
    seen: set[str] = set()
    while queue:
        fid = queue.popleft()
        if fid in seen or fid not in project.functions:
            continue
        seen.add(fid)
        queue.extend(graph.get(fid, ()))
    result.reachable = seen
    return result
