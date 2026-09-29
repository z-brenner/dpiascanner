"""Manifest well-formedness. Runs before any analyzer exists, so manifests cannot drift.

Every location in a manifest is checked against the fixture source: the file must exist and
the snippet must appear on the stated line. Enumerated values must come from the question
set vocabularies the decision layer will use.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = REPO_ROOT / "fixtures"
CANARY_FIXTURES = ["canary-python", "canary-typescript"]
ALL_FIXTURES = ["clean-python", *CANARY_FIXTURES]
CANARY_IDS = [f"C{i:02d}" for i in range(1, 13)]

# Vocabularies mirror question set v1 in packages/decisions (source_v1, sink_v1, edge_v1).
DATA_CATEGORIES = {
    "identifier",
    "contact",
    "financial",
    "health",
    "biometric",
    "precise_location",
    "coarse_location",
    "behavioral",
    "credentials",
    "government_id",
    "demographic",
    "none",
}
SINK_CLASSES = {
    "first_party_store",
    "log",
    "queue",
    "analytics",
    "ad_tech",
    "cloud_provider",
    "payment_processor",
    "communications",
    "ai_model_provider",
    "unknown_third_party",
    "cross_border",
}
TRANSFORMATIONS = {
    "passthrough",
    "hashed",
    "encrypted",
    "tokenized",
    "aggregated",
    "truncated",
    "dropped",
    "reversible_encoding",
}
FINDING_CATEGORIES = {
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
}
SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js"}


def load_manifest(fixture: str) -> dict[str, Any]:
    data = yaml.safe_load((FIXTURES / fixture / "MANIFEST.yaml").read_text())
    assert isinstance(data, dict)
    return data


def assert_location(fixture: str, loc: dict[str, Any], *, line_required: bool = True) -> None:
    path = FIXTURES / fixture / loc["file"]
    assert path.is_file(), f"{fixture}: missing file {loc['file']}"
    lines = path.read_text().splitlines()
    snippet = loc["snippet"]
    if "line" in loc:
        line = loc["line"]
        assert isinstance(line, int) and 1 <= line <= len(lines), f"{loc} line out of range"
        assert snippet in lines[line - 1], (
            f"{fixture}:{loc['file']}:{line} does not contain {snippet!r}; "
            f"found {lines[line - 1].strip()!r}"
        )
    else:
        assert not line_required, f"{loc} needs a line"
        assert snippet in path.read_text(), f"{fixture}:{loc['file']} lacks {snippet!r}"


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_manifest_header(fixture: str) -> None:
    manifest = load_manifest(fixture)
    assert manifest["schema_version"] == 1
    assert manifest["fixture"] == fixture
    assert manifest["language"] in {"python", "typescript"}
    assert isinstance(manifest["flows"], list)


@pytest.mark.parametrize("fixture", CANARY_FIXTURES)
def test_canary_flows_well_formed(fixture: str) -> None:
    manifest = load_manifest(fixture)
    flows = manifest["flows"]
    assert [f["id"] for f in flows] == CANARY_IDS

    for flow in flows:
        fid = flow["id"]
        assert isinstance(flow["description"], str) and flow["description"].strip(), fid
        assert flow["sources"], f"{fid}: no sources"
        for source in flow["sources"]:
            assert_location(fixture, source)
            assert isinstance(source["field"], str) and source["field"], fid
            assert source["expected_data_category"] in DATA_CATEGORIES, fid
        assert_location(fixture, flow["sink"])
        assert flow["sink"]["expected_sink_class"] in SINK_CLASSES, fid
        assert flow["expected_transformation"] in TRANSFORMATIONS, fid
        assert isinstance(flow["reachable"], bool), fid
        assert flow["resolution"] in {"resolved", "unresolved"}, fid

        expected = set(flow["expected_findings"])
        forbidden = set(flow.get("forbidden_findings", []))
        assert expected, f"{fid}: expected_findings is empty"
        assert expected <= FINDING_CATEGORIES, f"{fid}: {expected - FINDING_CATEGORIES}"
        assert forbidden <= FINDING_CATEGORIES, f"{fid}: {forbidden - FINDING_CATEGORIES}"
        assert not expected & forbidden, f"{fid}: expected and forbidden overlap"

        for key in ("transform", "opaque_access", "exception_raise"):
            if key in flow:
                assert_location(fixture, flow[key])
        if "mitigation" in flow:
            assert_location(fixture, flow["mitigation"]["hook_location"])
            assert_location(fixture, flow["mitigation"]["mitigated_sink"])
        for evidence in flow.get("retention", {}).get("evidence", []):
            assert_location(fixture, evidence, line_required=False)
        for value in flow.get("conflicting_config", {}).get("values", []):
            assert_location(fixture, value)
        for crossed in flow.get("files_crossed", []):
            assert (FIXTURES / fixture / crossed).is_file(), f"{fid}: {crossed}"
        for bad in flow.get("must_not_classify_as", []):
            assert bad in TRANSFORMATIONS, fid


@pytest.mark.parametrize("fixture", CANARY_FIXTURES)
def test_canary_semantics(fixture: str) -> None:
    flows = {f["id"]: f for f in load_manifest(fixture)["flows"]}
    assert flows["C03"]["expected_transformation"] == "reversible_encoding"
    assert "encrypted" in flows["C03"]["must_not_classify_as"]
    assert flows["C04"]["precise_geolocation"] is True
    assert flows["C05"]["special_category"] is True
    assert flows["C05"]["retention"]["has_expiry"] is False
    assert flows["C06"]["expected_transformation"] == "aggregated"
    assert flows["C06"]["retention"]["has_expiry"] is True
    assert len(flows["C07"]["files_crossed"]) == 6
    assert flows["C08"]["resolution"] == "unresolved"
    assert "opaque_access" in flows["C08"]
    assert flows["C09"]["mitigation"]["mitigated_sink"] != flows["C09"]["sink"]
    assert {s["expected_data_category"] for s in flows["C10"]["sources"]} >= {"financial"}
    assert flows["C11"]["reachable"] is False
    assert flows["C12"]["jurisdiction_review"] is True
    regions = {v["value"] for v in flows["C12"]["conflicting_config"]["values"]}
    assert len(regions) >= 2
    reachable = [f for f in flows.values() if f["id"] != "C11"]
    assert all(f["reachable"] for f in reachable)


def test_python_and_typescript_manifests_agree() -> None:
    py = {f["id"]: f for f in load_manifest("canary-python")["flows"]}
    ts = {f["id"]: f for f in load_manifest("canary-typescript")["flows"]}
    for fid in CANARY_IDS:
        for key in ("expected_findings", "resolution", "reachable", "expected_transformation"):
            assert py[fid][key] == ts[fid][key], f"{fid}.{key} differs across languages"
        assert py[fid]["sink"]["expected_sink_class"] == ts[fid]["sink"]["expected_sink_class"]


def test_clean_fixture_has_no_flows_and_traps_exist() -> None:
    manifest = load_manifest("clean-python")
    assert manifest["flows"] == []
    assert manifest["traps"]
    for trap in manifest["traps"]:
        assert_location("clean-python", trap, line_required=False)


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_fixture_source_does_not_leak_manifest(fixture: str) -> None:
    """Planted flows must not be labeled in code, or the benchmark is rigged."""
    leak = re.compile(r"\bC(0[1-9]|1[0-2])\b|MANIFEST|canary flow", re.IGNORECASE)
    root = FIXTURES / fixture
    for path in root.rglob("*"):
        if (
            path.suffix in SOURCE_EXTENSIONS
            and "node_modules" not in path.parts
            and "generated" not in path.parts
        ):
            text = path.read_text()
            match = leak.search(text)
            assert match is None, f"{path.relative_to(FIXTURES)} contains {match.group(0)!r}"


@pytest.mark.parametrize("fixture", ALL_FIXTURES)
def test_fixture_is_runnable(fixture: str) -> None:
    root = FIXTURES / fixture
    assert (root / "Dockerfile").is_file()
    assert (root / "lantern.yml").is_file()
    config = yaml.safe_load((root / "lantern.yml").read_text())["dynamic"]
    assert config["run"]
    for name, endpoint in config["endpoints"].items():
        url = endpoint if isinstance(endpoint, str) else endpoint["url"]
        assert url.startswith("https://"), f"{fixture}: {name} must default to a real https URL"
    has_tests = any((root / d).is_dir() for d in ("tests", "test"))
    has_exercise = any((root / "scripts").glob("exercise.*"))
    assert has_tests and has_exercise


def test_dependency_fixture_manifest_locations() -> None:
    manifest = load_manifest("unregistered-sdk-python")
    assert [f["id"] for f in manifest["flows"]] == ["D01"]
    for flow in manifest["flows"]:
        for source in flow["sources"]:
            assert_location("unregistered-sdk-python", source)
        assert_location("unregistered-sdk-python", flow["sink"])
        assert set(flow["expected_findings"]) <= FINDING_CATEGORIES
    mirror = FIXTURES / "dependency-mirror" / "pypi"
    assert (mirror / "acme-geo-0.3.1" / "acme_geo" / "client.py").is_file()


def test_gaps_fixture_manifest_locations() -> None:
    manifest = load_manifest("gaps-python")
    assert [f["id"] for f in manifest["flows"]] == ["G01", "G02", "G03", "G04", "G05"]
    for flow in manifest["flows"]:
        for source in flow["sources"]:
            assert_location("gaps-python", source)
            assert source["expected_data_category"] in DATA_CATEGORIES
        assert_location("gaps-python", flow["sink"])
        assert flow["sink"]["expected_sink_class"] in SINK_CLASSES
        assert set(flow["expected_findings"]) <= FINDING_CATEGORIES
    for flow in manifest["flows"]:
        # A known gap names what the analyzer is missing; the gate skips it until it is fixed.
        assert flow.get("known_gap", "x").strip(), flow["id"]
