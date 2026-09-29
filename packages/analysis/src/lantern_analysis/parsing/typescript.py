"""Lower TypeScript (and JavaScript) source to the Katz IR with tree-sitter."""

from __future__ import annotations

from functools import cache

import tree_sitter_typescript
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
    ObjectT,
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

TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


@cache
def _parser(tsx: bool) -> Parser:
    lang = (
        tree_sitter_typescript.language_tsx()
        if tsx
        else tree_sitter_typescript.language_typescript()
    )
    return Parser(Language(lang))


def module_name_for(path: str) -> str:
    for ext in TS_EXTENSIONS:
        if path.endswith(ext):
            path = path[: -len(ext)]
            break
    if path.endswith("/index"):
        path = path[: -len("/index")]
    return path


_FUNCTION_TYPES = ("function_declaration", "generator_function_declaration")
_FUNCTION_EXPRS = ("arrow_function", "function_expression", "function", "generator_function")


class TypeScriptLowerer(Lowerer):
    def __init__(self, file: str, source: bytes) -> None:
        super().__init__(file, source)
        self.mid = module_name_for(file)
        self.module = Module(mid=self.mid, file=file, language="typescript", source=source)

    def lower(self) -> Module:
        tsx = self.file.endswith((".tsx", ".jsx"))
        root = _parser(tsx).parse(self.source).root_node
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
        module_fn.body = self.block(list(named_children(root)), module_fn)
        return self.module

    # ------------------------------------------------------------------ statements

    def block(self, nodes: list[TSNode], fn: Function) -> list[Stmt]:
        out: list[Stmt] = []
        for node in nodes:
            out.extend(self.statement(node, fn))
        return out

    def body_of(self, node: TSNode | None, fn: Function) -> list[Stmt]:
        if node is None:
            return []
        if node.type == "statement_block":
            return self.block(list(named_children(node)), fn)
        return self.statement(node, fn)

    def statement(self, node: TSNode, fn: Function, exported: bool = False) -> list[Stmt]:
        t = node.type
        span = self.span(node)
        if t == "import_statement":
            self.import_statement(node)
            return []
        if t == "export_statement":
            return self.export_statement(node, fn)
        if t == "expression_statement":
            inner = next(named_children(node), None)
            if inner is None:
                return []
            return self.expression_statement(inner, fn)
        if t in ("lexical_declaration", "variable_declaration"):
            out: list[Stmt] = []
            for decl in named_children(node):
                if decl.type != "variable_declarator":
                    continue
                name_node = child(decl, "name")
                value_node = child(decl, "value")
                if name_node is None:
                    continue
                if exported and name_node.type == "identifier":
                    local = self.raw(name_node)
                    self.module.exports[local] = local
                if value_node is None:
                    continue
                value = self.expr(value_node, fn, name_hint=self._simple_name(name_node))
                ann = child(decl, "type")
                out.append(
                    Assign(
                        self.span(decl),
                        self.text(decl),
                        [self.target(name_node, fn)],
                        value,
                        self.text(ann).lstrip(": ") if ann is not None else None,
                    )
                )
            return out
        if t == "return_statement":
            inner = next(named_children(node), None)
            return [Return(span, self.text(node), self.expr(inner, fn) if inner else None)]
        if t == "throw_statement":
            inner = next(named_children(node), None)
            return [Raise(span, self.text(node), self.expr(inner, fn) if inner else None)]
        if t in _FUNCTION_TYPES:
            f = self.function(node, fn, None, name=None)
            f.exported = exported
            if exported:
                self.module.exports[f.name] = f.name
            return []
        if t in ("class_declaration", "abstract_class_declaration", "class"):
            c = self.class_declaration(node, fn)
            c.exported = exported
            if exported:
                self.module.exports[c.name] = c.name
            return []
        if t == "if_statement":
            out = []
            cond = child(node, "condition")
            if cond is not None:
                out.append(ExprStmt(self.span(cond), self.text(cond), self.expr(cond, fn)))
            out.extend(self.body_of(child(node, "consequence"), fn))
            alt = child(node, "alternative")
            if alt is not None:
                for sub in named_children(alt):
                    out.extend(self.body_of(sub, fn))
            return out
        if t == "statement_block":
            return self.block(list(named_children(node)), fn)
        if t in ("for_in_statement",):
            left = child(node, "left")
            right = child(node, "right")
            out = []
            if left is not None and right is not None:
                target_node: TSNode = left
                if left.type in ("lexical_declaration", "variable_declaration"):
                    first_decl = next(named_children(left), None)
                    named = child(first_decl, "name") if first_decl is not None else None
                    target_node = named or left
                out.append(
                    ForLoop(
                        span, self.text(node), self.target(target_node, fn), self.expr(right, fn)
                    )
                )
            return out + self.body_of(child(node, "body"), fn)
        if t in ("for_statement", "while_statement", "do_statement"):
            out = []
            for field_name in ("initializer", "condition", "increment"):
                part = child(node, field_name)
                if part is not None:
                    out.extend(
                        self.statement(part, fn)
                        or [ExprStmt(self.span(part), self.text(part), self.expr(part, fn))]
                    )
            return out + self.body_of(child(node, "body"), fn)
        if t == "try_statement":
            out = self.body_of(child(node, "body"), fn)
            handler = child(node, "handler")
            if handler is not None:
                param = child(handler, "parameter")
                if param is not None and param.type == "identifier":
                    out.append(
                        ExceptBind(self.span(handler), self.text(handler), [], self.raw(param))
                    )
                out.extend(self.body_of(child(handler, "body"), fn))
            finalizer = child(node, "finalizer")
            if finalizer is not None:
                out.extend(self.body_of(child(finalizer, "body") or finalizer, fn))
            return out
        if t == "switch_statement":
            out = []
            switch_value = child(node, "value")
            if switch_value is not None:
                out.append(
                    ExprStmt(
                        self.span(switch_value),
                        self.text(switch_value),
                        self.expr(switch_value, fn),
                    )
                )
            body = child(node, "body")
            if body is not None:
                for case in named_children(body):
                    for sub in named_children(case):
                        if sub.type.endswith("statement") or sub.type.endswith("declaration"):
                            out.extend(self.statement(sub, fn))
            return out
        if t == "labeled_statement":
            return self.body_of(child(node, "body"), fn)
        return []

    def export_statement(self, node: TSNode, fn: Function) -> list[Stmt]:
        is_default = any(c.type == "default" for c in node.children)
        declaration = child(node, "declaration")
        if declaration is not None:
            if is_default and declaration.type in _FUNCTION_TYPES:
                f = self.function(declaration, fn, None, name=None)
                f.exported = True
                self.module.default_export = f.name
                return []
            return self.statement(declaration, fn, exported=True)
        value = child(node, "value")
        if value is not None and is_default:
            expr = self.expr(value, fn, name_hint="default")
            if isinstance(expr, Name):
                self.module.default_export = expr.id
            elif isinstance(expr, FuncRef):
                self.module.default_export = self.module.functions[expr.fid].name
                self.module.functions[expr.fid].exported = True
            return [
                Assign(self.span(node), self.text(node), [NameT(self.span(node), "default")], expr)
            ]
        for clause in named_children(node):
            if clause.type == "export_clause":
                for spec in named_children(clause):
                    name = child(spec, "name")
                    alias = child(spec, "alias")
                    if name is not None:
                        local = self.raw(name)
                        self.module.exports[self.raw(alias) if alias else local] = local
        return []

    def expression_statement(self, node: TSNode, fn: Function) -> list[Stmt]:
        t = node.type
        span = self.span(node)
        if t == "assignment_expression":
            left = child(node, "left")
            right = child(node, "right")
            if left is None or right is None:
                return []
            return [
                Assign(
                    span,
                    self.text(node),
                    [self.target(left, fn)],
                    self.expr(right, fn, name_hint=self._simple_name(left)),
                )
            ]
        if t == "augmented_assignment_expression":
            left = child(node, "left")
            right = child(node, "right")
            if left is None or right is None:
                return []
            value = Compound(
                span, self.text(node), "binop", [self.expr(left, fn), self.expr(right, fn)]
            )
            return [Assign(span, self.text(node), [self.target(left, fn)], value)]
        if t == "unary_expression" and node.children and node.children[0].type == "delete":
            arg = child(node, "argument")
            if arg is not None:
                return [Delete(span, self.text(node), self.expr(arg, fn))]
        if t == "sequence_expression":
            out: list[Stmt] = []
            for sub in named_children(node):
                out.extend(self.expression_statement(sub, fn))
            return out
        if t == "string":
            return []
        return [ExprStmt(span, self.text(node), self.expr(node, fn))]

    # ------------------------------------------------------------------ definitions

    def import_statement(self, node: TSNode) -> None:
        source = child(node, "source")
        if source is None:
            return
        module = unquote(self.raw(source))
        span = self.span(node)
        type_only = self.raw(node).startswith("import type")
        clause = next((c for c in named_children(node) if c.type == "import_clause"), None)
        if clause is None:
            return
        for part in named_children(clause):
            if part.type == "identifier":
                local = self.raw(part)
                self.module.imports[local] = Import(local, module, "default", span, type_only)
            elif part.type == "namespace_import":
                ident = next(named_children(part), None)
                if ident is not None:
                    local = self.raw(ident)
                    self.module.imports[local] = Import(local, module, None, span, type_only)
            elif part.type == "named_imports":
                for spec in named_children(part):
                    name = child(spec, "name")
                    alias = child(spec, "alias")
                    if name is None:
                        continue
                    local = self.raw(alias) if alias is not None else self.raw(name)
                    spec_type_only = type_only or self.raw(spec).startswith("type ")
                    self.module.imports[local] = Import(
                        local, module, self.raw(name), span, spec_type_only
                    )

    def _simple_name(self, node: TSNode | None) -> str | None:
        if node is None:
            return None
        if node.type in ("identifier", "property_identifier", "shorthand_property_identifier"):
            return self.raw(node)
        if node.type == "member_expression":
            prop = child(node, "property")
            return self.raw(prop) if prop is not None else None
        return None

    def function(
        self,
        node: TSNode,
        parent: Function,
        cls: Class | None,
        name: str | None,
        is_property_method: bool = False,
    ) -> Function:
        name_node = child(node, "name")
        declared = self.raw(name_node) if name_node is not None else None
        fname = declared or name or "anonymous"
        if cls is not None:
            qual = f"{cls.name}.{fname}"
            kind = "method"
        elif parent.kind == "module" and (declared or (name and not is_property_method)):
            qual = fname
            kind = "function"
        else:
            qual = f"{parent.qualname}.<{fname}@{node.start_point[0] + 1}:{node.start_point[1]}>"
            kind = "lambda" if node.type == "arrow_function" else "function"
        fid = f"{self.mid}:{qual}"
        returns = child(node, "return_type")
        fn = Function(
            fid=fid,
            name=fname,
            module=self.mid,
            span=self.span(node),
            params=[],
            cls=cls.cid if cls is not None else None,
            parent=None if parent.kind == "module" else parent.fid,
            kind=kind,
            is_async=any(c.type == "async" for c in node.children),
            returns=self.text(returns).lstrip(": ") if returns is not None else None,
        )
        self.module.functions[fid] = fn
        params = child(node, "parameters")
        single = child(node, "parameter")
        if params is not None:
            fn.params = self.parameters(params, fn)
        elif single is not None:
            fn.params = [Param(self.raw(single), self.span(single))]
        if cls is not None and not any(c.type == "static" for c in node.children):
            fn.params.insert(0, Param("this", fn.span, cls.name, None, "self"))
        body = child(node, "body")
        if body is not None:
            if body.type == "statement_block":
                fn.body = self.block(list(named_children(body)), fn)
            else:
                fn.body = [Return(self.span(body), self.text(body), self.expr(body, fn))]
        if cls is not None and fname == "constructor":
            for p in fn.params:
                if p.is_property:
                    cls.fields[p.name] = ClassField(p.name, p.annotation, None, p.span)
        return fn

    def parameters(self, node: TSNode, fn: Function) -> list[Param]:
        params: list[Param] = []
        for p in named_children(node):
            if p.type not in (
                "required_parameter",
                "optional_parameter",
                "identifier",
                "rest_pattern",
            ):
                continue
            if p.type == "identifier":
                params.append(Param(self.raw(p), self.span(p)))
                continue
            pattern = child(p, "pattern") or next(named_children(p), None)
            ann = child(p, "type")
            value = child(p, "value")
            annotation = self.text(ann).lstrip(": ") if ann is not None else None
            is_property = any(c.type in ("accessibility_modifier", "readonly") for c in p.children)
            kind = "vararg" if pattern is not None and pattern.type == "rest_pattern" else "normal"
            if pattern is None:
                continue
            if pattern.type == "identifier" or pattern.type == "this":
                name = self.raw(pattern)
                target: Target | None = None
            elif pattern.type == "rest_pattern":
                inner = next(named_children(pattern), None)
                name = self.raw(inner) if inner is not None else "rest"
                target = None
            else:
                name = f"<param{len(params)}>"
                target = self.target(pattern, fn)
            params.append(
                Param(
                    name,
                    self.span(p),
                    annotation,
                    self.expr(value, fn) if value is not None else None,
                    kind,
                    is_property,
                    target,
                )
            )
        return params

    def class_declaration(self, node: TSNode, parent: Function) -> Class:
        name_node = child(node, "name")
        name = (
            self.raw(name_node) if name_node is not None else f"<class@{node.start_point[0] + 1}>"
        )
        cid = f"{self.mid}:{name}"
        cls = Class(cid=cid, name=name, module=self.mid, span=self.span(node))
        for sub in named_children(node):
            if sub.type == "class_heritage":
                for clause in named_children(sub):
                    if clause.type == "extends_clause":
                        value = child(clause, "value") or next(named_children(clause), None)
                        if value is not None:
                            cls.bases.append(self.expr(value, parent))
        self.module.classes[cid] = cls
        body = child(node, "body")
        if body is not None:
            for member in named_children(body):
                if member.type == "method_definition":
                    fn = self.function(member, parent, cls, name=None)
                    cls.methods[fn.name] = fn.fid
                elif member.type in ("public_field_definition", "field_definition"):
                    prop = child(member, "name") or child(member, "property")
                    ann = child(member, "type")
                    value = child(member, "value")
                    if prop is not None:
                        fname = self.raw(prop)
                        default = (
                            self.expr(value, parent, name_hint=fname) if value is not None else None
                        )
                        cls.fields[fname] = ClassField(
                            fname,
                            self.text(ann).lstrip(": ") if ann is not None else None,
                            default,
                            self.span(member),
                        )
        return cls

    # ------------------------------------------------------------------ targets

    def target(self, node: TSNode | None, fn: Function) -> Target:
        if node is None:
            return NameT(Span(self.file, 0, 0), "_")
        t = node.type
        span = self.span(node)
        if t in (
            "identifier",
            "shorthand_property_identifier_pattern",
            "shorthand_property_identifier",
        ):
            return NameT(span, self.raw(node))
        if t == "member_expression":
            base = child(node, "object")
            prop = child(node, "property")
            if base is not None and prop is not None:
                return AttrT(span, self.expr(base, fn), self.raw(prop))
        if t == "subscript_expression":
            base = child(node, "object")
            index = child(node, "index")
            if base is not None and index is not None:
                return SubscriptT(span, self.expr(base, fn), self.expr(index, fn))
        if t == "array_pattern":
            elts: list[Target] = []
            for c in named_children(node):
                if c.type == "assignment_pattern":
                    elts.append(self.target(child(c, "left"), fn))
                elif c.type == "rest_pattern":
                    inner = next(named_children(c), None)
                    elts.append(StarT(self.span(c), self.target(inner, fn)))
                else:
                    elts.append(self.target(c, fn))
            return TupleT(span, elts)
        if t == "object_pattern":
            entries: list[tuple[str, Target, Expr | None, Span]] = []
            rest: Target | None = None
            for c in named_children(node):
                cspan = self.span(c)
                if c.type == "shorthand_property_identifier_pattern":
                    key = self.raw(c)
                    entries.append((key, NameT(cspan, key), None, cspan))
                elif c.type == "object_assignment_pattern":
                    left = child(c, "left")
                    right = child(c, "right")
                    if left is not None:
                        key = self.raw(left)
                        default = self.expr(right, fn) if right is not None else None
                        entries.append((key, NameT(cspan, key), default, cspan))
                elif c.type == "pair_pattern":
                    key_node = child(c, "key")
                    value = child(c, "value")
                    if key_node is not None and value is not None:
                        key = unquote(self.raw(key_node))
                        default = None
                        if value.type == "assignment_pattern":
                            default_node = child(value, "right")
                            default = self.expr(default_node, fn) if default_node else None
                            value = child(value, "left") or value
                        entries.append((key, self.target(value, fn), default, cspan))
                elif c.type == "rest_pattern":
                    inner = next(named_children(c), None)
                    rest = self.target(inner, fn)
            return ObjectT(span, entries, rest)
        if t in ("parenthesized_expression", "non_null_expression", "as_expression"):
            inner = next(named_children(node), None)
            return self.target(inner, fn)
        return NameT(span, "_")

    # ------------------------------------------------------------------ expressions

    def expr(self, node: TSNode | None, fn: Function, name_hint: str | None = None) -> Expr:
        if node is None:
            return Literal(Span(self.file, 0, 0), "", None)
        t = node.type
        span = self.span(node)
        text = self.text(node)
        if t in ("identifier", "shorthand_property_identifier", "property_identifier"):
            return Name(span, text, self.raw(node))
        if t == "this":
            return Name(span, text, "this")
        if t in ("super",):
            return Name(span, text, "super")
        if t == "member_expression":
            base = child(node, "object")
            prop = child(node, "property")
            if base is not None and prop is not None:
                return Attr(span, text, self.expr(base, fn), self.raw(prop))
        if t == "subscript_expression":
            base = child(node, "object")
            index = child(node, "index")
            if base is not None:
                return Subscript(span, text, self.expr(base, fn), self.expr(index, fn))
        if t in ("call_expression", "new_expression"):
            return self.call(node, fn, is_new=t == "new_expression")
        if t == "string":
            return Literal(span, text, unquote(self.raw(node)))
        if t == "template_string":
            parts = [
                self.expr(next(named_children(c), None), fn)
                for c in named_children(node)
                if c.type == "template_substitution"
            ]
            if parts:
                return Compound(span, text, "template", parts)
            return Literal(span, text, unquote(self.raw(node)))
        if t == "number":
            return Literal(span, text, self.raw(node))
        if t in ("true", "false"):
            return Literal(span, text, t == "true")
        if t in ("null", "undefined"):
            return Literal(span, text, None)
        if t == "regex":
            return Literal(span, text, self.raw(node))
        if t == "object":
            return self.object_literal(node, fn)
        if t == "array":
            items: list[tuple[str | None, Expr]] = []
            spreads: list[Expr] = []
            for c in named_children(node):
                if c.type == "spread_element":
                    inner = next(named_children(c), None)
                    if inner is not None:
                        spreads.append(self.expr(inner, fn))
                else:
                    items.append((None, self.expr(c, fn)))
            return Container(span, text, "array", items, spreads)
        if t in _FUNCTION_EXPRS:
            f = self.function(node, fn, None, name=name_hint)
            return FuncRef(span, text, f.fid)
        if t in ("class", "class_expression"):
            class_def = self.class_declaration(node, fn)
            return Name(span, text, class_def.name)
        if t == "await_expression":
            return Compound(span, text, "await", [self.expr(c, fn) for c in named_children(node)])
        if t == "parenthesized_expression":
            inner = next(named_children(node), None)
            return (
                self.expr(inner, fn, name_hint) if inner is not None else Literal(span, text, None)
            )
        if t in ("as_expression", "satisfies_expression", "type_assertion"):
            inner = next(named_children(node), None)
            return Compound(span, text, "as", [self.expr(inner, fn)] if inner else [])
        if t == "non_null_expression":
            inner = next(named_children(node), None)
            return Compound(span, text, "nonnull", [self.expr(inner, fn)] if inner else [])
        if t == "ternary_expression":
            consequence = child(node, "consequence")
            alternative = child(node, "alternative")
            return Compound(
                span, text, "ternary", [self.expr(consequence, fn), self.expr(alternative, fn)]
            )
        if t == "binary_expression":
            op_node = child(node, "operator")
            op = self.raw(op_node) if op_node is not None else ""
            parts = [self.expr(child(node, "left"), fn), self.expr(child(node, "right"), fn)]
            if op in ("||", "&&", "??"):
                return Compound(span, text, "boolop", parts)
            if op in ("==", "===", "!=", "!==", "<", ">", "<=", ">="):
                return Compound(span, text, "compare", parts)
            if op == "instanceof":
                return Compound(span, text, "instanceof", parts)
            if op == "in":
                return Compound(span, text, "in", parts)
            return Compound(span, text, "binop", parts)
        if t == "unary_expression":
            op = node.children[0].type if node.children else ""
            arg = child(node, "argument")
            parts = [self.expr(arg, fn)] if arg is not None else []
            if op == "!":
                return Compound(span, text, "not", parts)
            if op == "typeof":
                return Compound(span, text, "typeof", parts)
            return Compound(span, text, "unary", parts)
        if t in ("update_expression",):
            return Compound(span, text, "unary", [self.expr(c, fn) for c in named_children(node)])
        if t == "assignment_expression":
            right = child(node, "right")
            return self.expr(right, fn)
        if t == "spread_element":
            inner = next(named_children(node), None)
            return Compound(span, text, "spread", [self.expr(inner, fn)] if inner else [])
        if t == "sequence_expression":
            parts = [self.expr(c, fn) for c in named_children(node)]
            return parts[-1] if parts else Literal(span, text, None)
        return self.unknown(node, [self.expr(c, fn) for c in named_children(node)])

    def object_literal(self, node: TSNode, fn: Function) -> Container:
        items: list[tuple[str | None, Expr]] = []
        spreads: list[Expr] = []
        for c in named_children(node):
            if c.type == "pair":
                key_node = child(c, "key")
                value_node = child(c, "value")
                if key_node is None:
                    continue
                if key_node.type == "computed_property_name":
                    spreads.append(self.expr(value_node, fn))
                    continue
                key = unquote(self.raw(key_node))
                items.append((key, self.expr(value_node, fn, name_hint=key)))
            elif c.type == "shorthand_property_identifier":
                key = self.raw(c)
                items.append((key, Name(self.span(c), key, key)))
            elif c.type == "spread_element":
                inner = next(named_children(c), None)
                if inner is not None:
                    spreads.append(self.expr(inner, fn))
            elif c.type == "method_definition":
                f = self.function(c, fn, None, name=None, is_property_method=True)
                items.append((f.name, FuncRef(self.span(c), self.text(c), f.fid)))
        return Container(self.span(node), self.text(node), "object", items, spreads)

    def call(self, node: TSNode, fn: Function, is_new: bool) -> Call:
        func_node = child(node, "constructor") if is_new else child(node, "function")
        func = self.expr(func_node, fn)
        args: list[Expr] = []
        splats: list[Expr] = []
        arguments = child(node, "arguments")
        if arguments is not None:
            if arguments.type == "template_string":
                args.append(self.expr(arguments, fn))
            else:
                for a in named_children(arguments):
                    if a.type == "spread_element":
                        inner = next(named_children(a), None)
                        if inner is not None:
                            splats.append(self.expr(inner, fn))
                    else:
                        args.append(self.expr(a, fn))
        return Call(
            self.span(node), self.text(node), func, args, [], splats, is_new, self.cid(node)
        )


def _count_errors(node: TSNode) -> int:
    count = 1 if node.type == "ERROR" or node.is_missing else 0
    for c in node.children:
        count += _count_errors(c)
    return count


def parse_typescript(file: str, source: bytes) -> Module:
    return TypeScriptLowerer(file, source).lower()


__all__ = ["TS_EXTENSIONS", "module_name_for", "parse_typescript"]

_ = (Attr, Call)
