"""A small, language-independent intermediate representation for taint tracking.

Parsers lower tree-sitter syntax trees for Python and TypeScript into this IR. It keeps
only what data-flow needs: names, attribute and subscript access, calls with arguments,
literals, containers, and statements that move values (assignment, return, raise, loops,
destructuring). Control flow is flattened: analysis within a function is flow-insensitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lantern_analysis.model import Span

# ----------------------------------------------------------------------------- expressions


@dataclass(eq=False)
class Expr:
    span: Span
    text: str


@dataclass(eq=False)
class Name(Expr):
    id: str


@dataclass(eq=False)
class Attr(Expr):
    base: Expr
    attr: str


@dataclass(eq=False)
class Subscript(Expr):
    base: Expr
    key: Expr


@dataclass(eq=False)
class Call(Expr):
    func: Expr
    args: list[Expr]
    kwargs: list[tuple[str, Expr]]
    splats: list[Expr]
    is_new: bool
    cid: str  # stable call-site id: "<file>:<start_byte>-<end_byte>"


@dataclass(eq=False)
class Literal(Expr):
    value: Any


@dataclass(eq=False)
class Container(Expr):
    kind: str  # dict | list | tuple | set | object | array
    items: list[tuple[str | None, Expr]]
    spreads: list[Expr]


# Compound operators that pass their operands through unchanged (the value is one of them).
IDENTITY_OPS = frozenset({"await", "paren", "as", "nonnull", "ternary", "boolop", "spread"})
# Operators whose result carries no information about the operands' personal data.
OPAQUE_OPS = frozenset({"compare", "not", "typeof", "instanceof", "in"})


@dataclass(eq=False)
class Compound(Expr):
    op: str
    parts: list[Expr]


@dataclass(eq=False)
class FuncRef(Expr):
    fid: str


# ----------------------------------------------------------------------------- targets


@dataclass(eq=False)
class Target:
    span: Span


@dataclass(eq=False)
class NameT(Target):
    name: str


@dataclass(eq=False)
class AttrT(Target):
    base: Expr
    attr: str


@dataclass(eq=False)
class SubscriptT(Target):
    base: Expr
    key: Expr


@dataclass(eq=False)
class TupleT(Target):
    elts: list[Target]


@dataclass(eq=False)
class ObjectT(Target):
    """TypeScript object destructuring: ``const { a, b: c = 1, ...rest } = value``."""

    entries: list[tuple[str, Target, Expr | None, Span]]
    rest: Target | None


@dataclass(eq=False)
class StarT(Target):
    inner: Target


# ----------------------------------------------------------------------------- statements


@dataclass(eq=False)
class Stmt:
    span: Span
    text: str


@dataclass(eq=False)
class Assign(Stmt):
    targets: list[Target]
    value: Expr
    annotation: str | None = None


@dataclass(eq=False)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(eq=False)
class Return(Stmt):
    value: Expr | None


@dataclass(eq=False)
class Raise(Stmt):
    exc: Expr | None


@dataclass(eq=False)
class ForLoop(Stmt):
    target: Target
    iter: Expr


@dataclass(eq=False)
class Delete(Stmt):
    target: Expr


@dataclass(eq=False)
class ExceptBind(Stmt):
    """``except E as e`` (Python) or ``catch (e)`` (TypeScript)."""

    types: list[Expr]
    name: str


# ----------------------------------------------------------------------------- definitions


@dataclass(eq=False)
class Param:
    name: str
    span: Span
    annotation: str | None = None
    default: Expr | None = None
    kind: str = "normal"  # normal | vararg | kwarg | self
    is_property: bool = False  # TypeScript constructor parameter property
    pattern: Target | None = None  # TypeScript destructured parameter


@dataclass(eq=False)
class Function:
    fid: str
    name: str
    module: str
    span: Span
    params: list[Param]
    body: list[Stmt] = field(default_factory=list)
    decorators: list[Expr] = field(default_factory=list)
    cls: str | None = None
    parent: str | None = None
    kind: str = "function"  # function | method | lambda | module | main
    is_async: bool = False
    exported: bool = False
    returns: str | None = None

    @property
    def qualname(self) -> str:
        return self.fid.split(":", 1)[1] if ":" in self.fid else self.fid


@dataclass(eq=False)
class ClassField:
    name: str
    annotation: str | None
    default: Expr | None
    span: Span


@dataclass(eq=False)
class Class:
    cid: str
    name: str
    module: str
    span: Span
    bases: list[Expr] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)
    fields: dict[str, ClassField] = field(default_factory=dict)
    decorators: list[Expr] = field(default_factory=list)
    exported: bool = False


@dataclass(eq=False)
class Import:
    local: str
    module: str  # as written: "app.db", "./db.js", "@sentry/node"
    name: str | None  # imported symbol; None for a whole-module or namespace import
    span: Span
    type_only: bool = False
    level: int = 0  # Python relative import level


@dataclass(eq=False)
class Module:
    mid: str
    file: str
    language: str
    source: bytes
    imports: dict[str, Import] = field(default_factory=dict)
    functions: dict[str, Function] = field(default_factory=dict)
    classes: dict[str, Class] = field(default_factory=dict)
    module_fn: str = ""
    main_fn: str | None = None
    exports: dict[str, str] = field(default_factory=dict)  # exported name -> local name
    default_export: str | None = None
    parse_errors: int = 0

    def line_text(self, line: int) -> str:
        lines = self.source.decode("utf-8", "replace").splitlines()
        return lines[line - 1] if 0 < line <= len(lines) else ""


# ----------------------------------------------------------------------------- walking


def sub_exprs(expr: Expr) -> list[Expr]:
    """Direct child expressions, for generic traversal."""
    if isinstance(expr, Attr):
        return [expr.base]
    if isinstance(expr, Subscript):
        return [expr.base, expr.key]
    if isinstance(expr, Call):
        return [expr.func, *expr.args, *(v for _, v in expr.kwargs), *expr.splats]
    if isinstance(expr, Container):
        return [v for _, v in expr.items] + list(expr.spreads)
    if isinstance(expr, Compound):
        return list(expr.parts)
    return []


def walk_expr(expr: Expr) -> list[Expr]:
    out = [expr]
    stack = [expr]
    while stack:
        current = stack.pop()
        for child in sub_exprs(current):
            out.append(child)
            stack.append(child)
    return out


def target_exprs(target: Target) -> list[Expr]:
    if isinstance(target, AttrT):
        return [target.base]
    if isinstance(target, SubscriptT):
        return [target.base, target.key]
    if isinstance(target, TupleT):
        return [e for t in target.elts for e in target_exprs(t)]
    if isinstance(target, ObjectT):
        out = [e for _, t, d, _ in target.entries for e in [*target_exprs(t), *([d] if d else [])]]
        return out + (target_exprs(target.rest) if target.rest else [])
    if isinstance(target, StarT):
        return target_exprs(target.inner)
    return []


def stmt_exprs(stmt: Stmt) -> list[Expr]:
    if isinstance(stmt, Assign):
        return [stmt.value, *(e for t in stmt.targets for e in target_exprs(t))]
    if isinstance(stmt, ExprStmt):
        return [stmt.expr]
    if isinstance(stmt, Return | Raise):
        value = stmt.value if isinstance(stmt, Return) else stmt.exc
        return [value] if value is not None else []
    if isinstance(stmt, ForLoop):
        return [stmt.iter, *target_exprs(stmt.target)]
    if isinstance(stmt, Delete):
        return [stmt.target]
    if isinstance(stmt, ExceptBind):
        return list(stmt.types)
    return []


def function_calls(fn: Function) -> list[Call]:
    calls: list[Call] = []
    for stmt in fn.body:
        for root in stmt_exprs(stmt):
            calls.extend(e for e in walk_expr(root) if isinstance(e, Call))
    for deco in fn.decorators:
        calls.extend(e for e in walk_expr(deco) if isinstance(e, Call))
    return calls


def target_names(target: Target) -> list[str]:
    if isinstance(target, NameT):
        return [target.name]
    if isinstance(target, TupleT):
        return [n for t in target.elts for n in target_names(t)]
    if isinstance(target, ObjectT):
        out = [n for _, t, _, _ in target.entries for n in target_names(t)]
        return out + (target_names(target.rest) if target.rest else [])
    if isinstance(target, StarT):
        return target_names(target.inner)
    return []


def dotted(expr: Expr) -> str | None:
    """``a.b.c`` for Name/Attr chains, else None."""
    parts: list[str] = []
    current: Expr = expr
    while isinstance(current, Attr):
        parts.append(current.attr)
        current = current.base
    if isinstance(current, Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None
