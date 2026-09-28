"""The run pipeline: analyze, build decision states, classify, and assemble findings.

Dynamic verification and report rendering hook in after classification (see the worker's
job runner); this module stays synchronous and side-effect free apart from the store.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lantern_analysis.analyze import AnalysisOptions, analyze_repo
from lantern_analysis.model import DataFlowGraph
from lantern_analysis.secrets import payload_guard
from lantern_analysis.state import Target, build_targets
from lantern_decisions.provider import DecisionProvider
from lantern_decisions.question_sets import TargetType, load_bundle
from lantern_decisions.types import DecisionRequest, DecisionResult
from lantern_registry.resolver import PackageSource
from lantern_report.findings import (
    DEFAULT_THRESHOLD,
    FindingsResult,
    assemble_findings,
    decision_id,
)
from lantern_worker.store import DecisionRow, RunStore

StageCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class PipelineConfig:
    provider: DecisionProvider
    question_set_version: str = "v1"
    threshold: float = DEFAULT_THRESHOLD
    analysis: AnalysisOptions = field(default_factory=AnalysisOptions)
    package_source: PackageSource | None = None


@dataclass
class PipelineResult:
    run_id: str
    graph: DataFlowGraph
    targets: list[Target]
    decisions: list[DecisionResult]
    findings: FindingsResult
    timings: dict[str, float]

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "commit": self.graph.commit,
            "flows": len(self.graph.flows),
            "targets": len(self.targets),
            "decisions": len(self.decisions),
            "findings": len(self.findings.findings),
            "unresolved_findings": len(self.findings.unresolved),
            "unresolved_decisions": len(self.findings.unresolved_decisions),
            "coverage": self.graph.summary.get("coverage", {}),
            "timings": self.timings,
        }


def classify(
    targets: list[Target], provider: DecisionProvider, question_set_version: str = "v1"
) -> list[DecisionResult]:
    """Run each target's question set through the provider in one batched call."""
    bundle = load_bundle(question_set_version)
    requests = []
    for target in targets:
        payload_guard(target.state)
        qs = bundle.for_target(target.target_type)
        requests.append(
            DecisionRequest(
                target_id=target.target_id,
                target_type=TargetType(target.target_type),
                state=target.state,
                question_set_id=qs.id,
                question_set_version=qs.version,
            )
        )
    return provider.decide(requests)


def decision_rows(run_id: str, decisions: list[DecisionResult]) -> list[DecisionRow]:
    return [
        DecisionRow(
            id=decision_id(d.target_id, d.question_set_id, d.question_id),
            run_id=run_id,
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
            raw=dict(d.raw),
        )
        for d in decisions
    ]


def run_pipeline(
    repo: Path | str,
    commit: str,
    config: PipelineConfig,
    store: RunStore | None = None,
    run_id: str | None = None,
    on_stage: StageCallback | None = None,
) -> PipelineResult:
    run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
    timings: dict[str, float] = {}

    def stage(name: str, **info: Any) -> None:
        if on_stage is not None:
            on_stage(name, info)

    started = time.monotonic()
    stage("parse")
    graph = analyze_repo(repo, commit, config.analysis, package_source=config.package_source)
    timings["analyze"] = round(time.monotonic() - started, 3)
    stage("graph", flows=len(graph.flows), coverage=graph.summary.get("coverage", {}))

    t = time.monotonic()
    targets = build_targets(graph)
    stage("classify", targets=len(targets))
    decisions = classify(targets, config.provider, config.question_set_version)
    timings["classify"] = round(time.monotonic() - t, 3)

    t = time.monotonic()
    findings = assemble_findings(graph, decisions, config.threshold)
    timings["findings"] = round(time.monotonic() - t, 3)
    result = PipelineResult(run_id, graph, targets, decisions, findings, timings)
    if store is not None:
        store.save_graph(run_id, graph)
        store.save_decisions(run_id, decision_rows(run_id, decisions))
        store.save_findings(run_id, findings)
    stage("findings", findings=len(findings.findings))
    return result
