"""Persist a run's graph, decisions, findings, and reports, and load them back.

Retention policy: after a run, the only repository text kept is each node's snippet, which
the analyzer already bounds to the node's span plus a few lines and redacts for secrets.
``scrub`` blanks every other code excerpt (evidence step text, mitigation and retention
evidence lines) before anything is stored or rendered into a stored report. Derived facts
stay: file paths and lines, field names, hosts, and redacted config values.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lantern_analysis.model import DataFlowGraph, Edge, Flow, Node, NodeKind, Step
from lantern_analysis.secrets import redact_value
from lantern_decisions.question_sets import TargetType
from lantern_decisions.types import DecisionResult
from lantern_platform.db import DecisionRowDB, EdgeRow, FindingRow, NodeRow, ReportRow, Run
from lantern_report.findings import Finding, FindingsResult, UnresolvedReason, decision_id

CODE_KEYS = frozenset({"text"})
CONTENT_TYPES = {
    "json": "application/json",
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def scrub(value: Any) -> Any:
    """Blank code excerpts (``text`` fields) anywhere in a nested structure."""
    if isinstance(value, dict):
        return {
            k: ("" if k in CODE_KEYS and isinstance(v, str) else scrub(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def scrub_graph(graph: DataFlowGraph) -> DataFlowGraph:
    """The graph as it may be stored: node snippets kept, all other code text blanked."""
    for node in graph.nodes.values():
        node.attrs = scrub(node.attrs)
    for edge in graph.edges.values():
        edge.evidence = [
            Step(s.file, s.line, s.kind, "", s.unresolved, s.note, s.function)
            for s in edge.evidence
        ]
        edge.attrs = scrub(edge.attrs)
    graph.config = scrub(graph.config)
    return graph


def scrub_findings(findings: FindingsResult) -> FindingsResult:
    for f in findings.findings:
        f.evidence = scrub(f.evidence)
    return findings


def _verification(finding: Finding, graph: DataFlowGraph) -> str:
    for node_id in finding.node_ids:
        dynamic = graph.nodes[node_id].attrs.get("dynamic") if node_id in graph.nodes else None
        if graph.nodes.get(node_id) and graph.nodes[node_id].kind == "sink" and dynamic:
            return str(dynamic.get("status", "not-run"))
    return "not-run"


def save_results(
    session: Session,
    run: Run,
    graph: DataFlowGraph,
    decisions: Sequence[DecisionResult],
    findings: FindingsResult,
    reports: dict[str, bytes],
) -> None:
    """Write everything for a run in the caller's transaction. Replaces earlier rows."""
    for table in (NodeRow, EdgeRow, DecisionRowDB, FindingRow, ReportRow):
        session.execute(delete(table).where(table.run_id == run.id))
    for n in graph.nodes.values():
        session.add(
            NodeRow(
                run_id=run.id,
                id=n.id,
                kind=n.kind,
                file=n.file,
                line_start=n.line_start,
                line_end=n.line_end,
                symbol=n.symbol,
                language=n.language,
                snippet=n.snippet,
                snippet_hash=n.snippet_hash,
                rule_ids=list(n.rule_ids),
                attrs=redact_value(n.attrs),
            )
        )
    for e in graph.edges.values():
        session.add(
            EdgeRow(
                run_id=run.id,
                id=e.id,
                from_node=e.from_node,
                to_node=e.to_node,
                kind=e.kind,
                evidence=[s.to_dict() for s in e.evidence],
                attrs=e.attrs,
            )
        )
    seen: set[str] = set()
    for d in decisions:
        did = decision_id(d.target_id, d.question_set_id, d.question_id)
        if did in seen:
            continue
        seen.add(did)
        session.add(
            DecisionRowDB(
                run_id=run.id,
                id=did,
                target_id=d.target_id,
                target_type=d.target_type.value,
                question_set_id=d.question_set_id,
                question_set_version=d.question_set_version,
                question_id=d.question_id,
                answer=d.answer,
                probability=d.probability,
                distribution=dict(d.distribution),
                provider=d.provider,
                provider_version=d.provider_version,
                state_hash=d.state_hash,
                raw=redact_value(dict(d.raw)),
            )
        )
    for f in findings.findings:
        session.add(
            FindingRow(
                run_id=run.id,
                id=f.id,
                key=f.key,
                category=f.category,
                severity=f.severity,
                status=f.status,
                verification=_verification(f, graph),
                title=f.title,
                data=f.to_dict(),
            )
        )
    for fmt, content in reports.items():
        session.add(
            ReportRow(run_id=run.id, format=fmt, content_type=CONTENT_TYPES[fmt], content=content)
        )
    run.graph_meta = {
        "repo": graph.repo,
        "commit": graph.commit,
        "languages": graph.languages,
        "flows": [f.to_dict() for f in graph.flows],
        "config": graph.config,
        "summary": graph.summary,
        "options": graph.options,
    }
    run.coverage = dict(graph.summary.get("coverage", {}))
    run.summary = {
        **(run.summary or {}),
        "findings": len(findings.findings),
        "unresolved": len(findings.unresolved),
        "by_severity": _count(f.severity for f in findings.findings),
        "threshold": findings.threshold,
        "severity_version": findings.severity_version,
        "unresolved_decisions": [r.to_dict() for r in findings.unresolved_decisions],
        "dynamic": {
            k: v
            for k, v in (graph.summary.get("dynamic") or {}).items()
            if k in ("status", "counts", "backend")
        },
    }


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return out


