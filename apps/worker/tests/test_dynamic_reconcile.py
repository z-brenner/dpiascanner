from lantern_analysis.model import DataFlowGraph, Edge, Flow, Node
from lantern_worker.dynamic.canaries import CanaryHit
from lantern_worker.dynamic.observed import ObservedRequest
from lantern_worker.dynamic.reconcile import apply, host_matches, reconcile


def sink(node_id, family, hosts, reachable=True, registry=None, file="app/x.py"):
    attrs = {
        "family": family,
        "reachable": reachable,
        "endpoints": {"hosts": hosts, "evidence": {h: "literal" for h in hosts}},
    }
    if registry:
        attrs["registry"] = {"id": registry}
    return Node(node_id, "sink", file, 1, 1, node_id, "python", attrs=attrs)


def request(host, *kinds):
    return ObservedRequest(
        host=host,
        method="POST",
        path="/v1/x",
        scheme="https",
        headers={},
        query_keys=[],
        body_sha256="0" * 64,
        body_size=10,
        body_field_names=["email"],
        canary_hits=[CanaryHit(k, "plain", "body") for k in kinds],
    )


def graph() -> DataFlowGraph:
    g = DataFlowGraph(repo="r", commit="c", languages=["python"])
    source = Node("N-src", "source", "app/r.py", 1, 1, "ssn", "python", attrs={"field": "ssn"})
    nodes = [
        source,
        sink("N-sentry-a", "registry", ["*.ingest.sentry.io"], registry="sentry"),
        sink("N-sentry-b", "registry", ["*.ingest.sentry.io"], registry="sentry"),
        sink("N-weather", "http", ["api.weather.example"]),
        sink("N-legacy", "http", ["legacy.example"], reachable=False),
        sink("N-quiet", "http", ["quiet.example"]),
        sink("N-hostless", "http", []),
        sink("N-orm", "orm", []),
        sink("N-log", "log", []),
    ]
    for n in nodes:
        g.nodes[n.id] = n
    g.edges["E-1"] = Edge("E-1", "N-src", "N-log", "flow")
    g.flows.append(Flow("P-1", ["N-src", "N-log"], ["E-1"], reachable=True, unresolved=False))
    return g


def test_host_patterns():
    assert host_matches("*.ingest.sentry.io", "o0.ingest.sentry.io")
    assert host_matches("*.amazonaws.com", "bucket.s3.eu-west-1.amazonaws.com")
    assert host_matches("api.stripe.com", "API.STRIPE.COM")
    assert not host_matches("*.ingest.sentry.io", "ingest.sentry.io.evil.example")
    assert not host_matches("api.stripe.com", "files.stripe.com")


def test_statuses():
    g = graph()
    observed = [
        request("o0.ingest.sentry.io", "email"),
        request("api.weather.example"),
        request("collector.unknown.example", "email", "ssn"),
    ]
    output = [CanaryHit("ssn", "base64", "process-output")]
    rec = reconcile(g, observed, output)
    status = {k: (v.status, v.reason) for k, v in rec.sinks.items()}
    assert status["N-sentry-a"] == ("verified", "canary-observed")
    assert status["N-sentry-b"] == ("verified", "canary-observed")
    assert rec.sinks["N-sentry-a"].shared_host_with == ["N-sentry-b"]
    assert status["N-weather"] == ("inferred", "observed-without-canary")
    assert status["N-legacy"] == ("inferred", "unreachable")
    assert status["N-quiet"] == ("inferred", "not-exercised")
    assert status["N-hostless"] == ("inferred", "no-known-host")
    assert status["N-orm"] == ("inferred", "not-network-observable")
    assert status["N-log"] == ("verified", "canary-in-process-output")
    [unexpected] = rec.unexpected
    assert unexpected.host == "collector.unknown.example"
    assert unexpected.canaries == ["email", "ssn"]
    assert unexpected.candidate_sinks == ["N-hostless"]


def test_log_sink_needs_a_matching_kind():
    rec = reconcile(graph(), [], [CanaryHit("email", "plain", "process-output")])
    assert rec.sinks["N-log"].reason == "not-observed-in-output"


def test_apply_writes_status_and_dynamic_only_nodes():
    g = graph()
    rec = reconcile(g, [request("collector.unknown.example", "email")])
    apply(g, rec, {"backend": "test"})
    assert g.nodes["N-weather"].attrs["dynamic"]["status"] == "inferred"
    [dyn] = [n for n in g.nodes.values() if n.file == "<dynamic>"]
    assert dyn.attrs["evidence"] == "dynamic-only"
    assert dyn.attrs["dynamic"]["status"] == "observed-unexpected"
    assert g.summary["dynamic"]["counts"]["observed-unexpected"] == 1
    # The dynamic-only node survives serialization and is not re-reported on a second pass.
    again = DataFlowGraph.from_json(g.to_json())
    assert (
        reconcile(again, [request("collector.unknown.example", "email")]).unexpected[0].node_id
        == dyn.id
    )
