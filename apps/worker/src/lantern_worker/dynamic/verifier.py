"""Run a repository under dynamic verification and reconcile what it sent with the graph."""

from __future__ import annotations

import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lantern_analysis.model import DataFlowGraph
from lantern_worker.dynamic.base import Backend, Limits, SetupError, StepOutcome
from lantern_worker.dynamic.canaries import CanaryHit, CanarySet
from lantern_worker.dynamic.observed import ObservedRequest, load_capture
from lantern_worker.dynamic.plan import DynamicSettings, Step, plan_steps, static_routes
from lantern_worker.dynamic.reconcile import Reconciliation, apply, is_network_sink, reconcile

MAX_OBSERVED = 500


@dataclass
class VerificationReport:
    backend: str
    status: str  # completed | setup-failed | no-runnable-mode | budget-exhausted
    steps: list[StepOutcome] = field(default_factory=list)
    reconciliation: Reconciliation | None = None
    observed: list[ObservedRequest] = field(default_factory=list)
    duration_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    def run_info(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "status": self.status,
            "duration_s": round(self.duration_s, 2),
            "steps": [s.to_dict() for s in self.steps],
            "notes": self.notes,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.run_info(),
            "reconciliation": self.reconciliation.to_dict() if self.reconciliation else None,
            "observed": [r.to_dict() for r in self.observed],
        }


def _pending(graph: DataFlowGraph, rec: Reconciliation) -> list[str]:
    """Reachable network sinks that no canary-bearing request has verified yet."""
    return [
        node_id
        for node_id, v in rec.sinks.items()
        if v.status != "verified"
        and is_network_sink(graph.nodes[node_id])
        and graph.nodes[node_id].attrs.get("reachable", True)
    ]


def _output_of(report: VerificationReport) -> list[CanaryHit]:
    return sorted({h for step in report.steps for h in step.output_canaries})


class DynamicVerifier:
    def __init__(
        self,
        backend: Backend,
        limits: Limits | None = None,
        canaries: CanarySet | None = None,
    ) -> None:
        self.backend = backend
        self.limits = limits or Limits()
        self.canaries = canaries or CanarySet.default()

    def verify(
        self,
        repo: Path,
        graph: DataFlowGraph,
        workdir: Path | None = None,
        steps: list[Step] | None = None,
    ) -> VerificationReport:
        """Run the planned modes, reconcile, and write the result onto ``graph``."""
        started = time.monotonic()
        settings = DynamicSettings.load(repo)
        language = next(iter(graph.languages), None) if graph.languages else None
        steps = steps if steps is not None else plan_steps(repo, settings, language)
        report = VerificationReport(backend=self.backend.name, status="completed")
        routes = static_routes(graph)
        own_workdir = workdir is None
        workdir = workdir or Path(tempfile.mkdtemp(prefix="lantern-dyn-"))
        capture = workdir / "capture.jsonl"
        try:
            if not steps:
                report.status = "no-runnable-mode"
                report.notes.append("no tests, run script, or server entry point detected")
            else:
                with self.backend.open(
                    repo, settings, self.canaries, workdir, self.limits
                ) as session:
                    capture = session.capture
                    for step in steps:
                        remaining = self.limits.wall_clock_s - (time.monotonic() - started)
                        if remaining <= 5:
                            report.status = "budget-exhausted"
                            report.notes.append(f"skipped {step.mode}: wall-clock budget exhausted")
                            break
                        report.steps.append(session.run(step, routes, remaining))
                        interim = reconcile(
                            graph,
                            load_capture(capture, self.canaries, delete=False),
                            _output_of(report),
                        )
                        if not _pending(graph, interim):
                            break
        except SetupError as exc:
            report.status = "setup-failed"
            report.notes.append(str(exc))
        finally:
            report.observed = load_capture(capture, self.canaries, delete=True)
            if own_workdir:
                shutil.rmtree(workdir, ignore_errors=True)
        report.reconciliation = reconcile(graph, report.observed, _output_of(report))
        report.duration_s = time.monotonic() - started
        observed = [r.to_dict() for r in report.observed[:MAX_OBSERVED]]
        apply(graph, report.reconciliation, {**report.run_info(), "observed": observed})
        return report
