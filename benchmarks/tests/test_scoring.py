from __future__ import annotations

from llm_baseline import parse_reply
from scoring import BENCHMARK_FIXTURES, Predicted, aggregate, gate, load_manifest, score_fixture

CANARY = next(s for s in BENCHMARK_FIXTURES if s.name == "canary-python")
CLEAN = next(s for s in BENCHMARK_FIXTURES if s.name == "clean-python")


def perfect(fixture: str) -> list[Predicted]:
    out = []
    for flow in load_manifest(fixture)["flows"]:
        for src in flow["sources"]:
            out.append(
                Predicted(
                    src["file"],
                    src["line"],
                    src["field"],
                    flow["sink"]["file"],
                    flow["sink"]["line"],
                    reachable=flow["reachable"],
                    resolution=flow["resolution"],
                )
            )
    return out


def test_perfect_predictions_score_perfectly() -> None:
    score = score_fixture(CANARY, perfect("canary-python"))
    assert score.recall == 1.0 and score.precision == 1.0
    assert all(f.handled for f in score.flows if f.special)
    assert {f.id for f in score.flows if f.special} == {"C08", "C11"}


def test_llm_style_offsets_and_field_spelling_still_match() -> None:
    preds = [
        Predicted("./app/routes/users.py", 26, "Email", "app/analytics.py", 13),  # off by one, case
        Predicted("app/schemas.py", 3, "fullName", "app/billing.py", 11),  # camelCase, other file
    ]
    score = score_fixture(CANARY, preds)
    found = {f.id for f in score.flows if f.found}
    assert {"C02", "C10"} <= found
    assert score.precision == 1.0


def test_missing_flows_extra_flows_and_wrong_resolution() -> None:
    preds = [p for p in perfect("canary-python") if p.sink_file != "app/partners.py"]
    preds.append(
        Predicted("app/routes/partners.py", 10, "phone", "app/partners.py", 11, True, "resolved")
    )
    preds.append(
        Predicted("app/routes/users.py", 27, "plan", "app/routes/health.py", 17)
    )  # wrong sink
    score = score_fixture(CANARY, preds)
    c08 = next(f for f in score.flows if f.id == "C08")
    assert c08.found and c08.handled is False  # found, but claimed resolved
    assert score.precision is not None and score.precision < 1.0


def test_clean_predictions_are_false_positives_and_fail_the_gate() -> None:
    clean = score_fixture(
        CLEAN, [Predicted("app/models.py", 10, "name", "app/routes/products.py", 20)]
    )
    canary = score_fixture(CANARY, perfect("canary-python")[:-3])
    failures = gate([canary, clean])
    assert any("clean-python: 1 false positive" in f for f in failures)
    assert any(f.startswith("canary-python: recall") for f in failures)
    assert aggregate([canary, clean]).clean_false_positives == 1
    assert gate([score_fixture(CANARY, perfect("canary-python")), score_fixture(CLEAN, [])]) == []


def test_parse_reply_tolerates_fences_and_skips_bad_entries() -> None:
    reply = """Here are the flows:
```json
{"flows": [
  {"source": {"file": "app/routes/users.py", "line": 27, "field": "email"},
   "sink": {"file": "app/analytics.py", "line": 12}, "reachable": true, "resolution": "resolved"},
  {"source": {"file": "x.py"}, "sink": {"file": "y.py"}},
  {"source": {"file": "a.py", "line": "7", "field": "ssn"}, "sink": {"file": "b.py", "line": 9}}
]}
```"""
    preds = parse_reply(reply)
    assert [(p.field, p.sink_line) for p in preds] == [("email", 12), ("ssn", 9)]
    assert parse_reply("not json") == []
