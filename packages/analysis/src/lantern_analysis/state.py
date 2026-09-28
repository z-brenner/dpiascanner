"""Build the state payload the decision model sees for every classifiable graph target.

Payloads follow ``lantern.state/v1`` (see lantern_decisions.state). They are built from the
graph only: node snippets are already bounded to the target span plus a few lines of context
and already redacted, so no file contents outside that window can leak into a payload. Every
string is redacted again here as a second line of defense, and ``payload_guard`` rejects a
payload that still contains a secret.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from lantern_analysis.model import DataFlowGraph, Edge, Node, Step
from lantern_analysis.secrets import payload_guard, redact_value

STATE_SCHEMA = "lantern.state/v1"
MAX_SNIPPET_CHARS = 1600
MAX_STEPS = 20
MAX_TEXT = 240
MAX_LIST = 12
TRUNCATED = "...[truncated]"


@dataclass(frozen=True)
class Target:
    target_id: str
    target_type: str  # source | edge | sink | retention
    state: dict[str, Any]


def _bound(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: limit - len(TRUNCATED)] + TRUNCATED, True


def _chain(steps: list[Step]) -> list[str]:
    chain: list[str] = []
    for s in steps:
        if s.function and (not chain or chain[-1] != s.function):
            chain.append(s.function)
    return chain[:MAX_LIST]


def _step_dict(step: Step) -> dict[str, Any]:
    data = step.to_dict()
    data["text"], _ = _bound(str(data["text"]), MAX_TEXT)
    if "note" in data:
        data["note"], _ = _bound(str(data["note"]), MAX_TEXT)
    return data


class StateBuilder:
    def __init__(self, graph: DataFlowGraph) -> None:
        self.g = graph
        self.incoming: dict[str, list[Edge]] = defaultdict(list)
        self.flows_through: dict[str, list[str]] = defaultdict(list)
        for edge in graph.edges.values():
            self.incoming[edge.to_node].append(edge)
        for flow in graph.flows:
            for item in [*flow.node_ids, *flow.edge_ids]:
                self.flows_through[item].append(flow.id)
        self.flows = {f.id: f for f in graph.flows}

    def _common(self, node: Node, target_type: str) -> dict[str, Any]:
        snippet, truncated = _bound(node.snippet, MAX_SNIPPET_CHARS)
        flows = [self.flows[f] for f in self.flows_through.get(node.id, [])]
        return {
            "schema": STATE_SCHEMA,
            "target_type": target_type,
            "language": node.language,
            "symbol": node.symbol,
            "location": {
                "file": node.file,
                "line_start": node.line_start,
                "line_end": node.line_end,
            },
            "snippet": snippet,
            "snippet_truncated": truncated,
            "rule_ids": list(node.rule_ids),
            "flags": {
                "reachable": bool(node.attrs.get("reachable", True)),
                "unresolved": any(f.unresolved for f in flows),
                "inherited_unresolved": bool(node.attrs.get("inherited_unresolved")),
                "mitigated": any(f.mitigated for f in flows),
                "conflicting_config": bool(node.attrs.get("config_conflict")),
            },
        }

    def _source_categories(self, flow_ids: list[str]) -> list[str]:
        cats: set[str] = set()
        for fid in flow_ids:
            src = self.g.nodes[self.flows[fid].node_ids[0]]
            cats.update(h["category"] for h in src.attrs.get("lexicon_hints", []))
        return sorted(cats)

    def source(self, node: Node) -> Target:
        state = self._common(node, "source")
        state.update(
            {
                "field": node.attrs.get("field"),
                "source_kind": node.attrs.get("source_kind"),
                "model": node.attrs.get("model"),
                "lexicon_hints": node.attrs.get("lexicon_hints", [])[:MAX_LIST],
                "entry": node.attrs.get("entry"),
                "call_chain": [node.attrs["function"]] if node.attrs.get("function") else [],
            }
        )
        return Target(node.id, "source", state)

    def sink(self, node: Node) -> Target:
        state = self._common(node, "sink")
        steps = [s for e in self.incoming.get(node.id, []) for s in e.evidence]
        registry = node.attrs.get("registry")
        if registry is not None:
            registry = {
                **registry,
                "method": node.attrs.get("method"),
                "event_path": node.attrs.get("event_path"),
            }
        state.update(
            {
                "sink_family": node.attrs.get("family"),
                "model": node.attrs.get("model"),
                "registry": registry,
                "dependency": node.attrs.get("dependency"),
                "config": node.attrs.get("config", [])[:MAX_LIST],
                "mitigations": node.attrs.get("mitigations", [])[:MAX_LIST],
                "call_chain": _chain(steps) or [node.attrs.get("function", "")],
                "upstream_categories": self._source_categories(self.flows_through.get(node.id, [])),
            }
        )
        return Target(node.id, "sink", state)

    def retention(self, node: Node) -> Target:
        state = self._common(node, "retention")
        evidence = node.attrs.get("retention_evidence", [])[:MAX_LIST]
        state.update(
            {
                "store": node.attrs.get("store"),
                "model": node.attrs.get("model"),
                "retention_evidence": [
                    {**e, "text": _bound(str(e.get("text", "")), MAX_TEXT)[0]} for e in evidence
                ],
            }
        )
        return Target(node.id, "retention", state)

    def edge(self, edge: Edge) -> Target:
        upstream = self.g.nodes[edge.from_node]
        downstream = self.g.nodes[edge.to_node]
        steps = edge.evidence
        truncated = len(steps) > MAX_STEPS
        shown = steps[: MAX_STEPS // 2] + steps[-MAX_STEPS // 2 :] if truncated else steps
        transforms = []
        if downstream.kind == "transform":
            transforms.append(
                {
                    "rule_id": downstream.rule_ids[0] if downstream.rule_ids else "",
                    "kind": downstream.attrs.get("transform_kind"),
                    "file": downstream.file,
                    "line": downstream.line_start,
                }
            )
        flows = self.flows_through.get(edge.id, [])
        snippet, snippet_truncated = _bound(downstream.snippet, MAX_SNIPPET_CHARS)
        state: dict[str, Any] = {
            "schema": STATE_SCHEMA,
            "target_type": "edge",
            "language": upstream.language,
            "symbol": f"{upstream.symbol} -> {downstream.symbol}",
            "location": {
                "file": downstream.file,
                "line_start": downstream.line_start,
                "line_end": downstream.line_end,
            },
            "snippet": snippet,
            "snippet_truncated": snippet_truncated,
            "rule_ids": sorted({*upstream.rule_ids, *downstream.rule_ids}),
            "evidence_chain": [_step_dict(s) for s in shown],
            "evidence_truncated": truncated,
            "call_chain": _chain(steps),
            "transforms": transforms,
            "upstream": {
                "kind": upstream.kind,
                "symbol": upstream.symbol,
                "field": upstream.attrs.get("field"),
                "lexicon_categories": self._source_categories(flows),
                "transform_kind": upstream.attrs.get("transform_kind"),
            },
            "downstream": {
                "kind": downstream.kind,
                "symbol": downstream.symbol,
                "sink_family": downstream.attrs.get("family"),
                "transform_kind": downstream.attrs.get("transform_kind"),
            },
            "flags": {
                "reachable": any(self.flows[f].reachable for f in flows),
                "unresolved": bool(edge.attrs.get("unresolved")),
                "inherited_unresolved": any(self.flows[f].unresolved for f in flows),
            },
        }
        return Target(edge.id, "edge", state)

    def build(self) -> list[Target]:
        """Targets on at least one flow, in a stable order."""
        on_flow = {i for f in self.g.flows for i in [*f.node_ids, *f.edge_ids]}
        targets: list[Target] = []
        for node in sorted(self.g.nodes.values(), key=lambda n: n.id):
            if node.id not in on_flow:
                continue
            if node.kind == "source":
                targets.append(self.source(node))
            elif node.kind == "sink":
                targets.append(self.sink(node))
            elif node.kind == "retention":
                targets.append(self.retention(node))
        for edge in sorted(self.g.edges.values(), key=lambda e: e.id):
            if edge.kind == "flow" and edge.id in on_flow:
                targets.append(self.edge(edge))
        return [self._finalize(t) for t in targets]

    @staticmethod
    def _finalize(target: Target) -> Target:
        state = redact_value(target.state)
        payload_guard(state)
        return Target(target.target_id, target.target_type, state)


def build_targets(graph: DataFlowGraph) -> list[Target]:
    return StateBuilder(graph).build()
