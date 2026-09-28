"""Analyzer results against the fixture manifests (Prompt 3 acceptance tests)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from lantern_analysis.model import DataFlowGraph, Flow, Node

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
CANARIES = ["canary-python", "canary-typescript"]


@pytest.fixture(params=CANARIES)
def canary(request: pytest.FixtureRequest) -> tuple[dict[str, Any], DataFlowGraph]:
    manifest = yaml.safe_load((FIXTURES / request.param / "MANIFEST.yaml").read_text())
    graph = request.getfixturevalue(request.param.replace("-", "_"))
    return manifest, graph


def _flows(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["id"]: f for f in manifest["flows"]}


def source_nodes(graph: DataFlowGraph, src: dict[str, Any]) -> list[Node]:
    return [
        n
        for n in graph.nodes.values()
        if n.kind == "source"
        and n.file == src["file"]
        and n.line_start <= src["line"] <= n.line_end
        and n.attrs.get("field") == src["field"]
    ]


def sink_node(graph: DataFlowGraph, sink: dict[str, Any]) -> Node:
    found = [
        n
        for n in graph.nodes.values()
        if n.kind == "sink"
        and n.file == sink["file"]
        and n.line_start <= sink["line"] <= n.line_end
    ]
    assert found, f"no sink node at {sink['file']}:{sink['line']}"
    return found[0]


def flows_between(graph: DataFlowGraph, src: dict[str, Any], sink: dict[str, Any]) -> list[Flow]:
    sources = {n.id for n in source_nodes(graph, src)}
    target = sink_node(graph, sink).id
    return [f for f in graph.flows if f.node_ids[0] in sources and target in f.node_ids]


def the_flow(graph: DataFlowGraph, flow: dict[str, Any]) -> Flow:
    found = flows_between(graph, flow["sources"][0], flow["sink"])
    assert found, f"{flow['id']}: no path from {flow['sources'][0]} to {flow['sink']}"
    return found[0]


# ---------------------------------------------------------------------------- recall


def test_every_manifest_flow_is_in_the_graph(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    manifest, graph = canary
    missing = []
    for flow in manifest["flows"]:
        for src in flow["sources"]:
            if not source_nodes(graph, src):
                missing.append(f"{flow['id']}: source {src['file']}:{src['line']} {src['field']}")
            elif not flows_between(graph, src, flow["sink"]):
                sink = flow["sink"]
                missing.append(f"{flow['id']}: {src['field']} misses {sink['file']}:{sink['line']}")
    assert not missing, "\n".join(missing)


def test_reachability_and_resolution_match_manifest(
    canary: tuple[dict[str, Any], DataFlowGraph],
) -> None:
    manifest, graph = canary
    for flow in manifest["flows"]:
        f = the_flow(graph, flow)
        assert f.reachable is flow["reachable"], f"{flow['id']} reachable={f.reachable}"
        assert f.unresolved is (flow["resolution"] == "unresolved"), (
            f"{flow['id']} unresolved={f.unresolved}"
        )


def test_sources_carry_lexicon_hints_for_expected_category(
    canary: tuple[dict[str, Any], DataFlowGraph],
) -> None:
    manifest, graph = canary
    for flow in manifest["flows"]:
        for src in flow["sources"]:
            categories = {
                h["category"] for n in source_nodes(graph, src) for h in n.attrs["lexicon_hints"]
            }
            assert src["expected_data_category"] in categories, (
                f"{flow['id']} {src['field']}: {categories}"
            )


# ---------------------------------------------------------------------------- specific canaries


def test_c03_passes_through_reversible_encoding(
    canary: tuple[dict[str, Any], DataFlowGraph],
) -> None:
    manifest, graph = canary
    flow = _flows(manifest)["C03"]
    f = the_flow(graph, flow)
    transforms = [graph.nodes[i] for i in f.node_ids if graph.nodes[i].kind == "transform"]
    assert [t.attrs["transform_kind"] for t in transforms] == ["reversible_encoding"]
    assert transforms[0].file == flow["transform"]["file"]
    assert transforms[0].line_start == flow["transform"]["line"]


def test_c06_aggregates_and_has_retention_evidence(
    canary: tuple[dict[str, Any], DataFlowGraph],
) -> None:
    manifest, graph = canary
    flow = _flows(manifest)["C06"]
    f = the_flow(graph, flow)
    kinds = [
        graph.nodes[i].attrs.get("transform_kind")
        for i in f.node_ids
        if graph.nodes[i].kind == "transform"
    ]
    assert kinds == ["aggregation"]
    retention = [graph.nodes[i] for i in f.node_ids if graph.nodes[i].kind == "retention"]
    assert len(retention) == 1
    evidence = retention[0].attrs["retention_evidence"]
    assert {"ttl_field", "deletion_job", "schedule"} <= {e["kind"] for e in evidence}
    for expected in flow["retention"]["evidence"]:
        assert any(e["file"] == expected["file"] for e in evidence), expected


@pytest.mark.parametrize("fid", ["C01", "C05", "C07"])
def test_stores_without_retention_policy_have_no_evidence(
    canary: tuple[dict[str, Any], DataFlowGraph], fid: str
) -> None:
    manifest, graph = canary
    f = the_flow(graph, _flows(manifest)[fid])
    retention = [graph.nodes[i] for i in f.node_ids if graph.nodes[i].kind == "retention"]
    assert len(retention) == 1
    assert retention[0].attrs["retention_evidence"] == []


def test_c07_evidence_crosses_six_files(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    manifest, graph = canary
    flow = _flows(manifest)["C07"]
    f = the_flow(graph, flow)
    steps = [s for e in f.edge_ids if e in graph.edges for s in graph.edges[e].evidence]
    covered = {s.file for s in steps} | {
        crossed for crossed in flow["files_crossed"] for s in steps if crossed in s.note
    }
    assert set(flow["files_crossed"]) <= covered, set(flow["files_crossed"]) - covered


def test_c08_is_unresolved_with_evidence_at_the_opaque_access(
    canary: tuple[dict[str, Any], DataFlowGraph],
) -> None:
    manifest, graph = canary
    flow = _flows(manifest)["C08"]
    f = the_flow(graph, flow)
    assert f.unresolved
    unresolved_steps = [s for e in f.edge_ids for s in graph.edges[e].evidence if s.unresolved]
    assert unresolved_steps, "unresolved flow must carry the step that could not be resolved"
    opaque = flow["opaque_access"]
    assert any(s.file == opaque["file"] and s.line == opaque["line"] for s in unresolved_steps)
    assert "non-literal key" in unresolved_steps[0].note
    sink = sink_node(graph, flow["sink"])
    assert sink.attrs.get("inherited_unresolved") is True


def test_c09_mitigation_hook_is_partial(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    manifest, graph = canary
    flow = _flows(manifest)["C09"]
    leak = the_flow(graph, flow)
    assert leak.mitigated is False
    mitigated = flows_between(graph, flow["sources"][0], flow["mitigation"]["mitigated_sink"])
    assert mitigated and all(f.mitigated for f in mitigated)
    sink = sink_node(graph, flow["mitigation"]["mitigated_sink"])
    hooks = sink.attrs["mitigations"]
    assert any(["user", "email"] in h["scrubs"] for h in hooks)
    hook_loc = flow["mitigation"]["hook_location"]
    assert any(h["file"] == hook_loc["file"] and h["line"] == hook_loc["line"] for h in hooks)


def test_c11_is_unreachable(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    manifest, graph = canary
    flow = _flows(manifest)["C11"]
    flows = flows_between(graph, flow["sources"][0], flow["sink"])
    assert flows and not any(f.reachable for f in flows)
    assert source_nodes(graph, flow["sources"][0])[0].attrs["reachable"] is False


def test_c12_carries_both_conflicting_regions(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    manifest, graph = canary
    flow = _flows(manifest)["C12"]
    sink = sink_node(graph, flow["sink"])
    assert sink.attrs["config_conflict"] is True
    regions = {(c["file"], c["value"]) for c in sink.attrs["config"] if c["key"].endswith("region")}
    for expected in flow["conflicting_config"]["values"]:
        assert (expected["file"], expected["value"]) in regions
    assert all(c.get("conflict") for c in sink.attrs["config"] if c["key"].endswith("region"))


@pytest.mark.parametrize(
    "fid,registry_id", [("C02", "mixpanel"), ("C09", "sentry"), ("C10", "stripe")]
)
def test_registry_sinks_have_registry_semantics(
    canary: tuple[dict[str, Any], DataFlowGraph], fid: str, registry_id: str
) -> None:
    manifest, graph = canary
    sink = sink_node(graph, _flows(manifest)[fid]["sink"])
    assert sink.attrs["registry"]["id"] == registry_id
    assert (
        sink.attrs["registry"]["destination_class"]
        == _flows(manifest)[fid]["sink"]["expected_sink_class"]
    )


def test_no_flows_beyond_the_manifest(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    """Precision: every flow in the canary graph is one the manifest documents."""
    manifest, graph = canary
    expected: set[tuple[str, int, str, int]] = set()
    for flow in manifest["flows"]:
        for src in flow["sources"]:
            expected.add((src["file"], src["line"], flow["sink"]["file"], flow["sink"]["line"]))
        if "mitigation" in flow:
            m = flow["mitigation"]["mitigated_sink"]
            for src in flow["sources"]:
                expected.add((src["file"], src["line"], m["file"], m["line"]))
    unexpected = []
    for f in graph.flows:
        src = graph.nodes[f.node_ids[0]]
        snk = next(graph.nodes[i] for i in f.node_ids if graph.nodes[i].kind == "sink")
        key = (src.file, src.line_start, snk.file, snk.line_start)
        if key not in expected:
            field = src.attrs.get("field")
            unexpected.append(f"{src.file}:{src.line_start} {field} -> {snk.file}:{snk.line_start}")
    assert not unexpected, "\n".join(unexpected)


# ---------------------------------------------------------------------------- clean control


def test_clean_fixture_has_zero_paths(clean_python: DataFlowGraph) -> None:
    assert clean_python.flows == []
    assert not [n for n in clean_python.nodes.values() if n.kind == "source"]
    assert clean_python.summary["coverage"]["tainted_paths"] == 0


# ---------------------------------------------------------------------------- output shape


def test_coverage_is_reported(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    _, graph = canary
    coverage = graph.summary["coverage"]
    assert coverage["tainted_paths"] == len(graph.flows) > 0
    assert coverage["paths_with_unresolved_step"] == 1
    assert 0 < coverage["resolved_fraction"] < 1
    assert graph.summary["unreachable_flows"] >= 1


def test_graph_serializes_and_round_trips(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    _, graph = canary
    again = DataFlowGraph.from_json(graph.to_json())
    assert again.to_dict() == graph.to_dict()
    nx_graph = graph.to_networkx()
    assert nx_graph.number_of_nodes() == len(graph.nodes)
    assert nx_graph.number_of_edges() == len(graph.edges)


def test_node_ids_are_stable_across_runs(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    from lantern_analysis.analyze import analyze_repo

    manifest, graph = canary
    again = analyze_repo(FIXTURES / manifest["fixture"], commit="fixture")
    assert sorted(again.nodes) == sorted(graph.nodes)
    assert sorted(f.id for f in again.flows) == sorted(f.id for f in graph.flows)


def test_no_secrets_in_graph(canary: tuple[dict[str, Any], DataFlowGraph]) -> None:
    _, graph = canary
    text = graph.to_json()
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" not in text


EXPECTED_ENDPOINTS = {
    # flow id -> (expected host, evidence kind) on the flow's sink node
    "C02": ("api.mixpanel.com", "registry"),
    "C04": ("api.open-weather-data.example", "env-default"),
    "C08": ("hooks.partner-crm.example", "env-default"),
    "C10": ("api.stripe.com", "registry"),
    "C11": ("legacy-crm.partner.example", "literal"),
    "C12": ("*.amazonaws.com", "registry"),
}


def test_network_sinks_name_their_expected_hosts(
    canary: tuple[dict[str, Any], DataFlowGraph],
) -> None:
    """Dynamic verification matches observed requests against these hosts."""
    manifest, graph = canary
    flows = _flows(manifest)
    for flow_id, (host, evidence) in EXPECTED_ENDPOINTS.items():
        endpoints = sink_node(graph, flows[flow_id]["sink"]).attrs["endpoints"]
        assert host in endpoints["hosts"], (flow_id, endpoints)
        assert endpoints["evidence"][host] == evidence, (flow_id, endpoints)
    for node in graph.nodes.values():
        if node.kind == "sink" and node.attrs.get("family") in ("orm", "log", "file"):
            assert "endpoints" not in node.attrs or not node.attrs["endpoints"]["hosts"]


def test_host_of() -> None:
    from lantern_analysis.endpoints import host_of

    assert host_of("https://publickey@o0.ingest.sentry.io/0") == "o0.ingest.sentry.io"
    assert host_of("https://API.Stripe.com:443/v1") == "api.stripe.com"
    assert host_of("http://localhost:8080/x") == "localhost"
    assert host_of("postgresql://u:p@db.internal:5432/app") is None
    assert host_of("not a url") is None


def _routes(graph: DataFlowGraph) -> set[tuple[str, str]]:
    return {
        (e["method"], e["path"])
        for e in graph.summary["entry_points_detail"]
        if e["kind"] == "route"
    }


def test_python_routes_carry_router_prefixes(canary_python: DataFlowGraph) -> None:
    assert _routes(canary_python) == {
        ("GET", "/healthz"),
        ("POST", "/health/intake"),
        ("POST", "/identity/verify"),
        ("POST", "/location/forecast"),
        ("POST", "/partners/sync"),
        ("POST", "/referrals"),
        ("POST", "/users"),
        ("POST", "/users/{user_id}/export"),
    }


def test_nested_router_and_blueprint_prefixes(tmp_path: Path) -> None:
    from lantern_analysis.analyze import analyze_repo

    (tmp_path / "app" / "api" / "routes").mkdir(parents=True)
    (tmp_path / "app" / "__init__.py").write_text("")
    (tmp_path / "app" / "api" / "__init__.py").write_text("")
    (tmp_path / "app" / "api" / "routes" / "__init__.py").write_text("")
    (tmp_path / "app" / "api" / "routes" / "users.py").write_text(
        "from fastapi import APIRouter\n\n"
        'router = APIRouter(tags=["users"])\n\n\n'
        '@router.get("/me")\n'
        "def read_me():\n    return {}\n"
    )
    (tmp_path / "app" / "api" / "main.py").write_text(
        "from fastapi import APIRouter\n\nfrom app.api.routes import users\n\n"
        "api_router = APIRouter()\n"
        'api_router.include_router(users.router, prefix="/users")\n'
    )
    (tmp_path / "app" / "main.py").write_text(
        "from fastapi import FastAPI\n\nfrom app.api.main import api_router\n\n"
        "app = FastAPI()\n"
        'app.include_router(api_router, prefix="/api/v1")\n'
    )
    (tmp_path / "app" / "admin.py").write_text(
        "from flask import Blueprint, Flask\n\n"
        'bp = Blueprint("admin", __name__, url_prefix="/admin")\n\n\n'
        '@bp.route("/stats")\n'
        "def stats():\n    return {}\n\n\n"
        '@bp.route("/reset", methods=["POST"])\n'
        "def reset():\n    return {}\n\n\n"
        "web = Flask(__name__)\n"
        'web.register_blueprint(bp, url_prefix="/internal")\n'
    )
    graph = analyze_repo(tmp_path, "x")
    routes = _routes(graph)
    assert ("GET", "/api/v1/users/me") in routes
    # register_blueprint's url_prefix replaces the blueprint's own.
    assert ("GET", "/internal/stats") in routes
    assert ("POST", "/internal/reset") in routes
