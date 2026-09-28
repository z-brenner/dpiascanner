"""Dynamic verification of the canary fixtures against fixtures/mock-server (Prompt 6).

These run fixture code on the host through the local fixture backend, which accepts only
paths under fixtures/. They need uv and npm, and install each fixture's dependencies once
into ~/.cache/lantern/fixture-envs. Without the toolchain they skip, unless
LANTERN_REQUIRE_DYNAMIC=1 (set by ``make test-dynamic`` and CI), which turns a skip into a
failure.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from lantern_analysis.analyze import analyze_repo
from lantern_analysis.model import DataFlowGraph, Node
from lantern_decisions.stub import StubProvider
from lantern_worker.dynamic import (
    DynamicSettings,
    DynamicVerifier,
    Step,
    VerificationReport,
    plan_steps,
)
from lantern_worker.dynamic.local import LocalFixtureBackend
from lantern_worker.dynamic.reconcile import is_network_sink
from lantern_worker.pipeline import DynamicConfig, PipelineConfig, run_pipeline

pytestmark = pytest.mark.dynamic

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
THIRD_PARTY_CLASSES = frozenset(
    {
        "analytics",
        "ad_tech",
        "payment_processor",
        "unknown_third_party",
        "cloud_provider",
        "ai_model_provider",
        "communications",
    }
)
_CACHE: dict[str, tuple[DataFlowGraph, VerificationReport]] = {}


def _require(*tools: str) -> None:
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        message = f"dynamic verification needs {', '.join(missing)}"
        if os.environ.get("LANTERN_REQUIRE_DYNAMIC"):
            pytest.fail(message)
        pytest.skip(message)


def _tools(fixture: str) -> tuple[str, ...]:
    return ("uv",) if fixture.endswith("python") else ("npm", "node")


def verified(fixture: str) -> tuple[DataFlowGraph, VerificationReport]:
    _require(*_tools(fixture))
    if fixture not in _CACHE:
        graph = analyze_repo(FIXTURES / fixture, "fixture-sha")
        report = DynamicVerifier(LocalFixtureBackend(FIXTURES)).verify(FIXTURES / fixture, graph)
        _CACHE[fixture] = (graph, report)
    return _CACHE[fixture]


def manifest(fixture: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((FIXTURES / fixture / "MANIFEST.yaml").read_text())
    return data


def sink_node(graph: DataFlowGraph, sink: dict[str, Any]) -> Node:
    [node] = [
        n
        for n in graph.nodes.values()
        if n.kind == "sink" and n.file == sink["file"] and n.line_start == sink["line"]
    ]
    return node


@pytest.mark.parametrize("fixture", ["canary-python", "canary-typescript"])
def test_every_third_party_sink_in_the_manifest_is_verified(fixture):
    graph, report = verified(fixture)
    assert report.status == "completed", report.notes
    checked = 0
    for flow in manifest(fixture)["flows"]:
        sink = flow["sink"]
        if (
            sink["expected_sink_class"] not in THIRD_PARTY_CLASSES
            and "registry_package" not in sink
        ):
            continue
        status = sink_node(graph, sink).attrs["dynamic"]
        if flow["reachable"]:
            assert status["status"] == "verified", (flow["id"], status)
            assert status["reason"] == "canary-observed", (flow["id"], status)
        else:
            # C11 is dead code: nothing can exercise it, so it stays a static inference.
            assert (status["status"], status["reason"]) == ("inferred", "unreachable"), flow["id"]
        checked += 1
    assert checked == 7


@pytest.mark.parametrize("fixture", ["canary-python", "canary-typescript"])
def test_every_reachable_network_sink_in_the_graph_is_verified(fixture):
    graph, report = verified(fixture)
    network = [n for n in graph.nodes.values() if is_network_sink(n) and n.file != "<dynamic>"]
    assert network
    for node in network:
        expected = "verified" if node.attrs.get("reachable", True) else "inferred"
        assert node.attrs["dynamic"]["status"] == expected, (node.file, node.line_start)
    assert report.reconciliation is not None
    assert report.reconciliation.unexpected == [], "no request should reach an unmodeled host"


@pytest.mark.parametrize("fixture", ["canary-python", "canary-typescript"])
def test_observed_canaries_match_the_planted_data(fixture):
    graph, _ = verified(fixture)
    by_flow = {f["id"]: f for f in manifest(fixture)["flows"]}

    def canaries(flow_id: str) -> set[str]:
        return set(sink_node(graph, by_flow[flow_id]["sink"]).attrs["dynamic"]["canaries"])

    assert "email" in canaries("C02")  # Mixpanel
    assert {"lat", "lng"} <= canaries("C04")  # weather API
    assert "phone" in canaries("C08")  # statically unresolved, dynamically confirmed
    assert {"email", "name", "payment_method"} <= canaries("C10")  # Stripe
    assert {"email", "name"} <= canaries("C12")  # S3 export
    # Sentry receives the email despite before_send (the C09 dynamic_note).
    assert "email" in canaries("C09")


def test_typescript_log_sink_is_verified_from_process_output():
    graph, _ = verified("canary-typescript")
    c03 = next(f for f in manifest("canary-typescript")["flows"] if f["id"] == "C03")
    status = sink_node(graph, c03["sink"]).attrs["dynamic"]
    assert (status["status"], status["reason"]) == ("verified", "canary-in-process-output")
    assert status["encodings"] == ["base64"]  # the "encrypted" SSN is base64 in the log line


def test_raw_capture_is_deleted_and_only_shapes_are_kept():
    graph, report = verified("canary-python")
    assert report.observed
    summary = graph.summary["dynamic"]
    assert summary["backend"] == "local-fixture"
    for request in summary["observed"]:
        assert set(request) >= {"host", "method", "path", "body_sha256", "body_field_names"}
        assert "body_b64" not in request and "body" not in request


def test_route_exercise_alone_verifies_the_python_fixture():
    """No run script: start the app and drive it from its OpenAPI document."""
    _require("uv")
    repo = FIXTURES / "canary-python"
    graph = analyze_repo(repo, "fixture-sha")
    steps = [s for s in plan_steps(repo, DynamicSettings.load(repo)) if s.mode == "routes"]
    report = DynamicVerifier(LocalFixtureBackend(FIXTURES)).verify(repo, graph, steps=steps)
    [step] = report.steps
    assert step.routes is not None and step.routes["discovery"] == "openapi"
    for node in graph.nodes.values():
        if is_network_sink(node) and node.attrs.get("reachable", True):
            assert node.attrs["dynamic"]["status"] == "verified", (node.file, node.line_start)


def test_pipeline_carries_verification_into_findings():
    _require("uv")
    result = run_pipeline(
        FIXTURES / "canary-python",
        "fixture-sha",
        PipelineConfig(StubProvider(), dynamic=DynamicConfig(LocalFixtureBackend(FIXTURES))),
    )
    assert result.verification is not None and result.verification.status == "completed"
    assert result.summary()["dynamic"]["counts"]["verified"] == 7
    unresolved = [f for f in result.findings.findings if f.category == "unresolved_flow"]
    assert unresolved, "C08 stays unresolved statically"
    [verification] = unresolved[0].evidence["verification"].values()
    assert verification["status"] == "verified" and "phone" in verification["canaries"]
    assert not [
        f for f in result.findings.findings if f.category == "observed_unexpected_destination"
    ]


def test_local_backend_refuses_repositories_outside_fixtures(tmp_path):
    (tmp_path / "requirements.txt").write_text("")
    graph = DataFlowGraph(repo=str(tmp_path), commit="x", languages=["python"])
    verifier = DynamicVerifier(LocalFixtureBackend(FIXTURES))
    with pytest.raises(PermissionError):
        verifier.verify(tmp_path, graph, steps=[Step("script", ("python", "-c", "print(1)"))])
    # A repository with no runnable mode never reaches the backend at all.
    assert verifier.verify(tmp_path, graph, steps=[]).status == "no-runnable-mode"
