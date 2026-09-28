"""Retention evidence for persisting sinks: TTL fields and arguments, deletion jobs, schedules.

Evidence is attached, not judged. Whether data actually expires is a decision-layer question
(retention_v1.has_expiry); the analyzer supplies what it can see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lantern_analysis.detect import Detection, SinkSpec
from lantern_analysis.ir import (
    Attr,
    Call,
    Container,
    Expr,
    Function,
    Literal,
    Name,
    stmt_exprs,
    walk_expr,
)
from lantern_analysis.lexicon import tokenize
from lantern_analysis.project import ClassSym, Global, Project

TTL_TOKENS = frozenset(
    {"ttl", "expires", "expiry", "expiration", "expire", "retention", "purge", "delete", "deleted"}
)
TTL_KWARGS = frozenset(
    {"ex", "px", "exat", "pxat", "ttl", "timeout", "expire", "expires_in", "EX", "PX", "expiration"}
)
DELETE_METHODS = frozenset(
    {"delete", "deleteMany", "destroy", "remove", "purge", "delete_many", "deleteOne", "truncate"}
)
SCHEDULE_FILES = re.compile(
    r"(^|/)(crontab|[^/]*\.cron|cronjobs?\.ya?ml|vercel\.json|[^/]*schedule[^/]*\.ya?ml|celerybeat[^/]*)$"
)


@dataclass
class RetentionEvidence:
    kind: (
        str  # ttl_field | ttl_argument | ttl_constant | deletion_job | schedule | retention_config
    )
    file: str
    line: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "file": self.file, "line": self.line, "text": self.text}


def _has_ttl_token(name: str) -> bool:
    tokens = set(tokenize(name))
    return bool(tokens & TTL_TOKENS) or (
        name.lower().endswith(("_at", "at")) and bool(tokens & {"expires", "purge", "delete"})
    )


class RetentionScanner:
    def __init__(self, project: Project, detection: Detection) -> None:
        self.project = project
        self.d = detection
        self._deletions = self._find_deletions()
        self._schedules = self._find_schedules()

    def _find_deletions(self) -> list[tuple[str, Function, Call]]:
        out: list[tuple[str, Function, Call]] = []
        models = set(self.d.orm_models)
        lowered = {m[:1].lower() + m[1:]: m for m in models}
        for fn in self.project.functions.values():
            for stmt in fn.body:
                for root in stmt_exprs(stmt):
                    for e in walk_expr(root):
                        if not (
                            isinstance(e, Call)
                            and isinstance(e.func, Attr)
                            and e.func.attr in DELETE_METHODS
                        ):
                            continue
                        model = None
                        for inner in walk_expr(e.func.base):
                            if isinstance(inner, Name) and inner.id in models:
                                model = inner.id
                            elif isinstance(inner, Attr) and inner.attr in lowered:
                                model = lowered[inner.attr]
                        if model is None:
                            for arg in e.args:
                                t = self.project.infer_type(fn, arg)
                                if t is not None and type(t).__name__ == "ClassT":
                                    model = self.project.classes[t.cid].name  # type: ignore[union-attr]
                        if model is not None:
                            out.append((model, fn, e))
        return out

    def _find_schedules(self) -> list[tuple[str, int, str]]:
        out: list[tuple[str, int, str]] = []
        for path in sorted(self.project.root.rglob("*")):
            rel = path.relative_to(self.project.root).as_posix()
            if not path.is_file() or "node_modules" in rel or not SCHEDULE_FILES.search(rel):
                continue
            for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if line.strip() and not line.strip().startswith("#"):
                    out.append((rel, number, line.strip()))
        return out

    def _scheduled(self, fn: Function) -> list[RetentionEvidence]:
        module = self.project.modules[fn.module]
        needles = {module.mid, module.file, Path(module.file).stem, fn.name}
        hits = []
        for rel, number, text in self._schedules:
            if any(n and n in text for n in needles if len(n) > 3):
                hits.append(RetentionEvidence("schedule", rel, number, text[:200]))
        return hits

    def evidence_for(self, sink: SinkSpec) -> list[RetentionEvidence]:
        evidence: list[RetentionEvidence] = []
        fn = self.project.functions[sink.fid]
        call = sink.call
        for key, value in call.kwargs:
            if key in TTL_KWARGS:
                evidence.append(
                    RetentionEvidence(
                        "ttl_argument", call.span.file, value.span.start_line, f"{key}={value.text}"
                    )
                )
        written = self._written_values(fn, call)
        for field_name, value, span_line, file in written:
            if _has_ttl_token(field_name):
                evidence.append(
                    RetentionEvidence(
                        "ttl_field", file, span_line, f"{field_name}={value.text}"[:200]
                    )
                )
                evidence.extend(self._constant_definitions(fn, value))
        if sink.model:
            for model, del_fn, del_call in self._deletions:
                if model == sink.model:
                    evidence.append(
                        RetentionEvidence(
                            "deletion_job",
                            del_call.span.file,
                            del_call.span.start_line,
                            del_call.text[:200],
                        )
                    )
                    evidence.extend(self._scheduled(del_fn))
        seen: set[tuple[str, str, int]] = set()
        unique = []
        for item in evidence:
            dedupe_key = (item.kind, item.file, item.line)
            if dedupe_key not in seen:
                seen.add(dedupe_key)
                unique.append(item)
        return unique

    def _written_values(self, fn: Function, call: Call) -> list[tuple[str, Expr, int, str]]:
        """(field, value) pairs written by the sink: constructor kwargs or object literal keys."""
        out: list[tuple[str, Expr, int, str]] = []

        def from_expr(expr: Expr, depth: int = 0) -> None:
            if depth > 2:
                return
            if isinstance(expr, Container):
                for key, value in expr.items:
                    if key is not None:
                        out.append((key, value, value.span.start_line, value.span.file))
                        from_expr(value, depth + 1)
            elif isinstance(expr, Call):
                for key, value in expr.kwargs:
                    out.append((key, value, value.span.start_line, value.span.file))
                for arg in expr.args:
                    from_expr(arg, depth + 1)
            elif isinstance(expr, Name):
                for value in self.project.values_of(self.project.lookup(fn, expr.id)):
                    from_expr(value, depth + 1)

        for arg in call.args:
            from_expr(arg)
        for _, value in call.kwargs:
            from_expr(value)
        return out

    def _constant_definitions(self, fn: Function, value: Expr) -> list[RetentionEvidence]:
        out = []
        for e in walk_expr(value):
            if isinstance(e, Name):
                sym = self.project.lookup(fn, e.id)
                if isinstance(sym, Global):
                    for assigned in self.project.values_of(sym):
                        if isinstance(assigned, Call | Literal) or _has_ttl_token(e.id):
                            text = f"{e.id} = {assigned.text}"
                            out.append(
                                RetentionEvidence(
                                    "ttl_constant",
                                    assigned.span.file,
                                    assigned.span.start_line,
                                    text[:200],
                                )
                            )
                elif isinstance(sym, ClassSym):
                    continue
        return out
