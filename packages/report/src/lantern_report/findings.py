"""Combine graph paths and decisions into findings.

Every finding is anchored on a graph node (usually the sink; the source for special
category data; the retention node for retention findings), lists the nodes, edges, flows and
decisions that support it, and carries the commit, question set version, and provider so it
can be reproduced. A finding whose supporting decisions include one below the confidence
threshold is marked ``unresolved`` and reported separately with its evidence.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from lantern_analysis.model import DataFlowGraph, Edge, Flow, Node
from lantern_decisions.types import DecisionResult
from lantern_report.severity import SeverityInputs, SeverityTable, default_table
from lantern_report.statutes import statute_refs_for

DEFAULT_THRESHOLD = 0.75
THIRD_PARTY_TRANSFER = frozenset(
    {
        "analytics",
        "ad_tech",
        "unknown_third_party",
        "ai_model_provider",
        "communications",
        "cross_border",
    }
)
THIRD_PARTY = THIRD_PARTY_TRANSFER | {"payment_processor"}
LONG_RETENTION = frozenset({"years", "indefinite"})
CATEGORIES = (
    "personal_data_to_third_party",
    "special_category_processing",
    "reversible_obfuscation",
    "indefinite_retention",
    "logging_of_personal_data",
    "cross_border_ambiguity",
    "sale_or_share_candidate",
    "unresolved_flow",
    "unreachable_flow",
    "mitigated_partially",
    "personal_data_processing",
    "observed_unexpected_destination",
)
TITLES = {
    "personal_data_to_third_party": "Personal data sent to a third party",
    "special_category_processing": "Special category data processed",
    "reversible_obfuscation": "Personal data 'protected' with a reversible encoding",
    "indefinite_retention": "Personal data stored without an evident retention limit",
    "logging_of_personal_data": "Personal data written to logs",
    "cross_border_ambiguity": "Storage region is ambiguous",
    "sale_or_share_candidate": "Possible sale or sharing under the CPRA",
    "unresolved_flow": "Flow that could not be fully resolved",
    "unreachable_flow": "Personal data flow in unreachable code",
    "mitigated_partially": "Vendor mitigation covers only some paths",
    "personal_data_processing": "Personal data processed",
    "observed_unexpected_destination": "Request to a destination static analysis did not find",
}
# Canary kinds seen by dynamic verification, as data categories (question set v1 labels).
CANARY_DATA_CATEGORIES = {
    "email": "contact",
    "email_2": "contact",
    "phone": "contact",
    "name": "identifier",
    "ssn": "government_id",
    "dob": "demographic",
    "lat": "precise_location",
    "lng": "precise_location",
    "condition": "health",
    "payment_method": "financial",
}
_ANON = re.compile(r"@\d+(?::\d+)?")


def decision_id(target_id: str, question_set_id: str, question_id: str) -> str:
    digest = hashlib.sha256(f"{target_id}|{question_set_id}|{question_id}".encode()).hexdigest()
    return f"D-{digest[:12]}"


@dataclass
class UnresolvedReason:
    decision_id: str
    target_id: str
    question: str
    answer: str
    probability: float
    distribution: Mapping[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "distribution": dict(self.distribution)}


@dataclass
class Finding:
    id: str
    key: str
    category: str
    title: str
    severity: str
    severity_modifiers: list[str]
    status: str  # resolved | unresolved
    anchor_id: str
    node_ids: list[str]
    edge_ids: list[str]
    flow_ids: list[str]
    decision_ids: list[str]
    statute_refs: list[str]
    reachable: bool
    data_categories: list[str]
    destination_class: str | None
    classification: dict[str, Any]
    unresolved_reasons: list[UnresolvedReason] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    commit: str = ""
    question_set_version: str = ""
    provider: str = ""
    provider_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["unresolved_reasons"] = [r.to_dict() for r in self.unresolved_reasons]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Finding:
        values = dict(data)
        values["unresolved_reasons"] = [
            UnresolvedReason(**r) for r in values.get("unresolved_reasons", [])
        ]
        return cls(**values)


@dataclass
class FindingsResult:
    findings: list[Finding]
    unresolved_decisions: list[UnresolvedReason]
    threshold: float
    severity_version: str

    @property
    def resolved(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "resolved"]

    @property
    def unresolved(self) -> list[Finding]:
        return [f for f in self.findings if f.status == "unresolved"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "severity_version": self.severity_version,
            "findings": [f.to_dict() for f in self.findings],
            "unresolved_decisions": [r.to_dict() for r in self.unresolved_decisions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FindingsResult:
        return cls(
            findings=[Finding.from_dict(f) for f in data["findings"]],
            unresolved_decisions=[UnresolvedReason(**r) for r in data["unresolved_decisions"]],
            threshold=float(data["threshold"]),
            severity_version=str(data["severity_version"]),
        )


@dataclass
class _Partial:
    category: str
    anchor: Node
    flows: list[Flow] = field(default_factory=list)
    extra_nodes: set[str] = field(default_factory=set)
    support: set[tuple[str, str]] = field(default_factory=set)  # (target_id, question_id)
    evidence: dict[str, Any] = field(default_factory=dict)


class FindingsEngine:
    def __init__(
        self,
        graph: DataFlowGraph,
        decisions: Sequence[DecisionResult],
        threshold: float = DEFAULT_THRESHOLD,
        table: SeverityTable | None = None,
    ) -> None:
        self.g = graph
        self.threshold = threshold
        self.table = table or default_table()
        self.decisions: dict[tuple[str, str], DecisionResult] = {
            (d.target_id, d.question_id): d for d in decisions
        }
        self.partials: dict[tuple[str, str], _Partial] = {}
        first = decisions[0] if decisions else None
        self.meta = {
            "commit": graph.commit,
            "question_set_version": first.question_set_version if first else "",
            "provider": first.provider if first else "",
            "provider_version": first.provider_version if first else "",
        }

    # ------------------------------------------------------------------ helpers

    def d(self, target_id: str, question: str) -> DecisionResult | None:
        return self.decisions.get((target_id, question))

    def answer(self, target_id: str, question: str) -> str | None:
        decision = self.d(target_id, question)
        return decision.answer if decision is not None else None

    def _add(
        self,
        category: str,
        anchor: Node,
        flow: Flow | None,
        support: Iterable[tuple[str, str]],
        **evidence: Any,
    ) -> None:
        partial = self.partials.setdefault((category, anchor.id), _Partial(category, anchor))
        if flow is not None and all(f.id != flow.id for f in partial.flows):
            partial.flows.append(flow)
        partial.extra_nodes.add(anchor.id)
        partial.support.update(s for s in support if (s[0], s[1]) in self.decisions)
        for key, value in evidence.items():
            partial.evidence.setdefault(key, value)

    def _flow_parts(self, flow: Flow) -> tuple[Node, Node, Node | None, list[Edge]]:
        nodes = [self.g.nodes[i] for i in flow.node_ids]
        source = nodes[0]
        sink = next(n for n in nodes if n.kind == "sink")
        retention = next((n for n in nodes if n.kind == "retention"), None)
        edges = [
            self.g.edges[e]
            for e in flow.edge_ids
            if e in self.g.edges and self.g.edges[e].kind == "flow"
        ]
        return source, sink, retention, edges

    def _min_identifiability(self, source: Node, edges: list[Edge]) -> int | None:
        values = [self.answer(source.id, "identifiability")] + [
            self.answer(e.id, "residual_identifiability") for e in edges
        ]
        scores = [int(v) for v in values if v is not None and v.isdigit()]
        return min(scores) if scores else None

    # ------------------------------------------------------------------ rules

    def _per_flow(self, flow: Flow) -> None:
        source, sink, retention, edges = self._flow_parts(flow)
        category = self.answer(source.id, "data_category")
        if category is None or category == "none":
            return
        dest = self.answer(sink.id, "destination_class")
        base_support = {(source.id, "data_category")}
        path_support = (
            base_support
            | {(source.id, "identifiability")}
            | {(e.id, "residual_identifiability") for e in edges}
        )
        if not flow.reachable:
            self._add("unreachable_flow", sink, flow, base_support)
            return
        if flow.mitigated:
            return  # reported with its unmitigated siblings under mitigated_partially
        if flow.unresolved:
            unresolved_steps = [s.to_dict() for e in edges for s in e.evidence if s.unresolved]
            self._add(
                "unresolved_flow", sink, flow, base_support, unresolved_steps=unresolved_steps
            )
            return
        produced = False
        ident = self._min_identifiability(source, edges)
        if dest in THIRD_PARTY_TRANSFER and ident is not None and ident >= 3:
            self._add(
                "personal_data_to_third_party",
                sink,
                flow,
                path_support | {(sink.id, "destination_class")},
            )
            produced = True
        reversible = [
            e for e in edges if self.answer(e.id, "transformation") == "reversible_encoding"
        ]
        if reversible and (dest == "log" or dest in THIRD_PARTY):
            self._add(
                "reversible_obfuscation",
                sink,
                flow,
                {(e.id, "transformation") for e in reversible} | {(sink.id, "destination_class")},
                transforms=[
                    {
                        "node": e.to_node,
                        "file": self.g.nodes[e.to_node].file,
                        "line": self.g.nodes[e.to_node].line_start,
                    }
                    for e in reversible
                ],
            )
            produced = True
        if dest == "log" and ident is not None and ident >= 3:
            self._add(
                "logging_of_personal_data",
                sink,
                flow,
                path_support | {(sink.id, "destination_class")},
            )
            produced = True
        if self.answer(sink.id, "sale_or_share_cpra") == "yes":
            self._add(
                "sale_or_share_candidate",
                sink,
                flow,
                base_support | {(sink.id, "sale_or_share_cpra")},
            )
            produced = True
        if sink.attrs.get("config_conflict"):
            conflicting = [c for c in sink.attrs.get("config", []) if c.get("conflict")]
            self._add(
                "cross_border_ambiguity", sink, flow, base_support, conflicting_config=conflicting
            )
            produced = True
        if retention is not None:
            has_expiry = self.answer(retention.id, "has_expiry")
            plausible = self.answer(retention.id, "plausible_retention")
            if has_expiry == "no" and plausible in LONG_RETENTION:
                self._add(
                    "indefinite_retention",
                    retention,
                    flow,
                    base_support
                    | {(retention.id, "has_expiry"), (retention.id, "plausible_retention")},
                    retention_evidence=retention.attrs.get("retention_evidence", []),
                )
                produced = True
        if not produced:
            self._add(
                "personal_data_processing",
                sink,
                flow,
                base_support | {(sink.id, "destination_class")},
            )

    def _special_category(self) -> None:
        for node in self.g.nodes_of("source"):
            if self.answer(node.id, "special_category_art9") != "yes":
                continue
            flows = [f for f in self.g.flows if f.node_ids[0] == node.id]
            if not node.attrs.get("reachable", True) and not any(f.reachable for f in flows):
                continue
            support = {(node.id, "special_category_art9"), (node.id, "data_category")}
            if not flows:
                self._add("special_category_processing", node, None, support)
            for flow in flows:
                self._add("special_category_processing", node, flow, support)

    def _mitigated_partially(self) -> None:
        by_registry: dict[str, list[tuple[Flow, Node]]] = {}
        for flow in self.g.flows:
            if not flow.reachable:
                continue
            _, sink, _, _ = self._flow_parts(flow)
            registry = sink.attrs.get("registry") or {}
            if registry.get("id"):
                by_registry.setdefault(registry["id"], []).append((flow, sink))
        for registry_id, items in by_registry.items():
            mitigated = [(f, s) for f, s in items if f.mitigated]
            leaking = [(f, s) for f, s in items if not f.mitigated]
            if not mitigated or not leaking:
                continue
            anchor = leaking[0][1]
            hooks = mitigated[0][1].attrs.get("mitigations", [])
            for flow, sink in items:
                source = self.g.nodes[flow.node_ids[0]]
                self._add(
                    "mitigated_partially",
                    anchor,
                    flow,
                    {(source.id, "data_category"), (sink.id, "destination_class")},
                    registry_id=registry_id,
                    mitigation_hooks=hooks,
                    mitigated_sinks=sorted({s.id for _, s in mitigated}),
                    leaking_sinks=sorted({s.id for _, s in leaking}),
                )

    def _observed_unexpected(self) -> None:
        """Dynamic-only sink nodes: hosts the application contacted that no static sink names."""
        for node in sorted(self.g.nodes.values(), key=lambda n: n.id):
            dynamic = node.attrs.get("dynamic") or {}
            if dynamic.get("status") != "observed-unexpected":
                continue
            canaries = list(dynamic.get("canaries", []))
            self._add(
                "observed_unexpected_destination",
                node,
                None,
                (),
                host=(dynamic.get("hosts") or [""])[0],
                methods=dynamic.get("methods", []),
                paths=dynamic.get("paths", []),
                canaries=canaries,
                encodings=dynamic.get("encodings", []),
                body_field_names=dynamic.get("body_field_names", []),
                candidate_sinks=dynamic.get("candidate_sinks", []),
                observed_data_categories=sorted(
                    {CANARY_DATA_CATEGORIES[c] for c in canaries if c in CANARY_DATA_CATEGORIES}
                ),
            )

    # ------------------------------------------------------------------ assembly

    def run(self) -> FindingsResult:
        for flow in self.g.flows:
            self._per_flow(flow)
        self._special_category()
        self._mitigated_partially()
        self._observed_unexpected()
        findings = [self._finalize(p) for p in self.partials.values()]
        findings.sort(
            key=lambda f: (
                -self.table.rank(f.severity),
                CATEGORIES.index(f.category),
                f.key,
            )
        )
        for index, finding in enumerate(findings, 1):
            finding.id = f"F-{index:04d}"
        return FindingsResult(
            findings=findings,
            unresolved_decisions=self._unresolved_decisions(findings),
            threshold=self.threshold,
            severity_version=self.table.version,
        )

    def _finalize(self, p: _Partial) -> Finding:
        node_ids: list[str] = []
        edge_ids: list[str] = []
        for flow in p.flows:
            for n in flow.node_ids:
                if n not in node_ids:
                    node_ids.append(n)
            for e in flow.edge_ids:
                if e not in edge_ids:
                    edge_ids.append(e)
        for n in sorted(p.extra_nodes):
            if n not in node_ids:
                node_ids.append(n)
        sources = [self.g.nodes[n] for n in node_ids if self.g.nodes[n].kind == "source"]
        sinks = [self.g.nodes[n] for n in node_ids if self.g.nodes[n].kind == "sink"]
        data_categories = sorted(
            {a for s in sources if (a := self.answer(s.id, "data_category")) and a != "none"}
        )
        destination = self.answer(sinks[0].id, "destination_class") if sinks else None
        observed = p.evidence.get("observed_data_categories")
        dynamic_only = p.category == "observed_unexpected_destination"
        if dynamic_only:
            data_categories = list(observed or [])
            destination = "unknown_third_party" if observed else None
        flow_edges = [
            self.g.edges[e]
            for e in edge_ids
            if e in self.g.edges and self.g.edges[e].kind == "flow"
        ]
        idents = [self._min_identifiability(s, flow_edges) for s in sources]
        reachable = (
            any(f.reachable for f in p.flows)
            if p.flows
            else bool(p.anchor.attrs.get("reachable", True))
        )
        severity, modifiers = self.table.compute(
            p.category,
            SeverityInputs(
                special_category=any(
                    self.answer(s.id, "special_category_art9") == "yes" for s in sources
                ),
                relates_to_minor=any(
                    self.answer(s.id, "relates_to_minor") == "yes" for s in sources
                ),
                data_categories=frozenset(data_categories),
                destination_class=destination,
                min_identifiability=min((i for i in idents if i is not None), default=None),
                reachable=reachable,
                no_personal_data_observed=dynamic_only and not observed,
            ),
        )
        verification = {
            n.id: {k: n.attrs["dynamic"].get(k) for k in ("status", "reason", "hosts", "canaries")}
            for n in sinks
            if n.attrs.get("dynamic") and not dynamic_only
        }
        evidence = {**p.evidence, "verification": verification} if verification else p.evidence
        reasons = []
        for target_id, question in sorted(p.support):
            decision = self.decisions[(target_id, question)]
            if decision.probability < self.threshold:
                reasons.append(self._reason(decision))
        status = "unresolved" if reasons or p.category == "unresolved_flow" else "resolved"
        classification = {
            "data_categories": data_categories,
            "destination_class": destination,
            "transformations": sorted(
                {a for e in flow_edges if (a := self.answer(e.id, "transformation"))}
            ),
        }
        return Finding(
            id="",
            key=self._key(p.category, p.anchor),
            category=p.category,
            title=TITLES[p.category],
            severity=severity,
            severity_modifiers=modifiers,
            status=status,
            anchor_id=p.anchor.id,
            node_ids=node_ids,
            edge_ids=edge_ids,
            flow_ids=[f.id for f in p.flows],
            decision_ids=sorted(
                {
                    decision_id(t, self.decisions[(t, q)].question_set_id, q)
                    for t in [*node_ids, *edge_ids]
                    for q in self._questions_for(t)
                }
            ),
            statute_refs=statute_refs_for(p.category, data_categories, destination),
            reachable=reachable,
            data_categories=data_categories,
            destination_class=destination,
            classification=classification,
            unresolved_reasons=reasons,
            evidence=evidence,
            **self.meta,
        )

    def _questions_for(self, target_id: str) -> list[str]:
        return [q for (t, q) in self.decisions if t == target_id]

    def _reason(self, decision: DecisionResult) -> UnresolvedReason:
        return UnresolvedReason(
            decision_id=decision_id(
                decision.target_id, decision.question_set_id, decision.question_id
            ),
            target_id=decision.target_id,
            question=f"{decision.question_set_id}.{decision.question_id}",
            answer=decision.answer,
            probability=decision.probability,
            distribution=decision.distribution,
        )

    def _key(self, category: str, anchor: Node) -> str:
        symbol = _ANON.sub("", anchor.symbol)
        entry = anchor.attrs.get("entry") or {}
        route = f"{entry.get('method') or ''} {entry.get('path') or ''}".strip()
        parts = [category, anchor.kind, symbol, ",".join(sorted(anchor.rule_ids)), route]
        return "K-" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]

    def _unresolved_decisions(self, findings: list[Finding]) -> list[UnresolvedReason]:
        """Low-confidence decisions on reachable flows that no finding already reports."""
        covered = {r.decision_id for f in findings for r in f.unresolved_reasons}
        on_reachable = {i for f in self.g.flows if f.reachable for i in [*f.node_ids, *f.edge_ids]}
        out = []
        for (target_id, _question), decision in sorted(self.decisions.items()):
            if target_id not in on_reachable or decision.probability >= self.threshold:
                continue
            reason = self._reason(decision)
            if reason.decision_id not in covered:
                out.append(reason)
        return out


def assemble_findings(
    graph: DataFlowGraph,
    decisions: Sequence[DecisionResult],
    threshold: float = DEFAULT_THRESHOLD,
) -> FindingsResult:
    return FindingsEngine(graph, decisions, threshold).run()
