"""Turn rule matches and IR structure into typed sources, sinks, transforms, and entry points."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

from lantern_analysis.ir import (
    Attr,
    Call,
    Container,
    Expr,
    FuncRef,
    Function,
    Literal,
    Name,
    stmt_exprs,
    target_exprs,
    walk_expr,
)
from lantern_analysis.lexicon import Lexicon, LexiconHint, tokenize
from lantern_analysis.model import Span
from lantern_analysis.project import ClassT, ExtT, Global, Project
from lantern_analysis.rules import RuleMatch
from lantern_registry import Registry, RegistryEntry
from lantern_registry.resolver import DependencyProfile

HTTP_METHODS = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "route",
        "api_route",
        "websocket",
        "all",
    }
)
# Constructors whose ``prefix`` or ``url_prefix`` keyword prefixes every route on the object.
ROUTER_FACTORIES = frozenset({"APIRouter", "Blueprint"})
PY_HANDLER_DECORATORS = frozenset(
    {
        "exception_handler",
        "errorhandler",
        "middleware",
        "on_event",
        "before_request",
        "after_request",
        "task",
        "command",
        "group",
        "callback",
        "listener",
        "subscriber",
        "receiver",
        "scheduled_job",
        "cron",
        "job",
        "consumer",
        "handler",
        "shared_task",
        "periodic_task",
        "add_api_route",
        "event",
        "on",
        "hookimpl",
    }
)
NON_SOURCE_ANNOTATIONS = frozenset(
    {
        "Request",
        "Response",
        "Session",
        "AsyncSession",
        "BackgroundTasks",
        "WebSocket",
        "HTTPConnection",
        "SecurityScopes",
        "HTTPAuthorizationCredentials",
        "Depends",
    }
)
SCALAR_PARAM_DEFAULTS = {
    "Form": "form",
    "Query": "http_param",
    "Path": "http_param",
    "Header": "header_cookie",
    "Cookie": "header_cookie",
    "Body": "http_param",
    "File": "upload",
}
ORM_BASE_NAMES = frozenset(
    {"Base", "DeclarativeBase", "Model", "db.Model", "models.Model", "SQLModel", "AbstractBaseUser"}
)


def expr_key(span: Span) -> tuple[str, int, int]:
    return (span.file, span.start_byte, span.end_byte)


@dataclass
class SourceSpec:
    sid: str
    kind: str
    field: str | None
    span: Span
    fid: str
    symbol: str
    hints: list[LexiconHint]
    rule_ids: list[str]
    inject: tuple[str, ...]  # ("param", fid, name) | ("expr", file, start, end) | ("call", cid)
    label_prefix: tuple[str, ...] = ()
    model: str | None = None
    pseudo: bool = False
    context: list[str] = dc_field(default_factory=list)


@dataclass
class SinkSpec:
    cid: str
    call: Call
    fid: str
    rule_ids: list[str]
    family: str
    persists: bool = False
    returns_input: bool = False
    registry: RegistryEntry | None = None
    method: str | None = None
    event_path: str | None = None
    model: str | None = None
    external: str | None = None
    dependency: DependencyProfile | None = None


@dataclass
class TransformSpec:
    key: tuple[str, int, int]
    kind: str
    rule_id: str
    span: Span
    fid: str
    text: str


@dataclass
class EntryPoint:
    fid: str
    # route | handler | exception_handler | error_middleware | main | job | module | library
    kind: str
    method: str | None = None
    path: str | None = None
    detail: str = ""
    catches: list[str] = dc_field(default_factory=list)  # exception class keys for handlers


@dataclass
class Detection:
    sources: list[SourceSpec] = dc_field(default_factory=list)
    sinks: dict[str, SinkSpec] = dc_field(default_factory=dict)
    transforms: dict[tuple[str, int, int], TransformSpec] = dc_field(default_factory=dict)
    entries: dict[str, EntryPoint] = dc_field(default_factory=dict)
    expr_index: dict[tuple[str, int, int], tuple[str, Expr]] = dc_field(default_factory=dict)
    orm_models: dict[str, list[str]] = dc_field(default_factory=dict)  # model name -> columns
    unmatched_rules: list[RuleMatch] = dc_field(default_factory=list)
    di_notes: dict[str, str] = dc_field(default_factory=dict)  # param "fid|name" -> provider note


class Detector:
    def __init__(
        self,
        project: Project,
        matches: list[RuleMatch],
        lexicon: Lexicon,
        registry: Registry,
        dependencies: list[DependencyProfile] | None = None,
    ) -> None:
        self.project = project
        self.matches = matches
        self.lexicon = lexicon
        self.registry = registry
        self.dependencies = [d for d in dependencies or [] if d.status == "profiled"]
        self.d = Detection()

    # ------------------------------------------------------------------ driver

    def run(self) -> Detection:
        self._index_expressions()
        self._orm_models()
        self._entrypoints()
        self._route_param_sources()
        for match in self.matches:
            self._apply_match(match)
        self._registry_sinks()
        return self.d

    # ------------------------------------------------------------------ indexing

    def _index_expressions(self) -> None:
        for fn in self.project.functions.values():
            roots: list[Expr] = []
            for stmt in fn.body:
                roots.extend(stmt_exprs(stmt))
            roots.extend(fn.decorators)
            roots.extend(p.default for p in fn.params if p.default is not None)
            for p in fn.params:
                if p.pattern is not None:
                    roots.extend(target_exprs(p.pattern))
            for root in roots:
                for e in walk_expr(root):
                    self.d.expr_index.setdefault(expr_key(e.span), (fn.fid, e))

    def locate(self, span: Span) -> tuple[str, Expr] | None:
        exact = self.d.expr_index.get(expr_key(span))
        if exact is not None:
            return exact
        best: tuple[int, tuple[str, Expr]] | None = None
        for (file, start, end), item in self.d.expr_index.items():
            if file != span.file or start > span.start_byte or end < span.end_byte:
                continue
            size = end - start
            if best is None or size < best[0]:
                best = (size, item)
        return best[1] if best else None

    def function_at(self, span: Span) -> Function | None:
        best: Function | None = None
        for fn in self.project.functions.values():
            if fn.span.file != span.file or fn.kind in ("class-body",):
                continue
            inside = fn.span.start_byte <= span.start_byte and span.end_byte <= fn.span.end_byte
            size = fn.span.end_byte - fn.span.start_byte
            if inside and (best is None or size < best.span.end_byte - best.span.start_byte):
                best = fn
        return best

    # ------------------------------------------------------------------ ORM models

    def _orm_models(self) -> None:
        for cls in self.project.classes.values():
            bases = {b.text for b in cls.bases}
            is_model = "__tablename__" in cls.fields or bool(bases & ORM_BASE_NAMES)
            if not is_model:
                for c in self.project.mro(cls.cid)[1:]:
                    if "__tablename__" in self.project.classes[c].fields:
                        is_model = True
            if is_model:
                cols = [f for f in self.project.field_order(cls.cid) if not f.startswith("_")]
                self.d.orm_models[cls.name] = cols
        for schema in sorted(self.project.root.rglob("*.prisma")):
            if "node_modules" in schema.parts:
                continue
            self.d.orm_models.update(parse_prisma_schema(schema.read_text()))

    def model_columns(self, model: str) -> list[str]:
        if model in self.d.orm_models:
            return self.d.orm_models[model]
        upper = model[:1].upper() + model[1:]
        return self.d.orm_models.get(upper, [])

    # ------------------------------------------------------------------ entry points

    def _router_mounts(self) -> dict[Global, tuple[str, Global | None]]:
        """Router or blueprint -> (prefix it is mounted under, the router it is mounted on).

        Covers ``app.include_router(users.router, prefix="/users")`` and Flask's
        ``app.register_blueprint(bp, url_prefix="/x")``, including nested routers.
        """
        mounts: dict[Global, tuple[str, Global | None]] = {}
        for fn in self.project.functions.values():
            for stmt in fn.body:
                for root in stmt_exprs(stmt):
                    for e in walk_expr(root):
                        if not (isinstance(e, Call) and isinstance(e.func, Attr) and e.args):
                            continue
                        if e.func.attr not in ("include_router", "register_blueprint"):
                            continue
                        child = self.project.expr_symbol(fn, e.args[0])
                        parent = self.project.expr_symbol(fn, e.func.base)
                        if isinstance(child, Global):
                            prefix = _kwarg_string(e, "prefix", "url_prefix") or ""
                            mounts.setdefault(
                                child, (prefix, parent if isinstance(parent, Global) else None)
                            )
        return mounts

    def _own_prefix(self, sym: Global) -> tuple[str, str]:
        """(prefix, factory) for a router or blueprint object."""
        for value in self.project.values_of(sym):
            factory = value.func.text.split(".")[-1] if isinstance(value, Call) else ""
            if isinstance(value, Call) and factory in ROUTER_FACTORIES:
                return _kwarg_string(value, "prefix", "url_prefix") or "", factory
        return "", ""

    def _route_prefix(self, fn: Function, router: Expr) -> str:
        start = self.project.expr_symbol(fn, router)
        sym: Global | None = start if isinstance(start, Global) else None
        prefix = ""
        seen: set[Global] = set()
        while sym is not None and sym not in seen:
            seen.add(sym)
            mount, parent = self._mounts.get(sym, ("", None))
            own, factory = self._own_prefix(sym)
            # FastAPI concatenates include_router's prefix with the router's own; Flask's
            # register_blueprint(url_prefix=...) replaces the blueprint's url_prefix.
            segment = mount if factory == "Blueprint" and mount else mount + own
            prefix = segment + prefix
            sym = parent
        return prefix

    def _entrypoints(self) -> None:
        project = self.project
        self._mounts = self._router_mounts()
        for fn in project.functions.values():
            if fn.kind == "module":
                self.d.entries.setdefault(fn.fid, EntryPoint(fn.fid, "module"))
            elif fn.kind == "main":
                self.d.entries[fn.fid] = EntryPoint(fn.fid, "main")
            for deco in fn.decorators:
                call = deco if isinstance(deco, Call) else None
                func = call.func if call is not None else deco
                name = (
                    func.attr
                    if isinstance(func, Attr)
                    else func.id
                    if isinstance(func, Name)
                    else ""
                )
                if name in HTTP_METHODS and isinstance(func, Attr):
                    path = _first_string(call.args) if call is not None else None
                    method = name.upper()
                    if project.modules[fn.module].language == "python":
                        path = (self._route_prefix(fn, func.base) + (path or "")) or path
                        if name in ("route", "api_route"):
                            method = _methods_kwarg(call) or "GET"
                    self.d.entries[fn.fid] = EntryPoint(fn.fid, "route", method, path)
                elif name in ("exception_handler", "errorhandler"):
                    catches = []
                    if call is not None and call.args:
                        owner = (
                            self.project.functions.get(fn.parent or "")
                            or self.project.functions[self.project.modules[fn.module].module_fn]
                        )
                        catches = [self.exception_key(owner, call.args[0])]
                    self.d.entries[fn.fid] = EntryPoint(
                        fn.fid, "exception_handler", detail=deco.text, catches=catches
                    )
                elif name in PY_HANDLER_DECORATORS:
                    self.d.entries[fn.fid] = EntryPoint(fn.fid, "handler", detail=deco.text)
            # TypeScript/Express style registrations: router.post("/x", handler)
            for stmt in fn.body:
                for root in stmt_exprs(stmt):
                    for e in walk_expr(root):
                        if not isinstance(e, Call) or not isinstance(e.func, Attr):
                            continue
                        method = e.func.attr
                        handlers = [a for a in e.args if isinstance(a, FuncRef)]
                        if method in HTTP_METHODS and handlers:
                            path = _first_string(e.args)
                            for h in handlers:
                                self.d.entries[h.fid] = EntryPoint(
                                    h.fid, "route", method.upper(), path
                                )
                        elif method == "use":
                            for h in handlers:
                                target = project.functions[h.fid]
                                if len([p for p in target.params if p.kind != "self"]) == 4:
                                    self.d.entries[h.fid] = EntryPoint(
                                        h.fid, "error_middleware", catches=["*"]
                                    )
                                else:
                                    self.d.entries.setdefault(h.fid, EntryPoint(h.fid, "handler"))
        # Next.js route handlers: exported GET/POST/... in route.ts files.
        for module in project.modules.values():
            if module.language == "typescript" and Path(module.file).stem == "route":
                for exported in module.exports:
                    if exported.lower() in HTTP_METHODS:
                        fid = f"{module.mid}:{exported}"
                        if fid in project.functions:
                            self.d.entries[fid] = EntryPoint(fid, "route", exported, module.mid)

    def exception_key(self, fn: Function, expr: Expr) -> str:
        sym = self.project.expr_symbol(fn, expr)
        if type(sym).__name__ == "ClassSym":
            return sym.cid  # type: ignore[union-attr]
        text = expr.text.split(".")[-1]
        return "*" if text in ("Exception", "BaseException", "Error") else f"ext:{text}"

    # ------------------------------------------------------------------ sources

    def context_for(self, fn: Function, extra: Iterable[str] = ()) -> list[str]:
        ctx = [fn.name, Path(fn.span.file).stem, *extra]
        entry = self.d.entries.get(fn.fid)
        if entry is not None and entry.path:
            ctx.append(entry.path)
        parent = self.project.functions.get(fn.parent or "")
        if parent is not None and parent.kind != "module":
            ctx.append(parent.name)
        if fn.cls is not None:
            ctx.append(self.project.classes[fn.cls].name)
        return [c for c in ctx if c and not c.startswith("<")]

    def _route_param_sources(self) -> None:
        for entry in list(self.d.entries.values()):
            if entry.kind != "route":
                continue
            fn = self.project.functions[entry.fid]
            if self.project.language(fn) != "python":
                continue
            for param in fn.params:
                if param.kind == "self":
                    continue
                default_fn = _callee_name(param.default)
                if default_fn == "Depends":
                    self._note_dependency(fn, param.name, param.default)
                    continue
                annotation = param.annotation or ""
                core = annotation.split("[")[0].split(".")[-1].strip()
                if core in NON_SOURCE_ANNOTATIONS:
                    continue
                t = self.project.resolve_annotation(fn, param.annotation)
                if isinstance(t, ExtT):
                    if core == "UploadFile":
                        self._add_param_source(fn, param.name, None, "upload", param.span, [], ())
                    continue
                if isinstance(t, ClassT):
                    self._model_param_sources(fn, param.name, param.span, t.cid, (), 0)
                    continue
                kind = SCALAR_PARAM_DEFAULTS.get(default_fn or "", "http_param")
                hints = self.lexicon.match(param.name, self.context_for(fn))
                if hints or kind == "upload":
                    self._add_param_source(fn, param.name, param.name, kind, param.span, hints, ())

    def _note_dependency(self, fn: Function, name: str, default: Expr | None) -> None:
        if not isinstance(default, Call) or not default.args:
            return
        provider = self.project.expr_symbol(fn, default.args[0])
        fid = getattr(provider, "fid", None)
        if fid and fid in self.project.functions:
            pf = self.project.functions[fid]
            self.d.di_notes[f"{fn.fid}|{name}"] = (
                f"injected by Depends({pf.name}) at {pf.span.file}:{pf.span.start_line}"
            )

    def _model_param_sources(
        self, fn: Function, param: str, span: Span, cid: str, prefix: tuple[str, ...], depth: int
    ) -> None:
        cls = self.project.classes[cid]
        for fname in self.project.field_order(cid):
            field_def = None
            for c in self.project.mro(cid):
                field_def = self.project.classes[c].fields.get(fname) or field_def
            nested = (
                self.project.resolve_annotation(
                    self.project.functions[self.project.modules[cls.module].module_fn],
                    field_def.annotation,
                )
                if field_def is not None
                else None
            )
            if isinstance(nested, ClassT) and depth < 1:
                self._model_param_sources(fn, param, span, nested.cid, (*prefix, fname), depth + 1)
                continue
            hints = self.lexicon.match(fname, self.context_for(fn, [cls.name]))
            if hints:
                self._add_param_source(
                    fn, param, fname, "http_body_field", span, hints, (*prefix, fname), cls.name
                )

    def _add_param_source(
        self,
        fn: Function,
        param: str,
        field_name: str | None,
        kind: str,
        span: Span,
        hints: list[LexiconHint],
        label_prefix: tuple[str, ...],
        model: str | None = None,
    ) -> None:
        dotted = ".".join([param, *label_prefix]) if label_prefix else param
        self.d.sources.append(
            SourceSpec(
                sid=f"{span.file}:{span.start_line}:{fn.qualname}:{dotted}",
                kind=kind,
                field=field_name,
                span=span,
                fid=fn.fid,
                symbol=f"{fn.qualname}:{dotted}",
                hints=hints,
                rule_ids=[f"python.source.http.fastapi-{kind.replace('_', '-')}"],
                inject=("param", fn.fid, param),
                label_prefix=label_prefix,
                model=model,
                context=self.context_for(fn),
            )
        )

    # ------------------------------------------------------------------ matches

    def _apply_match(self, match: RuleMatch) -> None:
        located = self.locate(match.span)
        if located is None:
            self.d.unmatched_rules.append(match)
            return
        fid, expr = located
        fn = self.project.functions[fid]
        if match.kind == "source":
            self._source_match(match, fn, expr)
        elif match.kind == "sink":
            self._sink_match(match, fn, expr)
        elif match.kind == "transform":
            key = expr_key(expr.span)
            if key not in self.d.transforms:
                self.d.transforms[key] = TransformSpec(
                    key, match.family, match.rule_id, expr.span, fid, expr.text
                )

    def _source_match(self, match: RuleMatch, fn: Function, expr: Expr) -> None:
        mode = match.values.get("mode", "")
        kind = match.values.get("kind", match.family)
        key = expr_key(expr.span)
        if mode == "container":
            self.d.sources.append(
                SourceSpec(
                    sid=f"{expr.span.file}:{expr.span.start_line}:{expr.span.start_byte}:container",
                    kind=kind,
                    field=None,
                    span=expr.span,
                    fid=fn.fid,
                    symbol=f"{fn.qualname}:{expr.text}",
                    hints=[],
                    rule_ids=[match.rule_id],
                    inject=("expr", *map(str, key)),
                    pseudo=True,
                    context=self.context_for(fn),
                )
            )
        elif mode == "field":
            raw_key = match.values.get("key", "")
            name = raw_key.strip("\"'`")
            hints = self.lexicon.match(name, self.context_for(fn))
            if not hints:
                return
            self.d.sources.append(
                SourceSpec(
                    sid=f"{expr.span.file}:{expr.span.start_line}:{name}",
                    kind=kind,
                    field=name,
                    span=expr.span,
                    fid=fn.fid,
                    symbol=f"{fn.qualname}:{expr.text}",
                    hints=hints,
                    rule_ids=[match.rule_id],
                    inject=("expr", *map(str, key)),
                    context=self.context_for(fn),
                )
            )
        elif mode == "orm_read":
            model = match.values.get("model", "")
            call = expr if isinstance(expr, Call) else None
            if call is None:
                return
            canonical = model[:1].upper() + model[1:]
            for column in self.model_columns(model):
                hints = self.lexicon.match(column, [canonical, model])
                if not hints:
                    continue
                self.d.sources.append(
                    SourceSpec(
                        sid=f"{expr.span.file}:{expr.span.start_line}:{canonical}.{column}",
                        kind="orm_read",
                        field=column,
                        span=expr.span,
                        fid=fn.fid,
                        symbol=f"{fn.qualname}:{canonical}.{column}",
                        hints=hints,
                        rule_ids=[match.rule_id],
                        inject=("call", call.cid),
                        label_prefix=(column,),
                        model=canonical,
                        context=[canonical],
                    )
                )

    def _sink_match(self, match: RuleMatch, fn: Function, expr: Expr) -> None:
        call = expr if isinstance(expr, Call) else None
        if call is None:
            calls = [e for e in walk_expr(expr) if isinstance(e, Call)]
            call = calls[0] if calls else None
        if call is None:
            self.d.unmatched_rules.append(match)
            return
        existing = self.d.sinks.get(call.cid)
        model = match.values.get("model")
        if model is None and match.family == "orm":
            model = self._orm_model_of_write(fn, call)
        if existing is not None:
            if match.rule_id not in existing.rule_ids:
                existing.rule_ids.append(match.rule_id)
            existing.persists = existing.persists or bool(match.metadata.get("persists"))
            return
        spec = SinkSpec(
            cid=call.cid,
            call=call,
            fid=fn.fid,
            rule_ids=[match.rule_id],
            family=match.family,
            persists=bool(match.metadata.get("persists")),
            returns_input=bool(match.metadata.get("returns_input")),
            model=(model[:1].upper() + model[1:]) if model else None,
        )
        registry_id = match.metadata.get("registry")
        if registry_id:
            # Globals such as gtag() and fbq() have no import to resolve.
            spec.registry = self.registry.get(str(registry_id))
            spec.method = call.func.text.split(".")[-1]
        self.d.sinks[call.cid] = spec

    def _orm_model_of_write(self, fn: Function, call: Call) -> str | None:
        for arg in call.args:
            t = self.project.infer_type(fn, arg)
            if isinstance(t, ClassT):
                return self.project.classes[t.cid].name
        return None

    def _registry_sinks(self) -> None:
        for fn in self.project.functions.values():
            language = self.project.language(fn)
            for stmt in fn.body:
                for root in stmt_exprs(stmt):
                    for e in walk_expr(root):
                        if not isinstance(e, Call):
                            continue
                        target = self.project.resolve_call(fn, e)
                        if target.kind != "external" or target.spec is None:
                            continue
                        path = target.import_path(language) or target.spec
                        entry = self.registry.match_import(language, path)
                        if target.is_constructor or e.is_new:
                            continue
                        if entry is None:
                            self._dependency_sink(fn, e, language, path, target.method or "")
                            continue
                        method = (target.method or "").removesuffix("()")
                        if not entry.is_sink_call(method):
                            continue
                        rule_id = (
                            f"{'python' if language == 'python' else 'ts'}.sink.registry.{entry.id}"
                        )
                        sink = self.d.sinks.get(e.cid)
                        if sink is None:
                            sink = SinkSpec(e.cid, e, fn.fid, [], "registry")
                            self.d.sinks[e.cid] = sink
                        if rule_id not in sink.rule_ids:
                            sink.rule_ids.append(rule_id)
                        sink.registry = entry
                        sink.method = method
                        sink.event_path = entry.event_paths.get(method)
                        sink.external = target.external
                        if entry.destination_class == "cloud_provider" and method in (
                            "put_object",
                            "upload_file",
                            "upload_fileobj",
                            "send",
                            "put_item",
                            "upload",
                            "putObject",
                        ):
                            sink.persists = True

    def _dependency_sink(
        self, fn: Function, call: Call, language: str, path: str, method: str
    ) -> None:
        method = method.removesuffix("()")
        for profile in self.dependencies:
            if profile.matches_import(path) and method in profile.network_methods:
                prefix = "python" if language == "python" else "ts"
                rule_id = f"{prefix}.sink.dependency.{profile.name}"
                sink = self.d.sinks.get(call.cid)
                if sink is None:
                    sink = SinkSpec(call.cid, call, fn.fid, [], "dependency")
                    self.d.sinks[call.cid] = sink
                if rule_id not in sink.rule_ids:
                    sink.rule_ids.append(rule_id)
                sink.dependency = profile
                sink.method = method
                return


def parse_prisma_schema(text: str) -> dict[str, list[str]]:
    models: dict[str, list[str]] = {}
    names = set(re.findall(r"^\s*model\s+(\w+)\s*\{", text, re.MULTILINE))
    for match in re.finditer(r"^\s*model\s+(\w+)\s*\{(.*?)^\s*\}", text, re.MULTILINE | re.DOTALL):
        name, body = match.group(1), match.group(2)
        columns = []
        for line in body.splitlines():
            parts = line.strip().split()
            if len(parts) < 2 or parts[0].startswith(("@@", "//")):
                continue
            ftype = parts[1].rstrip("?[]")
            if ftype in names:
                continue  # relation field
            columns.append(parts[0])
        models[name] = columns
    return models


def _methods_kwarg(call: Call | None) -> str | None:
    """The first method in ``methods=[...]`` on a Flask or FastAPI ``route`` decorator."""
    for key, value in call.kwargs if call is not None else []:
        if key == "methods" and isinstance(value, Container):
            for _, item in value.items:
                if isinstance(item, Literal) and isinstance(item.value, str):
                    return item.value.upper()
    return None


def _kwarg_string(call: Call, *names: str) -> str | None:
    for key, value in call.kwargs:
        if key in names and isinstance(value, Literal) and isinstance(value.value, str):
            return value.value
    return None


def _first_string(args: list[Expr]) -> str | None:
    for a in args:
        if isinstance(a, Literal) and isinstance(a.value, str):
            return a.value
    return None


def _callee_name(expr: Expr | None) -> str | None:
    if isinstance(expr, Call):
        func = expr.func
        if isinstance(func, Name):
            return func.id
        if isinstance(func, Attr):
            return func.attr
    return None


def is_personal_container_key(container: Container, lexicon: Lexicon) -> bool:
    return any(k and lexicon.match(k) for k, _ in container.items)


__all__ = [
    "Detection",
    "Detector",
    "EntryPoint",
    "SinkSpec",
    "SourceSpec",
    "TransformSpec",
    "expr_key",
    "parse_prisma_schema",
    "tokenize",
]
