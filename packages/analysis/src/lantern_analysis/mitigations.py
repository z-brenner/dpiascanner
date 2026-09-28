"""Detect vendor mitigation hooks (for example Sentry before_send) and what they scrub.

A hook is resolved from the SDK's init call, then read statically: removals such as
``user.pop("email")``, ``del event["user"]["email"]``, ``delete event.user.email``, or
overwrites with a literal, rooted at the event parameter. The result is a set of
(container, key) pairs that the hook scrubs, e.g. ("user", "email").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lantern_analysis.ir import (
    Assign,
    Attr,
    AttrT,
    Call,
    Container,
    Delete,
    Expr,
    ExprStmt,
    FuncRef,
    Function,
    Literal,
    Name,
    NameT,
    Subscript,
    SubscriptT,
    stmt_exprs,
    walk_expr,
)
from lantern_analysis.project import FuncSym, Project
from lantern_registry import Registry, RegistryEntry


@dataclass
class MitigationEvidence:
    registry_id: str
    hook: str
    kind: str
    file: str
    line: int
    text: str
    function: str | None = None
    scrubs: list[tuple[str, str]] = field(default_factory=list)
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_id": self.registry_id,
            "hook": self.hook,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "text": self.text,
            "function": self.function,
            "scrubs": [list(s) for s in self.scrubs],
            "value": self.value,
        }


def _hook_argument(call: Call, name: str) -> Expr | None:
    for key, value in call.kwargs:
        if key == name:
            return value
    for arg in call.args:
        if isinstance(arg, Container):
            for key2, value in arg.items:
                if key2 == name:
                    return value
    return None


def _event_path(
    expr: Expr, aliases: dict[str, tuple[str, ...]], event: str
) -> tuple[str, ...] | None:
    if isinstance(expr, Name):
        if expr.id == event:
            return ()
        return aliases.get(expr.id)
    if isinstance(expr, Attr):
        base = _event_path(expr.base, aliases, event)
        return None if base is None else (*base, expr.attr)
    if (
        isinstance(expr, Subscript)
        and isinstance(expr.key, Literal)
        and isinstance(expr.key.value, str)
    ):
        base = _event_path(expr.base, aliases, event)
        return None if base is None else (*base, expr.key.value)
    if (
        isinstance(expr, Call)
        and isinstance(expr.func, Attr)
        and expr.func.attr in ("get", "setdefault")
        and expr.args
        and isinstance(expr.args[0], Literal)
        and isinstance(expr.args[0].value, str)
    ):
        base = _event_path(expr.func.base, aliases, event)
        return None if base is None else (*base, expr.args[0].value)
    return None


def scrubbed_keys(fn: Function) -> list[tuple[str, str]]:
    params = [p for p in fn.params if p.kind != "self"]
    if not params:
        return []
    event = params[0].name
    aliases: dict[str, tuple[str, ...]] = {}
    scrubs: list[tuple[str, str]] = []

    def record(path: tuple[str, ...] | None) -> None:
        if path and len(path) >= 2:
            scrubs.append((".".join(path[:-1]), path[-1]))
        elif path and len(path) == 1:
            scrubs.append(("", path[0]))

    for stmt in fn.body:
        if isinstance(stmt, Assign):
            for tgt in stmt.targets:
                if isinstance(tgt, NameT):
                    path = _event_path(stmt.value, aliases, event)
                    if path is not None:
                        aliases[tgt.name] = path
                elif isinstance(tgt, SubscriptT | AttrT) and isinstance(stmt.value, Literal):
                    base = _event_path(tgt.base, aliases, event)
                    key = tgt.attr if isinstance(tgt, AttrT) else getattr(tgt.key, "value", None)
                    if base is not None and isinstance(key, str):
                        record((*base, key))
        elif isinstance(stmt, Delete):
            record(_event_path(stmt.target, aliases, event))
        if isinstance(stmt, ExprStmt | Assign):
            for root in stmt_exprs(stmt):
                for e in walk_expr(root):
                    if (
                        isinstance(e, Call)
                        and isinstance(e.func, Attr)
                        and e.func.attr in ("pop", "remove")
                        and e.args
                        and isinstance(e.args[0], Literal)
                        and isinstance(e.args[0].value, str)
                    ):
                        base = _event_path(e.func.base, aliases, event)
                        if base is not None:
                            record((*base, e.args[0].value))
    return sorted(set(scrubs))


def find_mitigations(project: Project, registry: Registry) -> list[MitigationEvidence]:
    found: list[MitigationEvidence] = []
    for fn in project.functions.values():
        language = project.language(fn)
        for stmt in fn.body:
            for root in stmt_exprs(stmt):
                for e in walk_expr(root):
                    if not isinstance(e, Call):
                        continue
                    target = project.resolve_call(fn, e)
                    if target.kind != "external" or target.spec is None:
                        continue
                    path = target.import_path(language) or target.spec
                    entry: RegistryEntry | None = registry.match_import(language, path)
                    if entry is None:
                        continue
                    for hook in entry.hooks_for(language):
                        if hook.config_call and target.method != hook.config_call:
                            continue
                        value = _hook_argument(e, hook.argument) if hook.argument else None
                        if value is None:
                            continue
                        evidence = MitigationEvidence(
                            registry_id=entry.id,
                            hook=hook.name,
                            kind=hook.kind,
                            file=e.span.file,
                            line=value.span.start_line,
                            text=f"{hook.argument}={value.text}"[:200],
                        )
                        if hook.kind == "event_scrubber":
                            hook_fn = _resolve_function(project, fn, value)
                            if hook_fn is not None:
                                evidence.function = hook_fn.fid
                                evidence.scrubs = scrubbed_keys(hook_fn)
                        elif isinstance(value, Literal):
                            evidence.value = value.value
                        found.append(evidence)
    return found


def _resolve_function(project: Project, fn: Function, expr: Expr) -> Function | None:
    if isinstance(expr, FuncRef):
        return project.functions.get(expr.fid)
    sym = project.expr_symbol(fn, expr)
    if isinstance(sym, FuncSym):
        return project.functions.get(sym.fid)
    return None
