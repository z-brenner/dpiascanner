from lantern_analysis.model import DataFlowGraph, Node
from lantern_report.findings import assemble_findings


def dynamic_node(node_id: str, host: str, canaries: list[str]) -> Node:
    return Node(
        node_id,
        "sink",
        "<dynamic>",
        0,
        0,
        f"observed request to {host}",
        "",
        rule_ids=["dynamic.observed-unexpected"],
        attrs={
            "family": "http",
            "evidence": "dynamic-only",
            "dynamic": {
                "status": "observed-unexpected",
                "hosts": [host],
                "canaries": canaries,
                "methods": ["POST"],
                "paths": ["/collect"],
            },
        },
    )


def test_observed_unexpected_destinations_become_findings():
    g = DataFlowGraph(repo="r", commit="abc", languages=["python"])
    for node in (
        dynamic_node("N-a", "collector.unknown.example", ["email", "ssn"]),
        dynamic_node("N-b", "telemetry.unknown.example", []),
    ):
        g.nodes[node.id] = node
    result = assemble_findings(g, [])
    by_host = {f.evidence["host"]: f for f in result.findings}
    leaking = by_host["collector.unknown.example"]
    assert leaking.category == "observed_unexpected_destination"
    assert leaking.data_categories == ["contact", "government_id"]
    assert leaking.destination_class == "unknown_third_party"
    assert leaking.severity == "critical"  # medium, +1 sensitive data, +1 risky destination
    assert "GDPR-30" in leaking.statute_refs
    quiet = by_host["telemetry.unknown.example"]
    assert quiet.severity == "low"
    assert quiet.severity_modifiers == ["no_personal_data_observed (-1)"]
    assert quiet.key != leaking.key
