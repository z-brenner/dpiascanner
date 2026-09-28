"""Shared helpers for tree-sitter lowering."""

from __future__ import annotations

import re
from collections.abc import Iterator

from tree_sitter import Node as TSNode

from lantern_analysis.ir import Compound, Expr, Literal
from lantern_analysis.model import Span

_WS = re.compile(r"\s+")
MAX_TEXT = 240


class Lowerer:
    """Base class holding the file context and span/text helpers."""

    def __init__(self, file: str, source: bytes) -> None:
        self.file = file
        self.source = source
        self.errors = 0

    def span(self, node: TSNode) -> Span:
        return Span(
            file=self.file,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
        )

    def raw(self, node: TSNode) -> str:
        return self.source[node.start_byte : node.end_byte].decode("utf-8", "replace")

    def text(self, node: TSNode) -> str:
        value = _WS.sub(" ", self.raw(node)).strip()
        return value if len(value) <= MAX_TEXT else value[: MAX_TEXT - 3] + "..."

    def cid(self, node: TSNode) -> str:
        return f"{self.file}:{node.start_byte}-{node.end_byte}"

    def unknown(self, node: TSNode, parts: list[Expr]) -> Expr:
        return Compound(self.span(node), self.text(node), "other", parts)

    def literal(self, node: TSNode, value: object) -> Literal:
        return Literal(self.span(node), self.text(node), value)


def named_children(node: TSNode) -> Iterator[TSNode]:
    for child in node.children:
        if child.is_named and child.type != "comment":
            yield child


def child(node: TSNode, field: str) -> TSNode | None:
    return node.child_by_field_name(field)


def unquote(raw: str) -> str:
    """Strip Python/JS string prefixes and quotes; leaves escape sequences as written."""
    value = raw
    prefix = re.match(r"^[rbuRBUfF]{0,3}(?=[\"'`])", value)
    if prefix is None:
        return value
    value = value[prefix.end() :]
    for quote in ('"""', "'''", '"', "'", "`"):
        if value.startswith(quote) and value.endswith(quote) and len(value) >= 2 * len(quote):
            return value[len(quote) : -len(quote)]
    return value
