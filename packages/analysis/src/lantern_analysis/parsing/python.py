"""Lower Python source to the Lantern IR with tree-sitter."""

from __future__ import annotations

from functools import cache

import tree_sitter_python
from tree_sitter import Language, Parser
from tree_sitter import Node as TSNode

from lantern_analysis.ir import (
    Assign,
    Attr,
    AttrT,
    Call,
    Class,
    ClassField,
    Compound,
    Container,
    Delete,
    ExceptBind,
    Expr,
    ExprStmt,
    ForLoop,
    FuncRef,
    Function,
    Import,
    Literal,
    Module,
    Name,
    NameT,
    Param,
    Raise,
    Return,
    StarT,
    Stmt,
    Subscript,
    SubscriptT,
    Target,
    TupleT,
)
from lantern_analysis.model import Span
from lantern_analysis.parsing.common import Lowerer, child, named_children, unquote


@cache
def _parser() -> Parser:
    return Parser(Language(tree_sitter_python.language()))


def module_name_for(path: str) -> str:
    """app/routes/users.py -> app.routes.users; pkg/__init__.py -> pkg."""
    parts = path[:-3].split("/") if path.endswith(".py") else path.split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class PythonLowerer(Lowerer):
    def __init__(self, file: str, source: bytes) -> None:
        super().__init__(file, source)
        self.mid = module_name_for(file)
        self.module = Module(mid=self.mid, file=file, language="python", source=source)
        self._lambda_count = 0

    # ------------------------------------------------------------------ entry point

    def lower(self) -> Module:
        tree = _parser().parse(self.source)
        root = tree.root_node
        self.module.parse_errors = _count_errors(root)
        module_fn = Function(
            fid=f"{self.mid}:<module>",
            name="<module>",
            module=self.mid,
            span=self.span(root),
            params=[],
            kind="module",
        )
        self.module.functions[module_fn.fid] = module_fn
        self.module.module_fn = module_fn.fid
        module_fn.body = self.block(list(named_children(root)), module_fn, None)
        return self.module

    # ------------------------------------------------------------------ statements

    def block(self, nodes: list[TSNode], fn: Function, cls: Class | None) -> list[Stmt]:
        out: list[Stmt] = []
        for node in nodes:
            out.extend(self.statement(node, fn, cls))
        return out

    def body_of(self, node: TSNode | None, fn: Function, cls: Class | None) -> list[Stmt]:
        if node is None:
            return []
        return self.block(list(named_children(node)), fn, cls)

    def statement(self, node: TSNode, fn: Function, cls: Class | None) -> list[Stmt]:
        t = node.type
        if t == "expression_statement":
            out: list[Stmt] = []
            for inner in named_children(node):
                out.extend(self.expression_statement(inner, fn, cls))
            return out
        if t == "return_statement":
            values = list(named_children(node))
            value = self.expr(values[0], fn) if values else None
            return [Return(self.span(node), self.text(node), value)]
        if t == "raise_statement":
            values = [c for c in named_children(node)]
            exc = self.expr(values[0], fn) if values else None
            return [Raise(self.span(node), self.text(node), exc)]
        if t == "delete_statement":
            return [
                Delete(self.span(node), self.text(node), self.expr(c, fn))
                for c in named_children(node)
            ]
        if t in ("function_definition", "decorated_definition", "class_definition"):
            self.definition(node, fn, cls, [])
            return []
        if t in ("import_statement", "import_from_statement", "future_import_statement"):
            self.imports(node)
            return []
        if t == "if_statement":
            return self.if_statement(node, fn, cls)
        if t == "for_statement":
            target = self.target(child(node, "left"), fn)
            iterable = self.expr(child(node, "right"), fn)
            loop = ForLoop(self.span(node), self.text(node), target, iterable)
            return [
                loop,
                *self.body_of(child(node, "body"), fn, cls),
                *self.body_of(child(node, "alternative"), fn, cls),
            ]
        if t == "while_statement":
            cond = child(node, "condition")
            out = [ExprStmt(self.span(cond), self.text(cond), self.expr(cond, fn))] if cond else []
            return out + self.body_of(child(node, "body"), fn, cls)
        if t == "try_statement":
            out = self.body_of(child(node, "body"), fn, cls)
            for clause in named_children(node):
                if clause.type == "except_clause":
                    out.extend(self.except_clause(clause, fn, cls))
                elif clause.type in ("else_clause", "finally_clause"):
                    out.extend(self.body_of(child(clause, "body") or clause, fn, cls))
            return out
        if t == "with_statement":
            out = []
            for item in node.children:
                if item.type == "with_clause":
                    for with_item in named_children(item):
                        out.extend(self.with_item(with_item, fn))
            return out + self.body_of(child(node, "body"), fn, cls)
        if t in ("block", "else_clause", "elif_clause", "finally_clause"):
            return self.body_of(node, fn, cls)
        if t == "match_statement":
            out = []
            for sub in named_children(node):
                if sub.type == "block":
                    for case in named_children(sub):
                        out.extend(self.body_of(child(case, "consequence"), fn, cls))
            return out
        return []

    def expression_statement(self, node: TSNode, fn: Function, cls: Class | None) -> list[Stmt]:
        t = node.type
        if t == "assignment":
            return self.assignment(node, fn, cls)
        if t == "augmented_assignment":
            left = child(node, "left")
            right = child(node, "right")
            if left is None or right is None:
                return []
            value = Compound(
                self.span(node),
                self.text(node),
                "binop",
                [self.expr(left, fn), self.expr(right, fn)],
            )
            return [Assign(self.span(node), self.text(node), [self.target(left, fn)], value)]
        if t == "string" and not any(c.type == "interpolation" for c in node.children):
            return []  # docstring
        return [ExprStmt(self.span(node), self.text(node), self.expr(node, fn))]

    def assignment(self, node: TSNode, fn: Function, cls: Class | None) -> list[Stmt]:
        targets: list[TSNode] = []
        annotation_node = child(node, "type")
        current: TSNode | None = node
        value_node: TSNode | None = None
        while current is not None and current.type == "assignment":
            left = child(current, "left")
            if left is not None:
                targets.append(left)
            value_node = child(current, "right")
            current = value_node
        annotation = self.text(annotation_node) if annotation_node is not None else None
        if cls is not None and fn.kind == "class-body":
            for left in targets:
                if left.type == "identifier":
                    name = self.raw(left)
                    default = self.expr(value_node, fn) if value_node is not None else None
                    cls.fields[name] = ClassField(name, annotation, default, self.span(left))
        if value_node is None:
            return []
        value = self.expr(value_node, fn)
        return [
            Assign(
                self.span(node),
                self.text(node),
                [self.target(t, fn) for t in targets],
                value,
                annotation,
            )
        ]

    def except_clause(self, node: TSNode, fn: Function, cls: Class | None) -> list[Stmt]:
        out: list[Stmt] = []
        types: list[Expr] = []
        name: str | None = None
        body: TSNode | None = None
        for sub in named_children(node):
            if sub.type == "block":
                body = sub
            elif sub.type == "as_pattern":
                inner = list(named_children(sub))
                if inner:
                    types.append(self.expr(inner[0], fn))
                alias = child(sub, "alias")
                if alias is not None:
                    name = self.raw(alias).strip()
            else:
                types.append(self.expr(sub, fn))
        if name:
            out.append(ExceptBind(self.span(node), self.text(node), types, name))
        return out + self.body_of(body, fn, cls)

    def with_item(self, node: TSNode, fn: Function) -> list[Stmt]:
        value = child(node, "value")
        if value is None:
            return []
        if value.type == "as_pattern":
            inner = list(named_children(value))
            alias = child(value, "alias")
            if inner and alias is not None:
                alias_target = next(iter(named_children(alias)), alias)
                return [
                    Assign(
                        self.span(node),
                        self.text(node),
                        [self.target(alias_target, fn)],
                        self.expr(inner[0], fn),
                    )
                ]
        return [ExprStmt(self.span(node), self.text(node), self.expr(value, fn))]

    def if_statement(self, node: TSNode, fn: Function, cls: Class | None) -> list[Stmt]:
        cond = child(node, "condition")
        if fn.kind == "module" and cond is not None and _is_main_guard(self.raw(cond)):
            main = Function(
                fid=f"{self.mid}:<main>",
                name="<main>",
                module=self.mid,
                span=self.span(node),
                params=[],
                parent=fn.fid,
                kind="main",
            )
            self.module.functions[main.fid] = main
            self.module.main_fn = main.fid
            main.body = self.body_of(child(node, "consequence"), main, None)
            return []
        out: list[Stmt] = []
        if cond is not None:
            out.append(ExprStmt(self.span(cond), self.text(cond), self.expr(cond, fn)))
        out.extend(self.body_of(child(node, "consequence"), fn, cls))
        for alt in node.children:
            if alt.type == "elif_clause":
                c = child(alt, "condition")
                if c is not None:
                    out.append(ExprStmt(self.span(c), self.text(c), self.expr(c, fn)))
                out.extend(self.body_of(child(alt, "consequence"), fn, cls))
            elif alt.type == "else_clause":
                out.extend(self.body_of(child(alt, "body"), fn, cls))
        return out

    # ------------------------------------------------------------------ definitions

    def definition(
        self, node: TSNode, fn: Function, cls: Class | None, decorators: list[Expr]
    ) -> None:
        if node.type == "decorated_definition":
            decos = [
                self.expr(next(named_children(d)), fn)
                for d in node.children
                if d.type == "decorator" and next(named_children(d), None) is not None
            ]
            inner = child(node, "definition")
            if inner is not None:
                self.definition(inner, fn, cls, decos)
            return
        if node.type == "class_definition":
            self.class_definition(node, fn, decorators)
            return
        if node.type == "function_definition":
            self.function_definition(node, fn, cls, decorators)

    def function_definition(
        self, node: TSNode, parent: Function, cls: Class | None, decorators: list[Expr]
    ) -> Function:
        name_node = child(node, "name")
        name = self.raw(name_node) if name_node is not None else "<anonymous>"
        if cls is not None:
            qual = f"{cls.name}.{name}"
            kind = "method"
        elif parent.kind in ("module",):
            qual = name
            kind = "function"
        else:
            qual = f"{parent.qualname}.<locals>.{name}"
            kind = "function"
        fid = f"{self.mid}:{qual}"
        returns = child(node, "return_type")
        fn = Function(
            fid=fid,
            name=name,
            module=self.mid,
            span=self.span(node),
            params=[],
            decorators=decorators,
            cls=cls.cid if cls is not None else None,
            parent=None if parent.kind in ("module", "class-body") else parent.fid,
            kind=kind,
            is_async=any(c.type == "async" for c in node.children),
            returns=self.text(returns) if returns is not None else None,
        )
        self.module.functions[fid] = fn
        if cls is not None:
            cls.methods[name] = fid
        params = child(node, "parameters")
        fn.params = self.parameters(params, fn) if params is not None else []
        if cls is not None and fn.params and not _is_static(decorators):
            fn.params[0].kind = "self"
        fn.body = self.body_of(child(node, "body"), fn, None)
        return fn

    def parameters(self, node: TSNode, fn: Function) -> list[Param]:
        params: list[Param] = []
        for p in named_children(node):
            t = p.type
            if t == "identifier":
                params.append(Param(self.raw(p), self.span(p)))
            elif t == "typed_parameter":
                ident = next((c for c in named_children(p) if c.type in ("identifier",)), None)
                splat = next(
                    (
                        c
                        for c in named_children(p)
                        if c.type in ("list_splat_pattern", "dictionary_splat_pattern")
                    ),
                    None,
                )
                ann = child(p, "type")
                annotation = self.text(ann) if ann is not None else None
                if ident is not None:
                    params.append(Param(self.raw(ident), self.span(p), annotation))
                elif splat is not None:
                    inner = next(named_children(splat), None)
                    kind = "vararg" if splat.type == "list_splat_pattern" else "kwarg"
                    if inner is not None:
                        params.append(Param(self.raw(inner), self.span(p), annotation, None, kind))
            elif t in ("default_parameter", "typed_default_parameter"):
                name_node = child(p, "name")
                ann = child(p, "type")
                value = child(p, "value")
                if name_node is not None:
                    params.append(
                        Param(
                            self.raw(name_node),
                            self.span(p),
                            self.text(ann) if ann is not None else None,
                            self.expr(value, fn) if value is not None else None,
                        )
                    )
            elif t in ("list_splat_pattern", "dictionary_splat_pattern"):
                inner = next(named_children(p), None)
                if inner is not None:
                    kind = "vararg" if t == "list_splat_pattern" else "kwarg"
                    params.append(Param(self.raw(inner), self.span(p), None, None, kind))
        return params

    def class_definition(self, node: TSNode, parent: Function, decorators: list[Expr]) -> Class:
        name_node = child(node, "name")
        name = self.raw(name_node) if name_node is not None else "<anonymous>"
        cid = f"{self.mid}:{name}"
        cls = Class(
            cid=cid, name=name, module=self.mid, span=self.span(node), decorators=decorators
        )
        supers = child(node, "superclasses")
        if supers is not None:
            cls.bases = [
                self.expr(b, parent) for b in named_children(supers) if b.type != "keyword_argument"
            ]
        self.module.classes[cid] = cls
        body_fn = Function(
            fid=f"{self.mid}:{name}.<body>",
            name="<body>",
            module=self.mid,
            span=self.span(node),
            params=[],
            kind="class-body",
            cls=cid,
        )
        body = child(node, "body")
        if body is not None:
            for stmt in named_children(body):
                if stmt.type in ("function_definition", "decorated_definition", "class_definition"):
                    self.definition(stmt, body_fn, cls, [])
                elif stmt.type == "expression_statement":
                    for inner in named_children(stmt):
                        if inner.type == "assignment":
                            self.assignment(inner, body_fn, cls)
        return cls

    def imports(self, node: TSNode) -> None:
        span = self.span(node)
        if node.type == "import_statement":
            for item in named_children(node):
                if item.type == "dotted_name":
                    dotted_name = self.raw(item)
                    local = dotted_name.split(".")[0]
                    self.module.imports[local] = Import(local, local, None, span)
                    if "." in dotted_name:
                        self.module.imports[dotted_name] = Import(
                            dotted_name, dotted_name, None, span
                        )
                elif item.type == "aliased_import":
                    target = child(item, "name")
                    alias = child(item, "alias")
                    if target is not None and alias is not None:
                        self.module.imports[self.raw(alias)] = Import(
                            self.raw(alias), self.raw(target), None, span
                        )
            return
        if node.type == "import_from_statement":
            module_node = child(node, "module_name")
            if module_node is None:
                return
            level = 0
            module = self.raw(module_node)
            if module_node.type == "relative_import":
                prefix = next((c for c in module_node.children if c.type == "import_prefix"), None)
                level = len(self.raw(prefix)) if prefix is not None else 0
                rest = next((c for c in module_node.children if c.type == "dotted_name"), None)
                module = self.raw(rest) if rest is not None else ""
            for i, item in enumerate(node.children):
                if node.field_name_for_child(i) != "name":
                    continue
                if item.type == "dotted_name":
                    name = self.raw(item)
                    self.module.imports[name] = Import(name, module, name, span, level=level)
                elif item.type == "aliased_import":
                    target = child(item, "name")
                    alias = child(item, "alias")
                    if target is not None and alias is not None:
                        self.module.imports[self.raw(alias)] = Import(
                            self.raw(alias), module, self.raw(target), span, level=level
                        )

    # ------------------------------------------------------------------ targets

    def target(self, node: TSNode | None, fn: Function) -> Target:
        if node is None:
            return NameT(Span(self.file, 0, 0), "_")
        t = node.type
        span = self.span(node)
        if t == "identifier":
            return NameT(span, self.raw(node))
        if t == "attribute":
            base = child(node, "object")
            attr = child(node, "attribute")
            if base is not None and attr is not None:
                return AttrT(span, self.expr(base, fn), self.raw(attr))
        if t == "subscript":
            base = child(node, "value")
            key = child(node, "subscript")
            if base is not None and key is not None:
                return SubscriptT(span, self.expr(base, fn), self.expr(key, fn))
        if t in (
            "pattern_list",
            "tuple_pattern",
            "list_pattern",
            "tuple",
            "list",
            "expression_list",
        ):
            return TupleT(span, [self.target(c, fn) for c in named_children(node)])
        if t in ("list_splat_pattern", "list_splat"):
            inner = next(named_children(node), None)
            return StarT(span, self.target(inner, fn))
        if t == "parenthesized_expression":
            inner = next(named_children(node), None)
            return self.target(inner, fn)
        return NameT(span, "_")

    # ------------------------------------------------------------------ expressions

    def expr(self, node: TSNode | None, fn: Function) -> Expr:
        if node is None:
            return Literal(Span(self.file, 0, 0), "", None)
        t = node.type
        span = self.span(node)
        text = self.text(node)
        if t == "identifier":
            return Name(span, text, self.raw(node))
        if t == "attribute":
            base = child(node, "object")
            attr = child(node, "attribute")
            if base is not None and attr is not None:
                return Attr(span, text, self.expr(base, fn), self.raw(attr))
        if t == "subscript":
            base = child(node, "value")
            key = child(node, "subscript")
            if base is not None:
                return Subscript(span, text, self.expr(base, fn), self.expr(key, fn))
        if t == "call":
            return self.call(node, fn)
        if t == "string":
            parts = [
                self.expr(child(c, "expression") or next(named_children(c), None), fn)
                for c in node.children
                if c.type == "interpolation"
            ]
            if parts:
                return Compound(span, text, "fstring", parts)
            return Literal(span, text, unquote(self.raw(node)))
        if t == "concatenated_string":
            parts = [self.expr(c, fn) for c in named_children(node)]
            if all(isinstance(p, Literal) for p in parts):
                value = "".join(str(p.value) for p in parts if isinstance(p, Literal))
                return Literal(span, text, value)
            return Compound(span, text, "fstring", parts)
        if t == "integer":
            try:
                return Literal(span, text, int(self.raw(node).replace("_", ""), 0))
            except ValueError:
                return Literal(span, text, self.raw(node))
        if t == "float":
            return Literal(span, text, self.raw(node))
        if t in ("true", "false"):
            return Literal(span, text, t == "true")
        if t in ("none", "ellipsis"):
            return Literal(span, text, None)
        if t == "dictionary":
            items: list[tuple[str | None, Expr]] = []
            spreads: list[Expr] = []
            for c in named_children(node):
                if c.type == "pair":
                    key_expr = self.expr(child(c, "key"), fn)
                    value_expr = self.expr(child(c, "value"), fn)
                    key_name = (
                        key_expr.value
                        if isinstance(key_expr, Literal) and isinstance(key_expr.value, str)
                        else None
                    )
                    items.append((key_name, value_expr))
                    if key_name is None and not isinstance(key_expr, Literal):
                        spreads.append(key_expr)
                elif c.type == "dictionary_splat":
                    inner = next(named_children(c), None)
                    if inner is not None:
                        spreads.append(self.expr(inner, fn))
            return Container(span, text, "dict", items, spreads)
        if t in ("list", "tuple", "set", "expression_list", "pattern_list"):
            items = []
            spreads = []
            for c in named_children(node):
                if c.type == "list_splat":
                    inner = next(named_children(c), None)
                    if inner is not None:
                        spreads.append(self.expr(inner, fn))
                else:
                    items.append((None, self.expr(c, fn)))
            kind = "tuple" if t in ("tuple", "expression_list", "pattern_list") else t
            return Container(span, text, kind, items, spreads)
        if t in (
            "list_comprehension",
            "set_comprehension",
            "generator_expression",
            "dictionary_comprehension",
        ):
            comp_parts: list[Expr] = []
            for c in named_children(node):
                if c.type == "for_in_clause":
                    right = child(c, "right")
                    if right is not None:
                        comp_parts.append(self.expr(right, fn))
                elif c.type not in ("if_clause",):
                    comp_parts.append(self.expr(c, fn))
            return Compound(span, text, "comprehension", comp_parts)
        if t == "binary_operator":
            return Compound(span, text, "binop", [self.expr(c, fn) for c in named_children(node)])
        if t == "boolean_operator":
            return Compound(span, text, "boolop", [self.expr(c, fn) for c in named_children(node)])
        if t == "comparison_operator":
            return Compound(span, text, "compare", [self.expr(c, fn) for c in named_children(node)])
        if t == "not_operator":
            return Compound(span, text, "not", [self.expr(c, fn) for c in named_children(node)])
        if t == "unary_operator":
            return Compound(span, text, "unary", [self.expr(c, fn) for c in named_children(node)])
        if t == "conditional_expression":
            parts = [self.expr(c, fn) for c in named_children(node)]
            # a if cond else b: the value is a or b, never cond
            chosen = [parts[0], parts[2]] if len(parts) == 3 else parts
            return Compound(span, text, "ternary", chosen)
        if t == "await":
            return Compound(span, text, "await", [self.expr(c, fn) for c in named_children(node)])
        if t == "parenthesized_expression":
            inner = next(named_children(node), None)
            return self.expr(inner, fn) if inner is not None else Literal(span, text, None)
        if t == "lambda":
            return self.lambda_(node, fn)
        if t == "named_expression":
            return self.expr(child(node, "value"), fn)
        if t in ("list_splat", "dictionary_splat", "parenthesized_list_splat"):
            inner = next(named_children(node), None)
            return Compound(span, text, "spread", [self.expr(inner, fn)] if inner else [])
        if t == "keyword_argument":
            return self.expr(child(node, "value"), fn)
        if t == "slice":
            return Literal(span, text, "slice")
        if t in ("type", "keyword_separator", "positional_separator"):
            return Literal(span, text, None)
        return self.unknown(node, [self.expr(c, fn) for c in named_children(node)])

    def call(self, node: TSNode, fn: Function) -> Call:
        func = self.expr(child(node, "function"), fn)
        args: list[Expr] = []
        kwargs: list[tuple[str, Expr]] = []
        splats: list[Expr] = []
        arguments = child(node, "arguments")
        if arguments is not None:
            if arguments.type == "generator_expression":
                args.append(self.expr(arguments, fn))
            else:
                for a in named_children(arguments):
                    if a.type == "keyword_argument":
                        key = child(a, "name")
                        if key is not None:
                            kwargs.append((self.raw(key), self.expr(child(a, "value"), fn)))
                    elif a.type in ("list_splat", "dictionary_splat"):
                        inner = next(named_children(a), None)
                        if inner is not None:
                            splats.append(self.expr(inner, fn))
                    else:
                        args.append(self.expr(a, fn))
        return Call(
            self.span(node), self.text(node), func, args, kwargs, splats, False, self.cid(node)
        )

    def lambda_(self, node: TSNode, parent: Function) -> FuncRef:
        self._lambda_count += 1
        fid = f"{self.mid}:{parent.qualname}.<lambda{node.start_point[0] + 1}_{self._lambda_count}>"
        fn = Function(
            fid=fid,
            name="<lambda>",
            module=self.mid,
            span=self.span(node),
            params=[],
            parent=None if parent.kind in ("module",) else parent.fid,
            kind="lambda",
        )
        self.module.functions[fid] = fn
        params = child(node, "parameters")
        if params is not None:
            fn.params = self.parameters(params, fn)
        body = child(node, "body")
        if body is not None:
            fn.body = [Return(self.span(body), self.text(body), self.expr(body, fn))]
        return FuncRef(self.span(node), self.text(node), fid)


def _is_main_guard(condition: str) -> bool:
    compact = condition.replace(" ", "").replace("'", '"')
    return compact in ('__name__=="__main__"', '"__main__"==__name__')


def _is_static(decorators: list[Expr]) -> bool:
    return any(isinstance(d, Name) and d.id == "staticmethod" for d in decorators)


def _count_errors(node: TSNode) -> int:
    count = 1 if node.type == "ERROR" or node.is_missing else 0
    for c in node.children:
        count += _count_errors(c)
    return count


def parse_python(file: str, source: bytes) -> Module:
    return PythonLowerer(file, source).lower()


__all__ = ["module_name_for", "parse_python"]


# Re-export for type checkers that look for Attr/Call here.
_ = (Attr, Call)
