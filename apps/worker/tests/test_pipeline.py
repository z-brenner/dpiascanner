"""Classification pass and findings against the fixture manifests (Prompt 5 acceptance tests)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from lantern_decisions.stub import StubProvider
from lantern_report.diff import diff_findings
from lantern_report.findings import Finding, assemble_findings
from lantern_worker.pipeline import PipelineConfig, PipelineResult, run_pipeline
from lantern_worker.store import FileRunStore

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
CANARIES = ["canary-python", "canary-typescript"]
_RESULTS: dict[str, PipelineResult] = {}


def result_for(fixture: str) -> PipelineResult:
    if fixture not in _RESULTS:
        _RESULTS[fixture] = run_pipeline(
            FIXTURES / fixture, "fixture-sha", PipelineConfig(StubProvider())
        )
    return _RESULTS[fixture]


def manifest(fixture: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((FIXTURES / fixture / "MANIFEST.yaml").read_text())
    return data


def findings_for_flow(result: PipelineResult, flow: dict[str, Any]) -> list[Finding]:
    g = result.graph
    src = flow["sources"][0]
    source_ids = {
        n.id
        for n in g.nodes.values()
        if n.kind == "source"
        and n.file == src["file"]
        and n.line_start == src["line"]
        and n.attrs.get("field") == src["field"]
    }
    sink = flow["sink"]
    sink_ids = {
        n.id
        for n in g.nodes.values()
        if n.kind == "sink" and n.file == sink["file"] and n.line_start == sink["line"]
    }
    assert source_ids and sink_ids, flow["id"]
    return [
        f
        for f in result.findings.findings
        if source_ids & set(f.node_ids) and sink_ids & set(f.node_ids)
    ]


@pytest.mark.parametrize("fixture", CANARIES)
def test_every_manifest_flow_yields_its_expected_findings(fixture: str) -> None:
    result = result_for(fixture)
    problems = []
    for flow in manifest(fixture)["flows"]:
        found = findings_for_flow(result, flow)
        categories = {f.category for f in found}
        missing = set(flow["expected_findings"]) - categories
        forbidden = set(flow.get("forbidden_findings", [])) & categories
        if missing:
            problems.append(f"{flow['id']}: missing {sorted(missing)}; got {sorted(categories)}")
        if forbidden:
            problems.append(f"{flow['id']}: forbidden {sorted(forbidden)} present")
        if flow["resolution"] == "resolved":
            for f in found:
                if f.category in flow["expected_findings"] and f.status != "resolved":
                    problems.append(
                        f"{flow['id']}: {f.category} is unresolved: {f.unresolved_reasons}"
                    )
    assert not problems, "\n".join(problems)


def test_clean_fixture_yields_no_findings() -> None:
    result = run_pipeline(FIXTURES / "clean-python", "fixture-sha", PipelineConfig(StubProvider()))
    assert result.findings.findings == []
    assert result.decisions == []


@pytest.mark.parametrize("fixture", CANARIES)
def test_specific_canaries(fixture: str) -> None:
    result = result_for(fixture)
    flows = {f["id"]: f for f in manifest(fixture)["flows"]}
    by_flow = {
        fid: {f.category: f for f in findings_for_flow(result, flow)} for fid, flow in flows.items()
    }
    c03 = by_flow["C03"]["reversible_obfuscation"]
    assert c03.status == "resolved" and "CPRA-150" in c03.statute_refs
    assert c03.classification["transformations"] == ["passthrough", "reversible_encoding"]
    c08 = by_flow["C08"]["unresolved_flow"]
    assert c08.status == "unresolved"
    assert c08.evidence["unresolved_steps"][0]["line"] == flows["C08"]["opaque_access"]["line"]
    c11 = by_flow["C11"]["unreachable_flow"]
    assert c11.severity == "informational" and c11.reachable is False
    c12 = by_flow["C12"]["cross_border_ambiguity"]
    regions = {
        c["value"] for c in c12.evidence["conflicting_config"] if c["key"].endswith("region")
    }
    assert regions == {"eu-west-1", "us-east-1"}
    c05 = by_flow["C05"]["special_category_processing"]
    assert "GDPR-9" in c05.statute_refs and c05.severity in ("high", "critical")


@pytest.mark.parametrize("fixture", CANARIES)
def test_findings_are_reproducible_and_traceable(fixture: str) -> None:
    result = result_for(fixture)
    g = result.graph
    decision_ids = {f"{d.target_id}|{d.question_id}" for d in result.decisions}
    for finding in result.findings.findings:
        assert finding.commit == "fixture-sha"
        assert finding.question_set_version == "v1"
        assert finding.provider == "stub" and finding.provider_version
        assert finding.node_ids and all(n in g.nodes for n in finding.node_ids)
        assert all(e in g.edges for e in finding.edge_ids)
        assert finding.decision_ids
        assert finding.key.startswith("K-")
    assert decision_ids


def test_low_confidence_decisions_make_findings_unresolved() -> None:
    result = result_for("canary-python")
    strict = assemble_findings(result.graph, result.decisions, threshold=0.95)
    unresolved = [f for f in strict.findings if f.status == "unresolved"]
    assert len(unresolved) > len(result.findings.unresolved)
    for finding in unresolved:
        if finding.category != "unresolved_flow":
            assert finding.unresolved_reasons
            assert all(r.probability < 0.95 for r in finding.unresolved_reasons)


def test_every_decision_row_is_persisted_with_raw_output(tmp_path: Path) -> None:
    store = FileRunStore(tmp_path)
    result = run_pipeline(
        FIXTURES / "canary-python",
        "fixture-sha",
        PipelineConfig(StubProvider()),
        store=store,
        run_id="r1",
    )
    rows = store.load_decisions("r1")
    assert len(rows) == len(result.decisions)
    assert all(r.raw and r.provider == "stub" and r.distribution for r in rows)
    assert store.load_findings("r1").to_dict() == result.findings.to_dict()
    assert store.load_graph("r1").to_dict() == result.graph.to_dict()


def _copy_fixture(tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(
        FIXTURES / "canary-python", target, ignore=shutil.ignore_patterns("__pycache__", "*.db")
    )
    return target


def test_diff_reports_removed_c02_as_resolved(tmp_path: Path) -> None:
    before_repo = _copy_fixture(tmp_path, "before")
    after_repo = _copy_fixture(tmp_path, "after")
    users = after_repo / "app" / "routes" / "users.py"
    text = users.read_text()
    assert "    track_signup(user.id, payload.email, payload.plan)\n" in text
    users.write_text(text.replace("    track_signup(user.id, payload.email, payload.plan)\n", ""))
    config = PipelineConfig(StubProvider())
    before = run_pipeline(before_repo, "sha-1", config).findings.findings
    after = run_pipeline(after_repo, "sha-2", config).findings.findings
    diff = diff_findings(before, after)
    assert [f.category for f in diff.resolved] == ["personal_data_to_third_party"]
    assert diff.resolved[0].destination_class == "analytics"
    assert diff.new == []


def test_moving_code_does_not_change_finding_keys(tmp_path: Path) -> None:
    before_repo = _copy_fixture(tmp_path, "before")
    after_repo = _copy_fixture(tmp_path, "after")
    for path in (after_repo / "app").rglob("*.py"):
        path.write_text("# a new header line shifts every line number\n\n" + path.read_text())
    config = PipelineConfig(StubProvider())
    before = run_pipeline(before_repo, "sha-1", config).findings.findings
    after = run_pipeline(after_repo, "sha-2", config).findings.findings
    diff = diff_findings(before, after)
    assert diff.is_empty, diff.to_dict()
