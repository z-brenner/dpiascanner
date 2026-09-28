import json

from lantern_analysis.model import DataFlowGraph
from lantern_analysis.secrets import contains_secret, find_secrets, redact
from lantern_analysis.state import MAX_SNIPPET_CHARS, MAX_STEPS, build_targets


def test_states_cover_every_target_on_a_flow(canary_python: DataFlowGraph) -> None:
    targets = build_targets(canary_python)
    by_type = {t.target_type for t in targets}
    assert by_type == {"source", "edge", "sink", "retention"}
    on_flow = {i for f in canary_python.flows for i in [*f.node_ids, *f.edge_ids]}
    classifiable = {
        i
        for i in on_flow
        if (i in canary_python.edges and canary_python.edges[i].kind == "flow")
        or (i in canary_python.nodes and canary_python.nodes[i].kind != "transform")
    }
    assert {t.target_id for t in targets} == classifiable


def test_states_carry_the_evidence_the_model_needs(canary_python: DataFlowGraph) -> None:
    targets = build_targets(canary_python)
    for t in targets:
        s = t.state
        assert s["schema"] == "lantern.state/v1" and s["target_type"] == t.target_type
        assert s["location"]["file"] and s["snippet"]
        assert len(s["snippet"]) <= MAX_SNIPPET_CHARS
        if t.target_type == "source":
            assert s["lexicon_hints"] and s["field"]
        if t.target_type == "edge":
            assert s["evidence_chain"] and len(s["evidence_chain"]) <= MAX_STEPS
            assert s["upstream"]["lexicon_categories"]
            assert s["call_chain"]
        if t.target_type == "sink":
            assert s["call_chain"]
    mixpanel = next(
        t
        for t in targets
        if t.target_type == "sink" and (t.state.get("registry") or {}).get("id") == "mixpanel"
    )
    assert mixpanel.state["registry"]["destination_class"] == "analytics"
    s3 = next(
        t
        for t in targets
        if t.target_type == "sink" and (t.state.get("registry") or {}).get("id") == "aws-python"
    )
    assert s3.state["flags"]["conflicting_config"] is True


def test_no_secrets_reach_any_state(
    canary_python: DataFlowGraph, canary_typescript: DataFlowGraph
) -> None:
    for graph in (canary_python, canary_typescript):
        text = json.dumps([t.state for t in build_targets(graph)])
        assert "AKIAIOSFODNN7EXAMPLE" not in text
        assert "wJalrXUtnFEMI" not in text
        assert not contains_secret(text)


def test_secrets_scanner_patterns() -> None:
    samples = {
        "aws_access_key": 'key = "AKIAIOSFODNN7EXAMPLE"',
        "github_token": "token ghp_" + "a" * 36,
        "private_key": "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
        "url_credentials": "postgres://admin:hunter2secret@db.internal/app",
        "assigned_secret": 'SIGNING_SECRET = "9f8e7d6c5b4a3f2e1d0c"',
    }
    for kind, text in samples.items():
        assert any(m.kind == kind for m in find_secrets(text)), kind
        assert "[REDACTED" in redact(text)
    assert not find_secrets('password_field = "replace-me"')
    assert not find_secrets("email = payload.email")
