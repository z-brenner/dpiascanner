"""The run pipeline: analyze, classify, verify dynamically (optional), and assemble findings.

Stages, in order, reported through ``on_stage``: ``parse``, ``graph``, ``registry``,
``classify``, ``verify`` (only with ``PipelineConfig.dynamic``). The job runner adds
``clone`` before and ``report`` after. Dynamic verification runs after classification and
before findings are assembled, so findings carry each sink's verification status; decision
states never include dynamic results, so the order does not change any decision.

An incremental run (``PipelineConfig.incremental``) still analyzes the whole repository,
because interprocedural taint needs the whole call graph and analysis takes seconds. It
limits the expensive part: only targets in the changed files and their graph neighbors are
classified; every other target reuses the base run's decision when its state hash is
unchanged.
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
from lantern_decisions.types import DecisionRequest, DecisionResult, state_hash
from lantern_registry.resolver import PackageSource
from lantern_report.findings import (
    DEFAULT_THRESHOLD,
    FindingsResult,
    assemble_findings,
    decision_id,
)
from lantern_worker.dynamic.base import Backend, Limits
from lantern_worker.dynamic.verifier import DynamicVerifier, VerificationReport
from lantern_worker.store import DecisionRow, RunStore

StageCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class DynamicConfig:
    backend: Backend
    limits: Limits = field(default_factory=Limits)


@dataclass
class IncrementalScope:
    """Files whose targets are classified afresh, and the base run's decisions for the rest."""

    files: set[str]
    base_decisions: list[DecisionResult]


@dataclass
class PipelineConfig:
    provider: DecisionProvider
    question_set_version: str = "v1"
    threshold: float = DEFAULT_THRESHOLD
    analysis: AnalysisOptions = field(default_factory=AnalysisOptions)
    package_source: PackageSource | None = None
    dynamic: DynamicConfig | None = None
    incremental: IncrementalScope | None = None


@dataclass
class PipelineResult:
    run_id: str
    graph: DataFlowGraph
    targets: list[Target]
    decisions: list[DecisionResult]
    findings: FindingsResult
    timings: dict[str, float]
    verification: VerificationReport | None = None
    classification: dict[str, int] = field(default_factory=dict)

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
            "dynamic": (
                {
                    "status": self.verification.status,
                    "counts": self.verification.reconciliation.counts()
                    if self.verification.reconciliation
                    else {},
                }
                if self.verification
                else None
            ),
            "classification": self.classification,
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


def target_files(target: Target) -> set[str]:
    files = {str(target.state.get("location", {}).get("file", ""))}
    files.update(str(step.get("file", "")) for step in target.state.get("evidence_chain", []))
    return files - {""}


def classify_incremental(
    targets: list[Target],
    provider: DecisionProvider,
    question_set_version: str,
    scope: IncrementalScope,
) -> tuple[list[DecisionResult], dict[str, int]]:
    """Classify targets in scope; reuse base decisions for unchanged targets outside it."""
    by_target: dict[str, list[DecisionResult]] = {}
    for d in scope.base_decisions:
        by_target.setdefault(d.target_id, []).append(d)
    reused: list[DecisionResult] = []
    fresh: list[Target] = []
    for target in targets:
        previous = by_target.get(target.target_id, [])
        unchanged = previous and all(d.state_hash == state_hash(target.state) for d in previous)
        if not (target_files(target) & scope.files) and unchanged:
            reused.extend(previous)
        else:
            fresh.append(target)
    decisions = classify(fresh, provider, question_set_version) if fresh else []
    stats = {"classified_targets": len(fresh), "reused_targets": len(targets) - len(fresh)}
    return [*decisions, *reused], stats


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
    deps = graph.summary.get("dependencies", {})
    stage("registry", analyzed=deps.get("analyzed", 0), skipped=deps.get("skipped", 0))

    t = time.monotonic()
    targets = build_targets(graph)
    stage("classify", targets=len(targets))
    if config.incremental is not None:
        decisions, classification = classify_incremental(
            targets, config.provider, config.question_set_version, config.incremental
        )
    else:
        decisions = classify(targets, config.provider, config.question_set_version)
        classification = {"classified_targets": len(targets), "reused_targets": 0}
    timings["classify"] = round(time.monotonic() - t, 3)

    verification: VerificationReport | None = None
    if config.dynamic is not None:
        t = time.monotonic()
        stage("verify", backend=config.dynamic.backend.name)
        verifier = DynamicVerifier(config.dynamic.backend, config.dynamic.limits)
        verification = verifier.verify(Path(repo), graph)
        timings["verify"] = round(time.monotonic() - t, 3)

    t = time.monotonic()
    findings = assemble_findings(graph, decisions, config.threshold)
    timings["findings"] = round(time.monotonic() - t, 3)
    result = PipelineResult(
        run_id, graph, targets, decisions, findings, timings, verification, classification
    )
    if store is not None:
        store.save_graph(run_id, graph)
        store.save_decisions(run_id, decision_rows(run_id, decisions))
        store.save_findings(run_id, findings)
    return result
