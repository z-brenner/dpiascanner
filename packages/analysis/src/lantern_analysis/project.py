"""Whole-program view: modules, symbol lookup, light type inference, and call resolution.

Resolution is static and deliberately conservative. When a callee or type cannot be
determined, the answer is ``unknown`` or ``dynamic``, and the taint engine decides how to
propagate (conservatively) and whether to mark the step unresolved.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from lantern_analysis.ir import (
    Assign,
    Attr,
    Call,
    Class,
    Compound,
    Container,
    Expr,
    ExprStmt,
    FuncRef,
    Function,
    Import,
    Module,
    Name,
    NameT,
    Stmt,
    Target,
    target_names,
)
from lantern_analysis.parsing.python import module_name_for as py_module_name
from lantern_analysis.parsing.python import parse_python
from lantern_analysis.parsing.typescript import module_name_for as ts_module_name
from lantern_analysis.parsing.typescript import parse_typescript

PY_BUILTINS = frozenset(
    {
        "len",
        "bool",
        "isinstance",
        "issubclass",
        "hasattr",
        "type",
        "id",
        "callable",
        "hash",
        "str",
        "int",
        "float",
        "repr",
        "format",
        "bytes",
        "bytearray",
        "list",
        "dict",
        "tuple",
        "set",
        "frozenset",
        "sorted",
        "reversed",
        "enumerate",
        "zip",
        "map",
        "filter",
        "min",
        "max",
        "sum",
        "abs",
        "round",
        "print",
        "open",
        "range",
        "iter",
        "next",
        "getattr",
        "setattr",
        "delattr",
        "vars",
        "dir",
        "eval",
        "exec",
        "compile",
        "super",
        "object",
        "Exception",
        "ValueError",
        "KeyError",
        "TypeError",
        "RuntimeError",
        "any",
        "all",
        "ord",
        "chr",
        "divmod",
        "pow",
        "input",
        "memoryview",
        "slice",
        "staticmethod",
        "classmethod",
        "property",
        "NotImplementedError",
        "PermissionError",
        "LookupError",
        "OSError",
    }
)
TS_BUILTINS = frozenset(
    {
        "String",
        "Number",
        "Boolean",
        "Object",
        "Array",
        "JSON",
        "Math",
        "Date",
        "Promise",
        "console",
        "parseInt",
        "parseFloat",
        "isNaN",
        "isFinite",
        "encodeURIComponent",
        "decodeURIComponent",
        "encodeURI",
        "decodeURI",
        "Error",
        "TypeError",
        "RangeError",
        "Map",
        "Set",
        "WeakMap",
        "Symbol",
        "BigInt",
        "Buffer",
        "process",
        "setTimeout",
        "setInterval",
        "clearTimeout",
        "require",
        "eval",
        "Reflect",
        "Proxy",
        "globalThis",
        "structuredClone",
        "fetch",
        "URL",
        "URLSearchParams",
        "RegExp",
        "btoa",
        "atob",
        "undefined",
        "NaN",
        "Infinity",
        "escape",
        "unescape",
        "queueMicrotask",
    }
)
EXCEPTION_BASES = frozenset(
    {
        "Exception",
        "BaseException",
        "Error",
        "ValueError",
        "RuntimeError",
        "LookupError",
        "KeyError",
        "TypeError",
        "HTTPException",
        "PermissionError",
    }
)

# ---------------------------------------------------------------------------- symbols


@dataclass(frozen=True)
class Local:
    fid: str
    name: str


@dataclass(frozen=True)
class Global:
    mid: str
    name: str


@dataclass(frozen=True)
class FuncSym:
    fid: str


@dataclass(frozen=True)
class ClassSym:
    cid: str


@dataclass(frozen=True)
class ModuleSym:
    mid: str


@dataclass(frozen=True)
class ExternalSym:
    spec: str  # python top-level module path or npm specifier
    qual: str  # "" for the module itself


@dataclass(frozen=True)
class BuiltinSym:
    name: str


@dataclass(frozen=True)
class UnknownSym:
    name: str


Sym = Local | Global | FuncSym | ClassSym | ModuleSym | ExternalSym | BuiltinSym | UnknownSym

# ---------------------------------------------------------------------------- types


@dataclass(frozen=True)
class ClassT:
    cid: str


@dataclass(frozen=True)
class ExtT:
    spec: str
    qual: str


@dataclass(frozen=True)
class ModT:
    mid: str


@dataclass(frozen=True)
class ObjT:
    props: tuple[tuple[str, TypeRef | None], ...]

    def get(self, name: str) -> TypeRef | None:
        for key, value in self.props:
            if key == name:
                return value
        return None


TypeRef = ClassT | ExtT | ModT | ObjT

# ---------------------------------------------------------------------------- calls


@dataclass(frozen=True)
class CallTarget:
    kind: str  # function | method | constructor | external | builtin | dynamic | unknown | super
    fid: str | None = None
    cid: str | None = None
    spec: str | None = None
    qual: str | None = None
    method: str | None = None
    is_constructor: bool = False
    note: str = ""

    @property
    def external(self) -> str | None:
        return f"{self.spec}:{self.qual}" if self.spec is not None else None

    def import_path(self, language: str) -> str | None:
        """The path a registry matches against: python.dotted.path or an npm specifier."""
        if self.spec is None:
            return None
        if language == "python" and self.qual:
            return f"{self.spec}.{self.qual}".replace("()", "")
        return self.spec


_TYPE_WRAPPERS = re.compile(
    r"^(?:Optional|Annotated|Mapped|Promise|Readonly|Awaitable|Type|type)\[(.*)\]$|^(?:Promise|Readonly)<(.*)>$"
)


def _core_type_name(annotation: str) -> str | None:
    """'Optional[ContactRepository]' -> 'ContactRepository'; 'X | None' -> 'X'."""
    text = annotation.strip().strip('"').strip("'")
    for _ in range(4):
        m = _TYPE_WRAPPERS.match(text)
        if not m:
            break
        text = (m.group(1) or m.group(2) or "").strip()
    parts = [
        p.strip() for p in re.split(r"\|", text) if p.strip() not in ("None", "null", "undefined")
    ]
    if len(parts) != 1:
        return None
    text = parts[0].split("[")[0].split("<")[0].strip()
    return text if re.match(r"^[A-Za-z_][\w.]*$", text) else None


@dataclass
class Project:
    root: Path
    modules: dict[str, Module] = field(default_factory=dict)
    by_file: dict[str, Module] = field(default_factory=dict)
    functions: dict[str, Function] = field(default_factory=dict)
    classes: dict[str, Class] = field(default_factory=dict)
    py_index: dict[str, str] = field(default_factory=dict)
    locals_of: dict[str, set[str]] = field(default_factory=dict)
    assigns_of: dict[tuple[str, str], list[Expr]] = field(default_factory=dict)
    globals_of: dict[str, set[str]] = field(default_factory=dict)
    fn_by_start: dict[tuple[str, int], str] = field(default_factory=dict)
    _type_cache: dict[tuple[str, int], TypeRef | None] = field(default_factory=dict)

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, root: Path, files: dict[str, list[str]]) -> Project:
        project = cls(root=root)
        for rel in files.get("python", []):
            project.add(parse_python(rel, (root / rel).read_bytes()))
        for rel in files.get("typescript", []):
            project.add(parse_typescript(rel, (root / rel).read_bytes()))
        project.index()
        return project

    def add(self, module: Module) -> None:
        self.modules[module.mid] = module
        self.by_file[module.file] = module
        self.functions.update(module.functions)
        self.classes.update(module.classes)

    def index(self) -> None:
        for mid, module in self.modules.items():
            if module.language == "python":
                self.py_index[mid] = mid
                for alias in self._python_aliases(module.file):
                    self.py_index.setdefault(alias, mid)
        for fn in self.functions.values():
            names = {p.name for p in fn.params}
            for p in fn.params:
                if p.pattern is not None:
                    names.update(target_names(p.pattern))
            for stmt in fn.body:
                for tgt, value in _assigned(stmt):
                    for name in target_names(tgt):
                        names.add(name)
                        if value is not None and isinstance(tgt, NameT):
                            self.assigns_of.setdefault((fn.fid, name), []).append(value)
            if fn.kind == "module":
                self.globals_of[fn.module] = names
            self.locals_of[fn.fid] = names
            self.fn_by_start[(fn.span.file, fn.span.start_byte)] = fn.fid

    def _python_aliases(self, file: str) -> list[str]:
        """Import names for a module under a source root such as src/ or backend/.

        The source root is the parent of the outermost directory chain that contains
        __init__.py files, so backend/app/models.py (with backend/app/__init__.py) is
        importable as app.models.
        """
        parts = file.split("/")
        package_depth = 0
        for i in range(len(parts) - 1, 0, -1):
            if (self.root / "/".join(parts[:i]) / "__init__.py").is_file():
                package_depth += 1
            else:
                break
        root_len = len(parts) - 1 - package_depth
        aliases = []
        if root_len > 0:
            aliases.append(py_module_name("/".join(parts[root_len:])))
        if parts[0] == "src" and len(parts) > 1:
            aliases.append(py_module_name("/".join(parts[1:])))
        return [a for a in aliases if a]

    def module_of(self, fn: Function) -> Module:
        return self.modules[fn.module]

    def language(self, fn: Function) -> str:
        return self.modules[fn.module].language

    # ------------------------------------------------------------------ imports

    def resolve_import(self, module: Module, imp: Import) -> Sym:
        if module.language == "python":
            return self._resolve_py_import(module, imp)
        return self._resolve_ts_import(module, imp)

    def _resolve_py_import(self, module: Module, imp: Import) -> Sym:
        base = imp.module
        if imp.level:
            package = module.mid.split(".")
            if not module.file.endswith("__init__.py"):
                package = package[:-1]
            package = package[: len(package) - (imp.level - 1)] if imp.level > 1 else package
            base = ".".join([*package, imp.module] if imp.module else package)
        if imp.name is None:
            target = self.py_index.get(base)
            if target is not None:
                return ModuleSym(target)
            return ExternalSym(base.split(".")[0], ".".join(base.split(".")[1:]))
        sub = self.py_index.get(f"{base}.{imp.name}")
        if sub is not None:
            return ModuleSym(sub)
        target = self.py_index.get(base)
        if target is not None:
            return self.module_symbol(target, imp.name)
        top, _, rest = base.partition(".")
        return ExternalSym(top, f"{rest}.{imp.name}" if rest else imp.name)

    def _resolve_ts_module(self, module: Module, spec: str) -> str | None:
        if not spec.startswith("."):
            return None
        joined = posixpath.normpath(posixpath.join(posixpath.dirname(module.file), spec))
        candidates = [ts_module_name(joined), ts_module_name(joined + ".ts"), joined]
        for cand in candidates:
            if cand in self.modules:
                return cand
            if f"{cand}/index" in self.modules:
                return f"{cand}/index"
        return None

    def _resolve_ts_import(self, module: Module, imp: Import) -> Sym:
        target = self._resolve_ts_module(module, imp.module)
        if target is None:
            if imp.module.startswith("."):
                # A relative import we cannot see (generated or excluded code).
                return ExternalSym(imp.module, imp.name or "")
            return ExternalSym(imp.module, imp.name or "")
        if imp.name is None:
            return ModuleSym(target)
        if imp.name == "default":
            local = self.modules[target].default_export
            return self.module_symbol(target, local) if local else UnknownSym("default")
        return self.module_symbol(target, imp.name)

    def module_symbol(self, mid: str, name: str, depth: int = 0) -> Sym:
        module = self.modules.get(mid)
        if module is None or depth > 8:
            return UnknownSym(name)
        if module.language == "typescript":
            name = module.exports.get(name, name)
        fid = f"{mid}:{name}"
        if fid in self.functions and self.functions[fid].kind in ("function", "lambda"):
            return FuncSym(fid)
        if fid in self.classes:
            return ClassSym(fid)
        if name in self.globals_of.get(mid, set()):
            return Global(mid, name)
        imp = module.imports.get(name)
        if imp is not None:
            resolved = self.resolve_import(module, imp)
            if isinstance(resolved, Global | FuncSym | ClassSym | ModuleSym | ExternalSym):
                return resolved
        if module.language == "python" and f"{mid}.{name}" in self.py_index:
            return ModuleSym(self.py_index[f"{mid}.{name}"])
        return UnknownSym(name)

    # ------------------------------------------------------------------ names

    def lookup(self, fn: Function, name: str) -> Sym:
        current: Function | None = fn
        while current is not None:
            if current.kind == "module":
                break
            if name in self.locals_of.get(current.fid, set()):
                return Local(current.fid, name)
            current = self.functions.get(current.parent) if current.parent else None
        sym = self.module_symbol(fn.module, name)
        if not isinstance(sym, UnknownSym):
            return sym
        builtins = PY_BUILTINS if self.language(fn) == "python" else TS_BUILTINS
        if name in builtins:
            return BuiltinSym(name)
        return sym

    def values_of(self, sym: Sym) -> list[Expr]:
        if isinstance(sym, Local):
            return self.assigns_of.get((sym.fid, sym.name), [])
        if isinstance(sym, Global):
            module = self.modules[sym.mid]
            return self.assigns_of.get((module.module_fn, sym.name), [])
        return []

    def param_of(self, sym: Sym) -> tuple[Function, int] | None:
        if not isinstance(sym, Local):
            return None
        fn = self.functions[sym.fid]
        for i, p in enumerate(fn.params):
            if p.name == sym.name:
                return fn, i
        return None

    # ------------------------------------------------------------------ classes

    def class_bases(self, cls: Class) -> list[Sym]:
        owner = self.functions.get(self.modules[cls.module].module_fn)
        out: list[Sym] = []
        for base in cls.bases:
            if owner is None:
                continue
            sym = self.expr_symbol(owner, base)
            out.append(sym)
        return out

    def mro(self, cid: str) -> list[str]:
        order: list[str] = []
        stack = [cid]
        while stack:
            current = stack.pop(0)
            if current in order or current not in self.classes:
                continue
            order.append(current)
            for base in self.class_bases(self.classes[current]):
                if isinstance(base, ClassSym):
                    stack.append(base.cid)
        return order

    def find_method(self, cid: str, name: str) -> str | None:
        for c in self.mro(cid):
            fid = self.classes[c].methods.get(name)
            if fid is not None:
                return fid
        return None

    def is_exception_class(self, cid: str) -> bool:
        for c in self.mro(cid):
            cls = self.classes[c]
            if cls.name.endswith(("Error", "Exception")):
                return True
            for base in cls.bases:
                text = base.text.split(".")[-1]
                if text in EXCEPTION_BASES:
                    return True
        return False

    def constructor_of(self, cid: str) -> str | None:
        return self.find_method(cid, "__init__") or self.find_method(cid, "constructor")

    def field_order(self, cid: str) -> list[str]:
        names: list[str] = []
        for c in reversed(self.mro(cid)):
            for name in self.classes[c].fields:
                if not name.startswith("__") and name not in names:
                    names.append(name)
        return names

    # ------------------------------------------------------------------ expressions

    def expr_symbol(self, fn: Function, expr: Expr) -> Sym:
        """What a Name or dotted Attr chain refers to, statically."""
        if isinstance(expr, Name):
            if expr.id in ("self", "this") and fn.cls is not None:
                return Local(fn.fid, expr.id)
            return self.lookup(fn, expr.id)
        if isinstance(expr, Attr):
            base = self.expr_symbol(fn, expr.base)
            if isinstance(base, ModuleSym):
                return self.module_symbol(base.mid, expr.attr)
            if isinstance(base, ExternalSym):
                return ExternalSym(
                    base.spec, f"{base.qual}.{expr.attr}" if base.qual else expr.attr
                )
            if isinstance(base, ClassSym):
                fid = self.find_method(base.cid, expr.attr)
                if fid is not None:
                    return FuncSym(fid)
        return UnknownSym(expr.text)

    def resolve_annotation(self, fn: Function, annotation: str | None) -> TypeRef | None:
        if not annotation:
            return None
        name = _core_type_name(annotation)
        if name is None:
            return None
        parts = name.split(".")
        sym = self.lookup(fn, parts[0])
        for part in parts[1:]:
            if isinstance(sym, ModuleSym):
                sym = self.module_symbol(sym.mid, part)
            elif isinstance(sym, ExternalSym):
                sym = ExternalSym(sym.spec, f"{sym.qual}.{part}" if sym.qual else part)
            else:
                return None
        if isinstance(sym, ClassSym):
            return ClassT(sym.cid)
        if isinstance(sym, ExternalSym):
            return ExtT(sym.spec, f"{sym.qual}()" if sym.qual else "()")
        return None

    def infer_type(self, fn: Function, expr: Expr, depth: int = 0) -> TypeRef | None:
        key = (fn.fid, id(expr))
        if key in self._type_cache:
            return self._type_cache[key]
        self._type_cache[key] = None  # break cycles
        result = self._infer(fn, expr, depth) if depth < 12 else None
        self._type_cache[key] = result
        return result

    def _infer(self, fn: Function, expr: Expr, depth: int) -> TypeRef | None:
        if isinstance(expr, Name):
            sym = (
                Local(fn.fid, expr.id)
                if expr.id in ("self", "this") and fn.cls is not None
                else self.lookup(fn, expr.id)
            )
            return self.sym_type(sym, depth)
        if isinstance(expr, Attr):
            base = self.infer_type(fn, expr.base, depth + 1)
            if base is None:
                sym = self.expr_symbol(fn, expr)
                return self.sym_type(sym, depth) if not isinstance(sym, UnknownSym) else None
            return self.member_type(base, expr.attr, depth)
        if isinstance(expr, Call):
            target = self.resolve_call(fn, expr, depth + 1)
            if target.kind == "constructor" and target.cid:
                return ClassT(target.cid)
            if target.kind == "external" and target.spec is not None:
                return ExtT(target.spec, f"{target.qual}()")
            if target.kind in ("function", "method") and target.fid:
                callee = self.functions[target.fid]
                annotated = self.resolve_annotation(callee, callee.returns)
                if annotated is not None:
                    return annotated
                for stmt in callee.body:
                    if type(stmt).__name__ == "Return" and getattr(stmt, "value", None) is not None:
                        inferred = self.infer_type(callee, stmt.value, depth + 1)  # type: ignore[attr-defined]
                        if inferred is not None:
                            return inferred
            return None
        if (
            isinstance(expr, Compound)
            and expr.op in ("await", "paren", "as", "nonnull")
            and expr.parts
        ):
            return self.infer_type(fn, expr.parts[0], depth + 1)
        if isinstance(expr, Container) and expr.kind == "object":
            return ObjT(
                tuple(
                    (k, self.infer_type(fn, v, depth + 1)) for k, v in expr.items if k is not None
                )
            )
        return None

    def sym_type(self, sym: Sym, depth: int = 0) -> TypeRef | None:
        if isinstance(sym, ClassSym):
            return None  # the class object itself, not an instance
        if isinstance(sym, ModuleSym):
            return ModT(sym.mid)
        if isinstance(sym, ExternalSym):
            return ExtT(sym.spec, sym.qual)
        if isinstance(sym, Local):
            fn = self.functions[sym.fid]
            if sym.name in ("self", "this") and fn.cls is not None:
                return ClassT(fn.cls)
            for p in fn.params:
                if p.name == sym.name:
                    annotated = self.resolve_annotation(fn, p.annotation)
                    if annotated is not None:
                        return annotated
            for value in self.assigns_of.get((sym.fid, sym.name), []):
                t = self.infer_type(fn, value, depth + 1)
                if t is not None:
                    return t
            return None
        if isinstance(sym, Global):
            module = self.modules[sym.mid]
            owner = self.functions[module.module_fn]
            for value in self.assigns_of.get((owner.fid, sym.name), []):
                t = self.infer_type(owner, value, depth + 1)
                if t is not None:
                    return t
        return None

    def member_type(self, base: TypeRef, attr: str, depth: int = 0) -> TypeRef | None:
        if isinstance(base, ModT):
            return self.sym_type(self.module_symbol(base.mid, attr), depth + 1)
        if isinstance(base, ExtT):
            return ExtT(base.spec, f"{base.qual}.{attr}" if base.qual else attr)
        if isinstance(base, ObjT):
            return base.get(attr)
        if isinstance(base, ClassT):
            return self.field_type(base.cid, attr, depth)
        return None

    def field_type(self, cid: str, attr: str, depth: int = 0) -> TypeRef | None:
        for c in self.mro(cid):
            cls = self.classes[c]
            owner_module = self.modules[cls.module]
            owner = self.functions[owner_module.module_fn]
            field_def = cls.fields.get(attr)
            if field_def is not None:
                annotated = self.resolve_annotation(owner, field_def.annotation)
                if annotated is not None:
                    return annotated
                if field_def.default is not None:
                    t = self.infer_type(owner, field_def.default, depth + 1)
                    if t is not None:
                        return t
            for fid in cls.methods.values():
                method = self.functions[fid]
                for stmt in method.body:
                    if isinstance(stmt, Assign):
                        for tgt in stmt.targets:
                            if (
                                type(tgt).__name__ == "AttrT"
                                and isinstance(getattr(tgt, "base", None), Name)
                                and tgt.base.id in ("self", "this")  # type: ignore[attr-defined]
                                and tgt.attr == attr  # type: ignore[attr-defined]
                            ):
                                t = self.infer_type(method, stmt.value, depth + 1)
                                if t is not None:
                                    return t
        return None

    # ------------------------------------------------------------------ calls

    def resolve_call(self, fn: Function, call: Call, depth: int = 0) -> CallTarget:
        func = call.func
        if isinstance(func, FuncRef):
            return CallTarget("function", fid=func.fid)
        if isinstance(func, Name):
            if func.id == "super":
                return CallTarget("super")
            sym = self.lookup(fn, func.id)
            return self._target_for_symbol(fn, sym, func.id, call, depth)
        if isinstance(func, Attr):
            method = func.attr
            if (
                isinstance(func.base, Call)
                and isinstance(func.base.func, Name)
                and func.base.func.id == "super"
            ):
                return CallTarget("super", method=method)
            base_sym = self.expr_symbol(fn, func.base)
            if isinstance(base_sym, ModuleSym):
                return self._target_for_symbol(
                    fn, self.module_symbol(base_sym.mid, method), method, call, depth
                )
            if isinstance(base_sym, ClassSym):
                fid = self.find_method(base_sym.cid, method)
                if fid is not None:
                    return CallTarget("function", fid=fid, method=method)
            base_type = self.infer_type(fn, func.base, depth + 1) if depth < 10 else None
            if isinstance(base_type, ClassT):
                fid = self.find_method(base_type.cid, method)
                if fid is not None:
                    return CallTarget("method", fid=fid, cid=base_type.cid, method=method)
                return CallTarget("unknown", cid=base_type.cid, method=method)
            if isinstance(base_type, ModT):
                return self._target_for_symbol(
                    fn, self.module_symbol(base_type.mid, method), method, call, depth
                )
            if isinstance(base_type, ExtT):
                qual = f"{base_type.qual}.{method}" if base_type.qual else method
                return CallTarget(
                    "external",
                    spec=base_type.spec,
                    qual=qual,
                    method=method,
                    is_constructor=call.is_new or method[:1].isupper(),
                )
            if isinstance(base_type, ObjT):
                member = base_type.get(method)
                if isinstance(member, ExtT):
                    return CallTarget("external", spec=member.spec, qual=member.qual, method=method)
            return CallTarget("unknown", method=method)
        return CallTarget("dynamic")

    def _target_for_symbol(
        self, fn: Function, sym: Sym, name: str, call: Call, depth: int
    ) -> CallTarget:
        if isinstance(sym, FuncSym):
            return CallTarget("function", fid=sym.fid, method=name)
        if isinstance(sym, ClassSym):
            return CallTarget("constructor", cid=sym.cid, method=name, is_constructor=True)
        if isinstance(sym, ExternalSym):
            qual = sym.qual or name
            last = qual.split(".")[-1]
            return CallTarget(
                "external",
                spec=sym.spec,
                qual=qual,
                method=last,
                is_constructor=call.is_new or last[:1].isupper(),
            )
        if isinstance(sym, BuiltinSym):
            return CallTarget("builtin", method=sym.name, is_constructor=call.is_new)
        if isinstance(sym, Local | Global):
            for value in self.values_of(sym):
                if isinstance(value, FuncRef):
                    return CallTarget("function", fid=value.fid, method=name)
            if self.param_of(sym) is not None:
                return CallTarget("dynamic", method=name, note="callee is a parameter")
            t = self.sym_type(sym, depth + 1)
            if isinstance(t, ExtT):
                return CallTarget("external", spec=t.spec, qual=t.qual, method=name)
            return CallTarget("dynamic", method=name)
        return CallTarget("unknown", method=name)

    def iter_functions(self) -> Iterator[Function]:
        yield from self.functions.values()


def _assigned(stmt: Stmt) -> list[tuple[Target, Expr | None]]:
    if isinstance(stmt, Assign):
        return [(t, stmt.value) for t in stmt.targets]
    if type(stmt).__name__ == "ForLoop":
        return [(stmt.target, None)]  # type: ignore[attr-defined]
    if type(stmt).__name__ == "ExceptBind":
        return [(NameT(stmt.span, stmt.name), None)]  # type: ignore[attr-defined]
    if isinstance(stmt, ExprStmt):
        return []
    return []


def py_module_for_path(path: str) -> str:
    return py_module_name(path)
