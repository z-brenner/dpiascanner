"""Interprocedural taint tracking over a value-flow graph (VFG).

VFG nodes are value locations:

    v|<fid>|<name>     a local variable or parameter
    g|<mid>|<name>     a module-level variable
    ca|<cid>|<attr>    a class attribute (heap, context-insensitive)
    r|<fid>            a function's return value
    c|<callsite>       a call's result
    t|<file>|<s>|<e>   a transform point (hash, encoding, aggregation, ...)
    s|<callsite>       a sink (every argument of the call flows in)
    x|<class key>      an exception channel; x|* receives every raised exception
    src|<sid>          a source; ps|<sid> a request container that yields sources on field reads

Each edge carries label-path operations (field sensitivity), an optional call or return
context (for call-string matching), and the evidence step that justified it. Taint labels
are (source, path, unresolved). The path says which field of the current value is personal:
() means the whole value. Reading ``x.email`` strips "email"; building ``{"email": v}``
prefixes it. A read with a key the analyzer cannot resolve (``d[k]``, ``getattr(o, name)``)
keeps the label but marks it unresolved, and so does everything downstream.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from lantern_analysis.detect import Detection, SinkSpec, SourceSpec, expr_key
from lantern_analysis.ir import (
    IDENTITY_OPS,
    OPAQUE_OPS,
    Assign,
    Attr,
    AttrT,
    Call,
    Compound,
    Container,
    ExceptBind,
    Expr,
    ExprStmt,
    ForLoop,
    FuncRef,
    Function,
    Literal,
    Name,
    NameT,
    ObjectT,
    Raise,
    Return,
    StarT,
    Subscript,
    SubscriptT,
    Target,
    TupleT,
)
from lantern_analysis.lexicon import Lexicon
from lantern_analysis.model import Step
from lantern_analysis.project import ClassSym, ClassT, Global, Local, Project

Op = tuple[str, ...]
Ref = tuple[str, tuple[Op, ...]]

MAX_PATH = 3
CONTEXT_K = 3
IDENTITY_METHODS = frozenset(
    {
        # ORM query chains keep the rows they select
        "all",
        "first",
        "one",
        "one_or_none",
        "scalar",
        "scalars",
        "scalar_one",
        "filter",
        "filter_by",
        "where",
        "order_by",
        "limit",
        "offset",
        "options",
        "join",
        "distinct",
        "execute",
        "fetchall",
        "fetchone",
        "unique",
        "select_related",
        "prefetch_related",
        # copies and views of the same object
        "copy",
        "model_dump",
        "dict",
        "to_dict",
        "as_dict",
        "_asdict",
        "model_copy",
        "values",
        "then",
        "finally",
        "clone",
        "toJSON",
        "valueOf",
    }
)
NON_PROPAGATING = frozenset(
    {
        "len",
        "bool",
        "isinstance",
        "issubclass",
        "hasattr",
        "callable",
        "id",
        "type",
        "hash",
        "startswith",
        "endswith",
        "isdigit",
        "isalpha",
        "isalnum",
        "includes",
        "has",
        "test",
        "some",
        "every",
        "isArray",
        "isInteger",
        "isNaN",
        "isFinite",
        "exists",
        "is_valid",
        "raise_for_status",
        "status_code",
        "keys",
        "commit",
        "flush",
        "close",
        "rollback",
        "refresh",
        "count_documents",
        "delete",
        "remove",
        "setHeader",
        "status",
        "sendStatus",
        "end",
        "next",
        "resolve",
        "reject",
        "log_message",
        "set_default",
    }
)
COLLECTION_BUILTINS = frozenset(
    {
        "dict",
        "list",
        "tuple",
        "set",
        "frozenset",
        "sorted",
        "reversed",
        "Array",
        "Object",
        "structuredClone",
        "Promise",
    }
)
GET_METHODS = frozenset({"get", "pop", "getlist", "setdefault", "getAll"})
# Library methods called for their effect on the receiver, a local or module variable: the
# arguments end up inside it. Element adders keep the argument's fields under "*"; merges keep
# them as they are; content setters (an email body, an attachment) taint the whole object.
# Deliberately a short list: a blanket "any method taints its receiver" rule would taint
# clients and sessions and every later result read from them (which is also why `insert`,
# a database collection method as often as a list one, is not here).
MUTATOR_METHODS: dict[str, Op | None] = {
    "append": ("prefix", "*"),
    "appendleft": ("prefix", "*"),
    "add": ("prefix", "*"),
    "push": ("prefix", "*"),
    "unshift": ("prefix", "*"),
    "extend": None,
    "update": None,
    "set_content": ("collapse",),
    "set_payload": ("collapse",),
    "add_alternative": ("collapse",),
    "add_attachment": ("collapse",),
    "attach": ("collapse",),
    "add_header": ("collapse",),
}


@dataclass(frozen=True)
class VEdge:
    dst: str
    ops: tuple[Op, ...]
    step: Step
    fid: str
    ctx: tuple[str, str] | None = None


@dataclass
class Hit:
    source: str
    sink: str
    edges: list[VEdge]
    path_at_sink: tuple[str, ...]
    unresolved: bool


@dataclass
class TaintStats:
    states: int = 0
    depth_truncated: int = 0
    state_limited: int = 0
    unresolved_steps: int = 0


@dataclass
class Materialized:
    source: SourceSpec
    injections: list[tuple[str, tuple[Op, ...], VEdge]] = field(default_factory=list)


def _is_heap(node: str) -> bool:
    return node.startswith(("g|", "ca|", "x|", "src|", "ps|"))


def apply_ops(path: tuple[str, ...], ops: Iterable[Op]) -> tuple[tuple[str, ...] | None, bool]:
    unresolved = False
    for op in ops:
        kind = op[0]
        if kind == "strip":
            if not path:
                continue
            if path[0] == op[1]:
                path = path[1:]
            elif path[0] == "?":
                path = path[1:]
                unresolved = True
            else:
                return None, unresolved
        elif kind == "strip_index":
            if path and (path[0] == "*" or path[0].isdigit()):
                path = path[1:]
        elif kind == "strip_any":
            unresolved = True
            if path:
                path = path[1:]
        elif kind == "prefix":
            path = (op[1], *path)[:MAX_PATH]
        elif kind == "collapse":
            path = ()
    return path, unresolved


class TaintEngine:
    def __init__(
        self,
        project: Project,
        detection: Detection,
        lexicon: Lexicon,
        depth_limit: int = 12,
        max_states_per_source: int = 250_000,
    ) -> None:
        self.project = project
        self.d = detection
        self.lexicon = lexicon
        self.depth_limit = depth_limit
        self.max_states = max_states_per_source
        self.adj: dict[str, list[VEdge]] = {}
        self.stats = TaintStats()
        self._memo: dict[int, list[Ref]] = {}
        self.overrides: dict[tuple[str, int, int], str] = {}
        self.exception_keys: set[str] = set()
        self.sources: dict[str, SourceSpec] = {}
        self.materialized: dict[str, Materialized] = {}

    # ------------------------------------------------------------------ graph building

    def add_edge(
        self,
        src: str,
        dst: str,
        ops: tuple[Op, ...],
        step: Step,
        fid: str,
        ctx: tuple[str, str] | None = None,
    ) -> None:
        unresolved = [op for op in ops if op[0] == "strip_any"]
        if unresolved and not step.unresolved:
            step = Step(step.file, step.line, step.kind, step.text, True, unresolved[0][1])
        self.adj.setdefault(src, []).append(VEdge(dst, ops, step, fid, ctx))

    def connect(
        self,
        refs: Iterable[Ref],
        dst: str,
        step: Step,
        fid: str,
        ctx: tuple[str, str] | None = None,
        extra: tuple[Op, ...] = (),
    ) -> None:
        for node, ops in refs:
            self.add_edge(node, dst, ops + extra, step, fid, ctx)

    def step(self, fn: Function, span_line: int, kind: str, text: str, note: str = "") -> Step:
        return Step(fn.span.file, span_line, kind, text, False, note)

    def build(self) -> None:
        for spec in self.d.sources:
            self.sources[spec.sid] = spec
            node = f"ps|{spec.sid}" if spec.pseudo else f"src|{spec.sid}"
            inject = spec.inject
            if inject[0] == "expr":
                self.overrides[(inject[1], int(inject[2]), int(inject[3]))] = node
            elif inject[0] == "param":
                fn = self.project.functions[inject[1]]
                ops = tuple(("prefix", f) for f in reversed(spec.label_prefix))
                self.add_edge(
                    node,
                    f"v|{inject[1]}|{inject[2]}",
                    ops,
                    self.step(fn, spec.span.start_line, "source", fn.span.file and _line_of(spec)),
                    fn.fid,
                )
            elif inject[0] == "call":
                fn = self.project.functions[spec.fid]
                ops = tuple(("prefix", f) for f in reversed(spec.label_prefix))
                self.add_edge(
                    node,
                    f"c|{inject[1]}",
                    ops,
                    self.step(fn, spec.span.start_line, "source", spec.symbol),
                    fn.fid,
                )
        for fn in self.project.functions.values():
            if fn.kind == "class-body":
                continue
            self._build_function(fn)
        self._link_exceptions()

    def _build_function(self, fn: Function) -> None:
        for p in fn.params:
            node = self.var_node(fn, p.name)
            if p.default is not None:
                refs = self.eval(fn, p.default)
                self.connect(
                    refs, node, self.step(fn, p.span.start_line, "default", p.default.text), fn.fid
                )
            if p.pattern is not None:
                self.assign(
                    fn,
                    p.pattern,
                    [(node, ())],
                    self.step(fn, p.span.start_line, "destructure", p.span.file),
                )
        for stmt in fn.body:
            line = stmt.span.start_line
            if isinstance(stmt, Assign):
                refs = self.eval(fn, stmt.value)
                step = self.step(fn, line, "assign", stmt.text)
                for tgt in stmt.targets:
                    self.assign(fn, tgt, refs, step)
            elif isinstance(stmt, ExprStmt):
                self.eval(fn, stmt.expr)
            elif isinstance(stmt, Return):
                if stmt.value is not None:
                    self.connect(
                        self.eval(fn, stmt.value),
                        f"r|{fn.fid}",
                        self.step(fn, line, "return", stmt.text),
                        fn.fid,
                    )
            elif isinstance(stmt, Raise):
                if stmt.exc is not None:
                    key = self._exception_key_of(fn, stmt.exc)
                    self.exception_keys.add(key)
                    self.connect(
                        self.eval(fn, stmt.exc),
                        f"x|{key}",
                        self.step(fn, line, "raise", stmt.text),
                        fn.fid,
                    )
            elif isinstance(stmt, ForLoop):
                refs = self.eval(fn, stmt.iter)
                self.assign(
                    fn,
                    stmt.target,
                    refs,
                    self.step(fn, line, "iterate", stmt.text),
                    (("strip_index",),),
                )
            elif isinstance(stmt, ExceptBind):
                keys = [self._handler_key(fn, t) for t in stmt.types] or ["*"]
                for key in keys:
                    self.add_edge(
                        f"x|{key}",
                        self.var_node(fn, stmt.name),
                        (),
                        self.step(fn, line, "except", stmt.text),
                        fn.fid,
                    )

    def var_node(self, fn: Function, name: str) -> str:
        if fn.kind == "module":
            return f"g|{fn.module}|{name}"
        return f"v|{fn.fid}|{name}"

    # ------------------------------------------------------------------ assignment

    def assign(
        self, fn: Function, target: Target, refs: list[Ref], step: Step, extra: tuple[Op, ...] = ()
    ) -> None:
        if isinstance(target, NameT):
            if target.name == "_":
                return
            self.connect(refs, self.var_node(fn, target.name), step, fn.fid, extra=extra)
        elif isinstance(target, AttrT):
            self._assign_attr(fn, target, refs, step, extra)
        elif isinstance(target, SubscriptT):
            key = target.key
            op: Op = (
                ("prefix", str(key.value))
                if isinstance(key, Literal) and key.value is not None
                else ("prefix", "?")
            )
            for node, _ in self.eval(fn, target.base):
                if node.startswith(("v|", "g|")):
                    self.connect(refs, node, step, fn.fid, extra=(*extra, op))
        elif isinstance(target, TupleT):
            for elt in target.elts:
                self.assign(fn, elt, refs, step, (*extra, ("strip_index",)))
        elif isinstance(target, StarT):
            self.assign(fn, target.inner, refs, step, extra)
        elif isinstance(target, ObjectT):
            for entry_key, sub, default, span in target.entries:
                read: Op = ("strip", entry_key, span.file, str(span.start_line))
                self.assign(
                    fn,
                    sub,
                    refs,
                    Step(step.file, span.start_line, "destructure", step.text),
                    (*extra, read),
                )
                if default is not None:
                    self.assign(fn, sub, self.eval(fn, default), step, extra)
            if target.rest is not None:
                self.assign(fn, target.rest, refs, step, extra)

    def _assign_attr(
        self, fn: Function, target: AttrT, refs: list[Ref], step: Step, extra: tuple[Op, ...]
    ) -> None:
        base = target.base
        if isinstance(base, Name) and base.id in ("self", "this") and fn.cls is not None:
            self.connect(
                refs,
                f"ca|{fn.cls}|{target.attr}",
                Step(step.file, step.line, "attr-set", step.text),
                fn.fid,
                extra=extra,
            )
            self.connect(
                refs,
                self.var_node(fn, base.id),
                step,
                fn.fid,
                extra=(*extra, ("prefix", target.attr)),
            )
            return
        t = self.project.infer_type(fn, base)
        if isinstance(t, ClassT):
            self.connect(
                refs,
                f"ca|{t.cid}|{target.attr}",
                Step(step.file, step.line, "attr-set", step.text),
                fn.fid,
                extra=extra,
            )
        for node, ops in self.eval(fn, base):
            if node.startswith(("v|", "g|")) and not ops:
                self.connect(refs, node, step, fn.fid, extra=(*extra, ("prefix", target.attr)))

    # ------------------------------------------------------------------ expressions

    def eval(self, fn: Function, expr: Expr) -> list[Ref]:
        memo = self._memo.get(id(expr))
        if memo is not None:
            return memo
        key = expr_key(expr.span)
        override = self.overrides.get(key)
        if override is not None:
            refs: list[Ref] = [(override, ())]
        else:
            refs = self._eval_raw(fn, expr)
        transform = self.d.transforms.get(key)
        if transform is not None:
            node = f"t|{key[0]}|{key[1]}|{key[2]}"
            self.connect(
                refs,
                node,
                self.step(fn, expr.span.start_line, "transform", expr.text),
                fn.fid,
                extra=(("collapse",),),
            )
            refs = [(node, ())]
        self._memo[id(expr)] = refs
        return refs

    def _eval_raw(self, fn: Function, expr: Expr) -> list[Ref]:
        if isinstance(expr, Name):
            if expr.id in ("self", "this") and fn.cls is not None:
                return [(self.var_node(fn, expr.id), ())]
            sym = self.project.lookup(fn, expr.id)
            if isinstance(sym, Local):
                owner = self.project.functions[sym.fid]
                return [(self.var_node(owner, sym.name), ())]
            if isinstance(sym, Global):
                return [(f"g|{sym.mid}|{sym.name}", ())]
            return []
        if isinstance(expr, Literal | FuncRef):
            return []
        if isinstance(expr, Attr):
            read: Op = ("strip", expr.attr, expr.span.file, str(expr.span.start_line))
            refs = [(node, (*ops, read)) for node, ops in self.eval(fn, expr.base)]
            base_type = self.project.infer_type(fn, expr.base)
            if isinstance(base_type, ClassT):
                for cid in self.project.mro(base_type.cid):
                    refs.append((f"ca|{cid}|{expr.attr}", ()))
            return refs
        if isinstance(expr, Subscript):
            key = expr.key
            op: Op
            if isinstance(key, Literal):
                if key.value == "slice":
                    return list(self.eval(fn, expr.base))
                if isinstance(key.value, str) and not key.value.lstrip("-").isdigit():
                    op = ("strip", key.value, expr.span.file, str(expr.span.start_line))
                else:
                    op = ("strip_index",)
            else:
                self.eval(fn, key)
                op = ("strip_any", f"subscript with non-literal key `{key.text}`")
                self.stats.unresolved_steps += 1
            return [(node, (*ops, op)) for node, ops in self.eval(fn, expr.base)]
        if isinstance(expr, Call):
            return self._eval_call(fn, expr)
        if isinstance(expr, Container):
            out: list[Ref] = []
            for k, v in expr.items:
                prefix: Op = ("prefix", k if k is not None else "*")
                out.extend((node, (*ops, prefix)) for node, ops in self.eval(fn, v))
            for s in expr.spreads:
                out.extend(self.eval(fn, s))
            return out
        if isinstance(expr, Compound):
            parts = [r for p in expr.parts for r in self.eval(fn, p)]
            if expr.op in OPAQUE_OPS:
                return []
            if expr.op in IDENTITY_OPS:
                return parts
            return [(node, (*ops, ("collapse",))) for node, ops in parts]
        return []

    # ------------------------------------------------------------------ calls

    def _eval_call(self, fn: Function, call: Call) -> list[Ref]:
        result = f"c|{call.cid}"
        line = call.span.start_line
        arg_refs = [self.eval(fn, a) for a in call.args]
        kw_refs = [(k, self.eval(fn, v)) for k, v in call.kwargs]
        splat_refs = [self.eval(fn, s) for s in call.splats]
        receiver = call.func.base if isinstance(call.func, Attr) else None
        recv_refs = self.eval(fn, receiver) if receiver is not None else []
        all_args = (
            [r for refs in arg_refs for r in refs]
            + [r for _, refs in kw_refs for r in refs]
            + [r for refs in splat_refs for r in refs]
        )

        target = self.project.resolve_call(fn, call)
        sink = self.d.sinks.get(call.cid)
        if sink is not None:
            step = self.step(fn, line, "sink-arg", call.text)
            payload = all_args + recv_refs if sink.receiver_payload else all_args
            self.connect(payload, f"s|{call.cid}", step, fn.fid)
            if sink.returns_input:
                # ORM create/update returns the written row: {data: {...}} -> row fields.
                unwrap: Op = ("strip", "data", call.span.file, str(line))
                self.connect(
                    all_args,
                    result,
                    self.step(fn, line, "call-result", call.text),
                    fn.fid,
                    extra=(unwrap,),
                )
            if target.kind not in ("function", "method"):
                # Library sink: its return value is a response or handle, not the data.
                return [(result, ())]
        call_step = self.step(fn, line, "call-arg", call.text, self._receiver_note(fn, receiver))
        ret_step = self.step(fn, line, "call-result", call.text)
        if target.kind in ("function", "method") and target.fid in self.project.functions:
            callee = self.project.functions[target.fid]
            self._bind_args(
                fn,
                callee,
                call,
                arg_refs,
                kw_refs,
                splat_refs,
                recv_refs if target.kind == "method" else [],
                call_step,
            )
            self.add_edge(f"r|{callee.fid}", result, (), ret_step, fn.fid, ("ret", call.cid))
            return [(result, ())]
        if target.kind == "constructor" and target.cid is not None:
            init = self.project.constructor_of(target.cid)
            if init is not None:
                callee = self.project.functions[init]
                self._bind_args(fn, callee, call, arg_refs, kw_refs, splat_refs, [], call_step)
                self_name = (
                    callee.params[0].name
                    if callee.params and callee.params[0].kind == "self"
                    else "self"
                )
                self.add_edge(
                    self.var_node(callee, self_name),
                    result,
                    (),
                    ret_step,
                    fn.fid,
                    ("ret", call.cid),
                )
            elif self.project.is_exception_class(target.cid):
                self.connect(all_args, result, call_step, fn.fid, extra=(("collapse",),))
            else:
                fields = self.project.field_order(target.cid)
                for i, refs in enumerate(arg_refs):
                    name = fields[i] if i < len(fields) else "*"
                    self.connect(refs, result, call_step, fn.fid, extra=(("prefix", name),))
                for k, refs in kw_refs:
                    self.connect(refs, result, call_step, fn.fid, extra=(("prefix", k),))
                for refs in splat_refs:
                    self.connect(refs, result, call_step, fn.fid)
            return [(result, ())]
        if target.kind == "super":
            owner = fn
            self_node = self.var_node(
                owner, "self" if self.project.language(fn) == "python" else "this"
            )
            self.connect(all_args, self_node, call_step, fn.fid, extra=(("collapse",),))
            return []

        method = target.method or ""
        # Library, builtin, unknown, and dynamic callees.
        if method in MUTATOR_METHODS and isinstance(receiver, Name) and all_args:
            mutate = MUTATOR_METHODS[method]
            step = self.step(fn, line, "mutate", call.text)
            for node, _ in recv_refs:
                if node.startswith(("v|", "g|")):
                    self.connect(all_args, node, step, fn.fid, extra=(mutate,) if mutate else ())
        if method in NON_PROPAGATING:
            return []
        if (
            method in GET_METHODS
            and call.args
            and isinstance(call.args[0], Literal)
            and isinstance(call.args[0].value, str)
        ):
            read: Op = ("strip", call.args[0].value, call.span.file, str(line))
            refs = [(n, (*ops, read)) for n, ops in recv_refs]
            refs += [r for refs2 in arg_refs[1:] for r in refs2]
            return refs
        if target.kind == "builtin" and method == "getattr" and len(call.args) >= 2:
            key = call.args[1]
            op: Op = (
                ("strip", str(key.value), call.span.file, str(line))
                if isinstance(key, Literal)
                else ("strip_any", f"getattr with non-literal name `{key.text}`")
            )
            return [(n, (*ops, op)) for n, ops in arg_refs[0]]
        if target.kind == "builtin" and method in ("eval", "exec", "compile"):
            op = ("strip_any", f"{method}() of dynamic code")
            self.connect(all_args, result, call_step, fn.fid, extra=(op,))
            return [(result, ())]
        if method in IDENTITY_METHODS and receiver is not None:
            self.connect(recv_refs, result, ret_step, fn.fid)
            self.connect(all_args, result, call_step, fn.fid, extra=(("collapse",),))
            return [(result, ())]
        if target.kind == "builtin" and method in COLLECTION_BUILTINS:
            self.connect(all_args, result, call_step, fn.fid)
            return [(result, ())]
        if target.kind == "dynamic":
            op = (
                "strip_any",
                f"call through {target.note or 'a dynamic callee'} `{call.func.text}`",
            )
            self.connect(all_args + recv_refs, result, call_step, fn.fid, extra=(op,))
            return [(result, ())]
        self.connect(all_args + recv_refs, result, call_step, fn.fid, extra=(("collapse",),))
        return [(result, ())]

    def _bind_args(
        self,
        fn: Function,
        callee: Function,
        call: Call,
        arg_refs: list[list[Ref]],
        kw_refs: list[tuple[str, list[Ref]]],
        splat_refs: list[list[Ref]],
        recv_refs: list[Ref],
        step: Step,
    ) -> None:
        ctx = ("call", call.cid)
        params = callee.params
        self_param = params[0] if params and params[0].kind == "self" else None
        positional = [p for p in params if p.kind in ("normal",)]
        vararg = next((p for p in params if p.kind == "vararg"), None)
        kwarg = next((p for p in params if p.kind == "kwarg"), None)
        if self_param is not None and recv_refs:
            self.connect(recv_refs, self.var_node(callee, self_param.name), step, fn.fid, ctx)
        for i, refs in enumerate(arg_refs):
            if i < len(positional):
                self.connect(refs, self.var_node(callee, positional[i].name), step, fn.fid, ctx)
            elif vararg is not None:
                self.connect(
                    refs,
                    self.var_node(callee, vararg.name),
                    step,
                    fn.fid,
                    ctx,
                    extra=(("prefix", "*"),),
                )
        names = {p.name for p in params}
        for k, refs in kw_refs:
            if k in names:
                self.connect(refs, self.var_node(callee, k), step, fn.fid, ctx)
            elif kwarg is not None:
                self.connect(
                    refs,
                    self.var_node(callee, kwarg.name),
                    step,
                    fn.fid,
                    ctx,
                    extra=(("prefix", k),),
                )
        for refs in splat_refs:
            for p in positional:
                self.connect(
                    refs,
                    self.var_node(callee, p.name),
                    step,
                    fn.fid,
                    ctx,
                    extra=(("strip_index",),),
                )

    def _receiver_note(self, fn: Function, receiver: Expr | None) -> str:
        if receiver is None:
            return ""
        root = receiver
        while isinstance(root, Attr):
            root = root.base
        if isinstance(root, Name):
            note = self.d.di_notes.get(f"{fn.fid}|{root.id}")
            if note:
                return f"receiver `{root.id}` {note}"
            sym = self.project.lookup(fn, root.id)
            for value in self.project.values_of(sym):
                inner = value
                while isinstance(inner, Attr):
                    inner = inner.base
                if isinstance(inner, Name):
                    gsym = self.project.lookup(fn, inner.id)
                    if isinstance(gsym, Global) and gsym.mid != fn.module:
                        module = self.project.modules[gsym.mid]
                        for assigned in self.project.values_of(gsym):
                            return (
                                f"receiver `{root.id}` resolved through `{value.text}` "
                                f"defined at {module.file}:{assigned.span.start_line}"
                            )
        return ""

    # ------------------------------------------------------------------ exceptions

    def _exception_key_of(self, fn: Function, exc: Expr) -> str:
        if isinstance(exc, Call):
            target = self.project.resolve_call(fn, exc)
            if target.kind == "constructor" and target.cid:
                return target.cid
            if target.spec:
                return f"ext:{(target.qual or '').split('.')[-1]}"
        t = self.project.infer_type(fn, exc)
        if isinstance(t, ClassT):
            return t.cid
        return "*"

    def _handler_key(self, fn: Function, expr: Expr) -> str:
        sym = self.project.expr_symbol(fn, expr)
        if isinstance(sym, ClassSym):
            return sym.cid
        name = expr.text.split(".")[-1]
        return "*" if name in ("Exception", "BaseException", "Error") else f"ext:{name}"

    def _link_exceptions(self) -> None:
        for key in list(self.exception_keys):
            if key != "*":
                self.add_edge(f"x|{key}", "x|*", (), Step("", 0, "exception", "propagates"), "")
        for entry in self.d.entries.values():
            if entry.kind not in ("exception_handler", "error_middleware"):
                continue
            handler = self.project.functions[entry.fid]
            params = [p for p in handler.params if p.kind != "self"]
            if not params:
                continue
            if entry.kind == "error_middleware":
                param = params[0]
            else:
                named = [p for p in params if p.name in ("exc", "error", "err", "e", "exception")]
                param = named[0] if named else params[-1]
            node = self.var_node(handler, param.name)
            step = Step(
                handler.span.file,
                handler.span.start_line,
                "exception-handler",
                f"{handler.name}({param.name})",
            )
            for catch in entry.catches or ["*"]:
                if catch == "*":
                    self.add_edge("x|*", node, (), step, handler.fid)
                    continue
                for raised in self.exception_keys:
                    if raised == catch or (
                        raised in self.project.classes and catch in self.project.mro(raised)
                    ):
                        self.add_edge(f"x|{raised}", node, (), step, handler.fid)

    # ------------------------------------------------------------------ propagation

    def materialize(self) -> None:
        """Phase 1: follow request containers until a field is read; create sources there."""
        from lantern_analysis.detect import SourceSpec as Spec

        for spec in [s for s in self.d.sources if s.pseudo]:
            start = f"ps|{spec.sid}"
            queue: deque[tuple[str, tuple[str, ...], tuple[str, ...]]] = deque([(start, (), ())])
            seen: set[tuple[str, tuple[str, ...], tuple[str, ...]]] = set()
            while queue:
                node, path, stack = queue.popleft()
                for edge in self.adj.get(node, []):
                    new_path: tuple[str, ...] | None = path
                    event: tuple[str, str, int, tuple[Op, ...]] | None = None
                    for i, op in enumerate(edge.ops):
                        if op[0] == "strip" and new_path == ():
                            event = (op[1], op[2], int(op[3]), edge.ops[i + 1 :])
                            break
                        new_path, _ = apply_ops(new_path or (), [op])
                        if new_path is None:
                            break
                    if event is not None:
                        self._materialize_field(spec, edge, *event)
                        continue
                    if new_path is None or any(
                        op[0] in ("collapse", "strip_any") for op in edge.ops
                    ):
                        continue
                    new_stack = stack
                    if edge.ctx is not None:
                        kind, cs = edge.ctx
                        if kind == "call":
                            if len(stack) >= self.depth_limit:
                                continue
                            new_stack = (*stack, cs)
                        elif stack:
                            if stack[-1] != cs:
                                continue
                            new_stack = stack[:-1]
                    state = (edge.dst, new_path, new_stack[-CONTEXT_K:])
                    if state in seen or len(seen) > self.max_states:
                        continue
                    seen.add(state)
                    queue.append((edge.dst, new_path, new_stack))
        for mat in self.materialized.values():
            spec = mat.source
            self.sources[spec.sid] = spec
            for dst, ops, via in mat.injections:
                step = Step(
                    via.step.file,
                    spec.span.start_line,
                    "source",
                    via.step.text,
                    False,
                    via.step.note,
                )
                self.add_edge(f"src|{spec.sid}", dst, ops, step, via.fid, via.ctx)
        _ = Spec

    def _materialize_field(
        self,
        container: SourceSpec,
        edge: VEdge,
        name: str,
        file: str,
        line: int,
        rest: tuple[Op, ...],
    ) -> None:
        fn = self.project.functions.get(edge.fid) or self.project.functions[container.fid]
        from lantern_analysis.detect import Detector  # local import avoids a cycle at module load

        context = [*container.context, fn.name]
        hints = self.lexicon.match(name, context)
        if not hints:
            return
        del Detector
        sid = f"{file}:{line}:{name}"
        mat = self.materialized.get(sid)
        if mat is None:
            from lantern_analysis.model import Span

            spec = SourceSpec(
                sid=sid,
                kind=container.kind if container.kind != "http_body" else "http_body_field",
                field=name,
                span=Span(file, line, line),
                fid=fn.fid,
                symbol=f"{container.symbol}.{name}",
                hints=hints,
                rule_ids=list(container.rule_ids),
                inject=("materialized",),
                context=context,
            )
            mat = Materialized(spec)
            self.materialized[sid] = mat
        mat.injections.append((edge.dst, rest, edge))

    def propagate(self, sid: str) -> list[Hit]:
        start = f"src|{sid}"
        queue: deque[tuple[str, tuple[str, ...], bool, tuple[str, ...]]] = deque(
            [(start, (), False, ())]
        )
        parent: dict[
            tuple[str, tuple[str, ...], bool, tuple[str, ...]],
            tuple[tuple[str, tuple[str, ...], bool, tuple[str, ...]] | None, VEdge | None],
        ] = {}
        root_key = (start, (), False, ())
        parent[root_key] = (None, None)
        full_stack: dict[tuple[str, tuple[str, ...], bool, tuple[str, ...]], tuple[str, ...]] = {
            root_key: ()
        }
        hits: dict[tuple[str, bool], Hit] = {}
        while queue:
            node, path, unresolved, stack = queue.popleft()
            key = (node, path, unresolved, stack[-CONTEXT_K:])
            for edge in self.adj.get(node, []):
                new_path, added = apply_ops(path, edge.ops)
                if new_path is None:
                    continue
                new_stack = stack
                if edge.ctx is not None:
                    kind, cs = edge.ctx
                    if kind == "call":
                        if len(stack) >= self.depth_limit:
                            self.stats.depth_truncated += 1
                            continue
                        new_stack = (*stack, cs)
                    elif stack:
                        if stack[-1] != cs:
                            continue
                        new_stack = stack[:-1]
                if _is_heap(edge.dst):
                    new_stack = ()
                new_unresolved = unresolved or added
                state = (edge.dst, new_path, new_unresolved, new_stack[-CONTEXT_K:])
                if state in parent:
                    continue
                if len(parent) > self.max_states:
                    self.stats.state_limited += 1
                    break
                parent[state] = (key, edge)
                full_stack[state] = new_stack
                self.stats.states += 1
                if edge.dst.startswith("s|"):
                    hk = (edge.dst, new_unresolved)
                    if hk not in hits:
                        hits[hk] = Hit(
                            sid, edge.dst[2:], self._chain(parent, state), new_path, new_unresolved
                        )
                    continue
                queue.append((edge.dst, new_path, new_unresolved, new_stack))
        # Prefer a resolved path to a sink when one exists.
        out: dict[str, Hit] = {}
        for (sink, _unresolved), hit in sorted(hits.items(), key=lambda kv: kv[0][1]):
            out.setdefault(sink, hit)
        return list(out.values())

    @staticmethod
    def _chain(
        parent: dict[
            tuple[str, tuple[str, ...], bool, tuple[str, ...]],
            tuple[tuple[str, tuple[str, ...], bool, tuple[str, ...]] | None, VEdge | None],
        ],
        state: tuple[str, tuple[str, ...], bool, tuple[str, ...]],
    ) -> list[VEdge]:
        edges: list[VEdge] = []
        current: tuple[str, tuple[str, ...], bool, tuple[str, ...]] | None = state
        while current is not None:
            prev, edge = parent[current]
            if edge is not None:
                edges.append(edge)
            current = prev
        edges.reverse()
        return edges

    def run(self) -> dict[str, list[Hit]]:
        self.build()
        self.materialize()
        results: dict[str, list[Hit]] = {}
        for sid, spec in self.sources.items():
            if spec.pseudo:
                continue
            results[sid] = self.propagate(sid)
        return results


def _line_of(spec: SourceSpec) -> str:
    return spec.symbol


__all__ = ["Hit", "TaintEngine", "TaintStats", "VEdge", "apply_ops"]

_ = SinkSpec
