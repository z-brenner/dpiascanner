"""The analyzer attaches registry semantics and mitigation hooks (Prompt 4 acceptance tests)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from lantern_analysis.analyze import AnalysisOptions, analyze_repo
from lantern_analysis.model import DataFlowGraph, Node
from lantern_registry.resolver import LocalDirectorySource

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
_GRAPHS: dict[str, DataFlowGraph] = {}


def graph(fixture: str) -> DataFlowGraph:
    if fixture not in _GRAPHS:
        _GRAPHS[fixture] = analyze_repo(FIXTURES / fixture)
    return _GRAPHS[fixture]


def manifest_flow(fixture: str, fid: str) -> dict[str, Any]:
    data = yaml.safe_load((FIXTURES / fixture / "MANIFEST.yaml").read_text())
    return next(f for f in data["flows"] if f["id"] == fid)


def sink_at(g: DataFlowGraph, loc: dict[str, Any]) -> Node:
    return next(
        n
        for n in g.nodes.values()
        if n.kind == "sink" and n.file == loc["file"] and n.line_start == loc["line"]
    )


@pytest.mark.parametrize("fixture", ["canary-python", "canary-typescript"])
@pytest.mark.parametrize(
    "fid,registry_id,destination",
    [
        ("C02", "mixpanel", "analytics"),
        ("C09", "sentry", "log"),
        ("C10", "stripe", "payment_processor"),
    ],
)
def test_registry_semantics_attached(
    fixture: str, fid: str, registry_id: str, destination: str
) -> None:
    node = sink_at(graph(fixture), manifest_flow(fixture, fid)["sink"])
    registry = node.attrs["registry"]
    assert registry["id"] == registry_id
    assert registry["destination_class"] == destination
    assert registry["dpa_url"].startswith("https://")
    assert registry["auto_collected"]
    assert any(rule.endswith(f".sink.registry.{registry_id}") for rule in node.rule_ids)


@pytest.mark.parametrize("fixture", ["canary-python", "canary-typescript"])
def test_before_send_is_a_partial_mitigation(fixture: str) -> None:
    g = graph(fixture)
    flow = manifest_flow(fixture, "C09")
    mitigated_sink = sink_at(g, flow["mitigation"]["mitigated_sink"])
    leaking_sink = sink_at(g, flow["sink"])
    hooks = [h for h in mitigated_sink.attrs["mitigations"] if h["kind"] == "event_scrubber"]
    assert hooks and hooks[0]["scrubs"] == [["user", "email"], ["user", "ip_address"]]
    to_mitigated = [f for f in g.flows if mitigated_sink.id in f.node_ids]
    to_leak = [f for f in g.flows if leaking_sink.id in f.node_ids]
    assert to_mitigated and all(f.mitigated for f in to_mitigated)
    assert to_leak and not any(f.mitigated for f in to_leak)
    flags = {
        h["hook"]: h["value"] for h in mitigated_sink.attrs["mitigations"] if h["kind"] == "flag"
    }
    assert flags.get("send_default_pii", flags.get("sendDefaultPii")) is False


def test_unregistered_sdk_found_through_dependency_source() -> None:
    g = analyze_repo(
        FIXTURES / "unregistered-sdk-python",
        options=AnalysisOptions(dependency_depth=1),
        package_source=LocalDirectorySource(FIXTURES / "dependency-mirror"),
    )
    flow = manifest_flow("unregistered-sdk-python", "D01")
    node = sink_at(g, flow["sink"])
    assert node.attrs["dependency"]["package"] == "acme-geo"
    assert node.attrs["dependency"]["endpoints"] == ["api.acme-geo.example"]
    assert node.attrs["dependency"]["evidence"] == "inferred-from-dependency-source"
    fields = {g.nodes[f.node_ids[0]].attrs["field"] for f in g.flows if node.id in f.node_ids}
    assert fields == {"latitude", "longitude"}
    deps = {d["name"]: d for d in g.summary["dependencies"]["profiles"]}
    assert deps["acme-geo"]["network_methods"] == ["locate"]
    assert deps["tiny-utils"]["status"] == "no-network"
    assert not [n for n in g.nodes.values() if n.kind == "sink" and "ping" in n.symbol]


def test_without_dependency_profiling_the_unregistered_sdk_is_invisible() -> None:
    g = analyze_repo(FIXTURES / "unregistered-sdk-python")
    assert g.flows == []
    assert g.summary["dependencies"]["depth"] == 0