def load_graph(session: Session, run: Run) -> DataFlowGraph:
    meta = run.graph_meta or {}
    graph = DataFlowGraph(
        repo=str(meta.get("repo", run.repo_full_name)),
        commit=str(meta.get("commit", run.sha)),
        languages=list(meta.get("languages", [])),
        flows=[Flow.from_dict(f) for f in meta.get("flows", [])],
        config=list(meta.get("config", [])),
        summary=dict(meta.get("summary", {})),
        options=dict(meta.get("options", {})),
    )
    for n in session.scalars(select(NodeRow).where(NodeRow.run_id == run.id)):
        graph.nodes[n.id] = Node(
            id=n.id,
            kind=cast(NodeKind, n.kind),
            file=n.file,
            line_start=n.line_start,
            line_end=n.line_end,
            symbol=n.symbol,
            language=n.language,
            snippet=n.snippet,
            snippet_hash=n.snippet_hash,
            rule_ids=list(n.rule_ids),
            attrs=dict(n.attrs),
        )
    for e in session.scalars(select(EdgeRow).where(EdgeRow.run_id == run.id)):
        graph.edges[e.id] = Edge(
            id=e.id,
            from_node=e.from_node,
            to_node=e.to_node,
            kind=e.kind,
            evidence=[Step(**s) for s in e.evidence],
            attrs=dict(e.attrs),
        )
    return graph


def load_findings(session: Session, run: Run) -> FindingsResult:
    rows = session.scalars(select(FindingRow).where(FindingRow.run_id == run.id)).all()
    findings = sorted((Finding.from_dict(r.data) for r in rows), key=lambda f: f.id)
    summary = run.summary or {}
    return FindingsResult(
        findings=findings,
        unresolved_decisions=[
            UnresolvedReason(**r) for r in summary.get("unresolved_decisions", [])
        ],
        threshold=float(summary.get("threshold", 0.75)),
        severity_version=str(summary.get("severity_version", "")),
    )


def load_decisions(session: Session, run: Run) -> list[DecisionResult]:
    rows = session.scalars(select(DecisionRowDB).where(DecisionRowDB.run_id == run.id)).all()
    return [
        DecisionResult(
            target_id=r.target_id,
            target_type=TargetType(r.target_type),
            question_set_id=r.question_set_id,
            question_set_version=r.question_set_version,
            question_id=r.question_id,
            answer=r.answer,
            probability=r.probability,
            distribution=dict(r.distribution),
            provider=r.provider,
            provider_version=r.provider_version,
            state_hash=r.state_hash,
            raw=dict(r.raw),
        )
        for r in rows
    ]
