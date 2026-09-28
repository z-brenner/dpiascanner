"""Graph types: nodes, edges, evidence, and the serialized data-flow graph.

Node kinds follow the data model: ``source``, ``transform``, ``sink``, ``retention``.
Intermediate propagation steps are not nodes; they are the evidence chain on an edge.
Node and edge ids are derived from stable properties (file, span, symbol, field, rule), not
from traversal order, so the same code produces the same ids across runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import networkx as nx

NodeKind = Literal["source", "transform", "sink", "retention"]
GRAPH_SCHEMA = "lantern.graph/v1"


def stable_id(prefix: str, *parts: object) -> str:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12]}"


@dataclass(frozen=True, order=True)
class Span:
    file: str
    start_line: int
    end_line: int
    start_byte: int = 0
    end_byte: int = 0

    def contains(self, other: Span) -> bool:
        return (
            self.file == other.file
            and self.start_byte <= other.start_byte
            and other.end_byte <= self.end_byte
        )

    def to_dict(self) -> dict[str, Any]:
        return {"file": self.file, "line_start": self.start_line, "line_end": self.end_line}


@dataclass(frozen=True)
class Step:
    """One propagation step: where it happened and which syntactic form carried the value."""

    file: str
    line: int
    kind: str
    text: str
    unresolved: bool = False
    note: str = ""
    function: str = ""

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "file": self.file,
            "line": self.line,
            "kind": self.kind,
            "text": self.text,
        }
        if self.unresolved:
            data["unresolved"] = True
        if self.note:
            data["note"] = self.note
        if self.function:
            data["function"] = self.function
        return data


@dataclass
class Node:
    id: str
    kind: NodeKind
    file: str
    line_start: int
    line_end: int
    symbol: str
    language: str
    snippet: str = ""
    snippet_hash: str = ""
    rule_ids: list[str] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Node:
        return cls(**dict(data))


@dataclass
class Edge:
    id: str
    from_node: str
    to_node: str
    kind: str  # "flow" carries data; "persists" links a sink to its retention node
    evidence: list[Step] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "from_node": self.from_node,
            "to_node": self.to_node,
            "kind": self.kind,
            "evidence": [s.to_dict() for s in self.evidence],
            "attrs": self.attrs,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Edge:
        return cls(
            id=data["id"],
            from_node=data["from_node"],
            to_node=data["to_node"],
            kind=data["kind"],
            evidence=[Step(**s) for s in data.get("evidence", [])],
            attrs=dict(data.get("attrs", {})),
        )


@dataclass
class Flow:
    """A complete source-to-sink path through zero or more transform nodes."""

    id: str
    node_ids: list[str]
    edge_ids: list[str]
    reachable: bool
    unresolved: bool
    mitigated: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Flow:
        return cls(**dict(data))


@dataclass
class DataFlowGraph:
    repo: str
    commit: str
    languages: list[str]
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)
    flows: list[Flow] = field(default_factory=list)
    config: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def add_node(self, node: Node) -> Node:
        existing = self.nodes.get(node.id)
        if existing is not None:
            for rule in node.rule_ids:
                if rule not in existing.rule_ids:
                    existing.rule_ids.append(rule)
            return existing
        self.nodes[node.id] = node
        return node

    def add_edge(self, edge: Edge) -> Edge:
        existing = self.edges.get(edge.id)
        if existing is not None:
            return existing
        self.edges[edge.id] = edge
        return edge

    def nodes_of(self, kind: NodeKind) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def to_networkx(self) -> nx.DiGraph[str]:
        g: nx.DiGraph[str] = nx.DiGraph()
        for node in self.nodes.values():
            g.add_node(node.id, **node.to_dict())
        for edge in self.edges.values():
            g.add_edge(edge.from_node, edge.to_node, **edge.to_dict())
        return g

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": GRAPH_SCHEMA,
            "repo": self.repo,
            "commit": self.commit,
            "languages": self.languages,
            "options": self.options,
            "nodes": [n.to_dict() for n in sorted(self.nodes.values(), key=lambda n: n.id)],
            "edges": [e.to_dict() for e in sorted(self.edges.values(), key=lambda e: e.id)],
            "flows": [f.to_dict() for f in sorted(self.flows, key=lambda f: f.id)],
            "config": self.config,
            "summary": self.summary,
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DataFlowGraph:
        if data.get("schema") != GRAPH_SCHEMA:
            raise ValueError(f"not a {GRAPH_SCHEMA} document")
        graph = cls(
            repo=data["repo"],
            commit=data["commit"],
            languages=list(data["languages"]),
            config=list(data.get("config", [])),
            summary=dict(data.get("summary", {})),
            options=dict(data.get("options", {})),
        )
        for n in data["nodes"]:
            graph.nodes[n["id"]] = Node.from_dict(n)
        for e in data["edges"]:
            graph.edges[e["id"]] = Edge.from_dict(e)
        graph.flows = [Flow.from_dict(f) for f in data.get("flows", [])]
        return graph

    @classmethod
    def from_json(cls, text: str) -> DataFlowGraph:
        return cls.from_dict(json.loads(text))

    def flows_through(self, node_ids: Iterable[str]) -> list[Flow]:
        wanted = set(node_ids)
        return [f for f in self.flows if wanted & set(f.node_ids)]
