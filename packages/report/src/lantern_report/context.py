"""What a report is built from, indexed for lookups.

``ReportContext`` holds the graph, findings, and decisions of one run and answers the
questions the report asks: which nodes a finding cites, where they are in the repository at
the commit, what the decision provider answered, and how to describe a finding without any
source code (``brief``), which is the only view of a finding the narrative drafter gets.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from lantern_analysis.model import DataFlowGraph, Edge, Node
from lantern_analysis.secrets import payload_guard, redact_value
from lantern_report.findings import Finding, FindingsResult, decision_id
from lantern_report.statutes import default_map


@dataclass(frozen=True)
class RunInfo:
    """Identity of the run a report describes."""

    repo: str  # display name, e.g. "acme/checkout"
    commit: str
    run_id: str = ""
    repo_url: str | None = None  # https://github.com/acme/checkout, for links at the commit
    path_prefix: str = ""  # subdirectory that was scanned, if not the repository root
    generated_at: str = field(
        default_factory=lambda: dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    )


class SourceLinker:
    """File and line links into the repository at the commit (GitHub URL layout)."""

    def __init__(self, repo_url: str | None, commit: str, path_prefix: str = "") -> None:
        self.repo_url = repo_url.rstrip("/") if repo_url else None
        self.commit = commit
        self.prefix = path_prefix.strip("/") + "/" if path_prefix.strip("/") else ""

    def url(self, file: str, line: int | None = None) -> str | None:
        if not self.repo_url or not file or file.startswith("<"):
            return None
        anchor = f"#L{line}" if line else ""
        return f"{self.repo_url}/blob/{self.commit}/{quote(self.prefix + file)}{anchor}"


@dataclass(frozen=True)
class DecisionRecord:
    id: str
    target_id: str
    question_set_id: str
    question_id: str
    answer: str
    probability: float
    distribution: dict[str, float]
    provider: str
    provider_version: str

    @property
    def question(self) -> str:
        return f"{self.question_set_id}.{self.question_id}"

    @classmethod
    def from_any(cls, obj: Any) -> DecisionRecord:
        """From a ``DecisionResult`` or a persisted decision row."""
        return cls(
            id=str(
                getattr(obj, "id", "")
                or decision_id(obj.target_id, obj.question_set_id, obj.question_id)
            ),
            target_id=str(obj.target_id),
            question_set_id=str(obj.question_set_id),
            question_id=str(obj.question_id),
            answer=str(obj.answer),
            probability=float(obj.probability),
            distribution={str(k): float(v) for k, v in dict(obj.distribution).items()},
            provider=str(obj.provider),
            provider_version=str(obj.provider_version),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "target_id": self.target_id,
            "question": self.question,
            "answer": self.answer,
            "probability": self.probability,
            "distribution": self.distribution,
            "provider": self.provider,
            "provider_version": self.provider_version,
        }


FIRST_PARTY_FAMILIES = {
    "orm": "the application's database",
    "cache": "the application's cache",
    "file": "files written by the application",
    "log": "application logs",
    "queue": "a message queue",
}


def _first_host(hosts: Iterable[str]) -> str | None:
    listed = list(hosts)
    concrete = [h for h in listed if not h.startswith("*.")]
    candidates = concrete or listed
    return candidates[0] if candidates else None


def destination_name(node: Node) -> str:
    """A human name for where a sink sends data. Never contains code."""
    registry = node.attrs.get("registry") or {}
    if registry.get("vendor"):
        return str(registry["vendor"])
    dependency = node.attrs.get("dependency") or {}
    if dependency.get("name"):
        return f"the {dependency['name']} package's service"
    dynamic = node.attrs.get("dynamic") or {}
    if dynamic.get("status") == "observed-unexpected":
        return str((dynamic.get("hosts") or ["an unknown host"])[0])
    host = _first_host((node.attrs.get("endpoints") or {}).get("hosts", []))
    if host:
        return host
    family = str(node.attrs.get("family") or "")
    if family in FIRST_PARTY_FAMILIES:
        model = node.attrs.get("model")
        base = FIRST_PARTY_FAMILIES[family]
        return f"{base} ({model} records)" if model and family == "orm" else base
    return "an external destination that static analysis could not name"


def is_third_party(node: Node | None) -> bool:
    """Whether a sink hands data to another party (not the application's own store or logs)."""
    if node is None:
        return False
    if node.attrs.get("registry") or node.attrs.get("dependency") or node.file == "<dynamic>":
        return True
    return node.attrs.get("family") == "http"


def entry_label(node: Node) -> str | None:
    entry = node.attrs.get("entry") or {}
    if entry.get("kind") == "route":
        return f"{entry.get('method') or 'ANY'} {entry.get('path') or '/'}"
    if entry.get("kind"):
        return str(entry["kind"]).replace("_", " ")
    return None


class ReportContext:
    def __init__(
        self,
        graph: DataFlowGraph,
        findings: FindingsResult,
        decisions: Sequence[Any],
        run: RunInfo,
    ) -> None:
        self.graph = graph
        self.findings = findings
        self.run = run
        self.linker = SourceLinker(run.repo_url, run.commit, run.path_prefix)
        self.records = [DecisionRecord.from_any(d) for d in decisions]
        self.by_target: dict[tuple[str, str], DecisionRecord] = {
            (d.target_id, d.question_id): d for d in self.records
        }
        self.by_decision_id = {d.id: d for d in self.records}
        self.by_finding = {f.id: f for f in findings.findings}
        self.statutes = default_map()

    # --- lookups -------------------------------------------------------------------------

    def finding(self, finding_id: str) -> Finding:
        return self.by_finding[finding_id]

    def node(self, node_id: str) -> Node:
        return self.graph.nodes[node_id]

    def edge(self, edge_id: str) -> Edge | None:
        return self.graph.edges.get(edge_id)

    def decision(self, target_id: str, question_id: str) -> DecisionRecord | None:
        return self.by_target.get((target_id, question_id))

    def sources(self, f: Finding) -> list[Node]:
        return [self.graph.nodes[n] for n in f.node_ids if self.graph.nodes[n].kind == "source"]

    def sinks(self, f: Finding) -> list[Node]:
        return [self.graph.nodes[n] for n in f.node_ids if self.graph.nodes[n].kind == "sink"]

    def anchor(self, f: Finding) -> Node:
        return self.graph.nodes[f.anchor_id]

    def primary_sink(self, f: Finding) -> Node | None:
        anchor = self.anchor(f)
        if anchor.kind == "sink":
            return anchor
        if anchor.kind == "retention" and anchor.attrs.get("sink_id") in self.graph.nodes:
            return self.graph.nodes[anchor.attrs["sink_id"]]
        sinks = self.sinks(f)
        return sinks[0] if sinks else None

    def entries(self, f: Finding) -> list[str]:
        labels = {label for s in self.sources(f) if (label := entry_label(s))}
        return sorted(labels)

    def destination(self, f: Finding) -> str | None:
        sink = self.primary_sink(f)
        return destination_name(sink) if sink is not None else None

    def verification(self, f: Finding) -> dict[str, Any] | None:
        sink = self.primary_sink(f)
        dynamic = (sink.attrs.get("dynamic") if sink is not None else None) or {}
        if not dynamic:
            return None
        return {k: dynamic.get(k) for k in ("status", "reason", "canaries", "hosts")}

    def retention(self, f: Finding) -> dict[str, Any] | None:
        node = next(
            (self.graph.nodes[n] for n in f.node_ids if self.graph.nodes[n].kind == "retention"),
            None,
        )
        if node is None:
            return None
        has_expiry = self.decision(node.id, "has_expiry")
        plausible = self.decision(node.id, "plausible_retention")
        return {
            "store": destination_name(self.graph.nodes[node.attrs["sink_id"]])
            if node.attrs.get("sink_id") in self.graph.nodes
            else node.attrs.get("store"),
            "has_expiry": has_expiry.answer if has_expiry else None,
            "plausible_retention": plausible.answer if plausible else None,
            "evidence_kinds": sorted(
                {e.get("kind", "") for e in node.attrs.get("retention_evidence", [])}
            ),
        }

    def sink_answer(self, f: Finding, question: str) -> DecisionRecord | None:
        sink = self.primary_sink(f)
        return self.decision(sink.id, question) if sink is not None else None

    def special_category(self, f: Finding) -> bool:
        return any(
            (d := self.decision(s.id, "special_category_art9")) is not None and d.answer == "yes"
            for s in self.sources(f)
        )

    def relates_to_minor(self, f: Finding) -> bool:
        return any(
            (d := self.decision(s.id, "relates_to_minor")) is not None and d.answer == "yes"
            for s in self.sources(f)
        )

    def fields(self, f: Finding) -> list[str]:
        return sorted({str(s.attrs["field"]) for s in self.sources(f) if s.attrs.get("field")})

    def fields_by_category(self, f: Finding) -> dict[str, list[str]]:
        out: dict[str, set[str]] = {}
        for s in self.sources(f):
            d = self.decision(s.id, "data_category")
            if d is not None and d.answer != "none" and s.attrs.get("field"):
                out.setdefault(d.answer, set()).add(str(s.attrs["field"]))
        return {k: sorted(v) for k, v in sorted(out.items())}

    def third_party(self, f: Finding) -> bool:
        return is_third_party(self.primary_sink(f))

    # --- the code-free view --------------------------------------------------------------

    def brief(self, f: Finding) -> dict[str, Any]:
        """A finding as structured facts, with no code, snippets, or evidence text.

        This is everything a narrative drafter (including an LLM) sees about a finding.
        """
        sink = self.primary_sink(f)
        registry = (sink.attrs.get("registry") if sink is not None else None) or {}
        purpose = self.sink_answer(f, "purpose")
        sale = self.sink_answer(f, "sale_or_share_cpra")
        evidence: dict[str, Any] = {}
        if f.category == "mitigated_partially":
            evidence = {
                "vendor_hooks": [m.get("hook") for m in f.evidence.get("mitigation_hooks", [])]
            }
        elif f.category == "cross_border_ambiguity":
            evidence = {
                "conflicting_values": sorted(
                    {str(c.get("value")) for c in f.evidence.get("conflicting_config", [])}
                )
            }
        elif f.category == "observed_unexpected_destination":
            evidence = {k: f.evidence.get(k) for k in ("host", "methods", "canaries")}
        brief = {
            "id": f.id,
            "category": f.category,
            "title": f.title,
            "severity": f.severity,
            "status": f.status,
            "reachable": f.reachable,
            "data_categories": f.data_categories,
            "fields": self.fields(f),
            "fields_by_category": self.fields_by_category(f),
            "third_party": self.third_party(f),
            "special_category": self.special_category(f),
            "relates_to_minor": self.relates_to_minor(f),
            "entry_points": self.entries(f),
            "destination": self.destination(f),
            "destination_class": f.destination_class,
            "vendor_claims": registry.get("processor_claims"),
            "vendor_regions": sorted(
                {e.get("region", "") for e in registry.get("endpoints", [])} - {""}
            ),
            "purpose": {"answer": purpose.answer, "probability": purpose.probability}
            if purpose
            else None,
            "sale_or_share": {"answer": sale.answer, "probability": sale.probability}
            if sale
            else None,
            "transformations": f.classification.get("transformations", []),
            "retention": self.retention(f),
            "verification": self.verification(f),
            "statutes": [r.citation for r in self.statutes.resolve(f.statute_refs)],
            "evidence": evidence,
        }
        safe: dict[str, Any] = redact_value(brief)
        payload_guard(safe)
        return safe
