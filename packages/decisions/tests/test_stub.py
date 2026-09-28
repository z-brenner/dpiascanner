from lantern_decisions.question_sets import TargetType
from lantern_decisions.stub import Condition, StubProvider, StubRules
from lantern_decisions.testing import make_request

RULES = """
version: test-rules
rules:
  - name: contact
    applies_to: [source]
    when: [{path: lexicon_hints.category, equals: contact}]
    confidence: 0.9
    answers:
      data_category: contact
      identifiability: {answer: "5", probability: 0.8}
  - name: registry analytics
    applies_to: [sink]
    when: [{path: registry.destination_class, in: [analytics]}]
    unless: [{path: flags.first_party, equals: true}]
    answers:
      destination_class: analytics
defaults:
  source:
    data_category: {answer: none, probability: 0.5}
lookup: {}
"""


def _answers(provider: StubProvider, request):  # type: ignore[no-untyped-def]
    return {r.question_id: r for r in provider.decide([request])}


def test_conditions() -> None:
    state = {"a": [{"b": "x"}, {"b": "hello world"}], "n": None, "flag": True}
    assert Condition.parse({"path": "a.b", "equals": "x"}).holds(state)
    assert Condition.parse({"path": "a.b", "contains": "world"}).holds(state)
    assert Condition.parse({"path": "a.b", "matches": "^hel"}).holds(state)
    assert Condition.parse({"path": "a.b", "in": ["y", "x"]}).holds(state)
    assert Condition.parse({"path": "a.b", "exists": True}).holds(state)
    assert Condition.parse({"path": "n", "exists": False}).holds(state)
    assert not Condition.parse({"path": "missing", "exists": True}).holds(state)
    assert Condition.parse({"path": "flag", "equals": True}).holds(state)


def test_rule_default_and_uniform_precedence() -> None:
    provider = StubProvider(StubRules.from_yaml(RULES))
    hinted = make_request(TargetType.SOURCE, lexicon_hints=[{"category": "contact"}])
    answers = _answers(provider, hinted)
    assert answers["data_category"].answer == "contact"
    assert answers["data_category"].probability == 0.9
    assert answers["data_category"].raw["basis"] == "rule:contact"
    assert answers["identifiability"].answer == "5"
    assert answers["special_category_art9"].raw["basis"] == "uniform"
    assert answers["special_category_art9"].probability == 0.5

    bare = _answers(provider, make_request(TargetType.SOURCE))
    assert bare["data_category"].answer == "none"
    assert bare["data_category"].raw["basis"] == "default"


def test_unless_blocks_a_rule() -> None:
    provider = StubProvider(StubRules.from_yaml(RULES))
    request = make_request(
        TargetType.SINK,
        registry={"destination_class": "analytics"},
        flags={"first_party": True},
    )
    assert _answers(provider, request)["destination_class"].raw["basis"] == "uniform"


def test_lookup_pins_win() -> None:
    request = make_request(TargetType.SOURCE, lexicon_hints=[{"category": "contact"}])
    pinned = RULES.replace(
        "lookup: {}",
        f"lookup:\n  {request.state_hash}:\n"
        "    data_category: {answer: health, probability: 0.97}",
    )
    answers = _answers(StubProvider(StubRules.from_yaml(pinned)), request)
    assert answers["data_category"].answer == "health"
    assert answers["data_category"].raw["basis"] == "lookup"


def test_scores_spread_to_neighbors() -> None:
    provider = StubProvider(StubRules.from_yaml(RULES))
    hinted = make_request(TargetType.SOURCE, lexicon_hints=[{"category": "contact"}])
    dist = _answers(provider, hinted)["identifiability"].distribution
    assert dist["5"] == 0.8
    assert dist["4"] > dist["3"] > dist["1"]


def test_version_tracks_rule_content() -> None:
    a = StubProvider(StubRules.from_yaml(RULES))
    b = StubProvider(StubRules.from_yaml(RULES.replace("0.9", "0.91")))
    assert a.version != b.version
    assert a.version.startswith("test-rules+")


def test_shipped_rules_parse_and_classify_hints() -> None:
    provider = StubProvider()
    health = _answers(
        provider,
        make_request(
            TargetType.SOURCE, lexicon_hints=[{"category": "health", "term": "condition"}]
        ),
    )
    assert health["data_category"].answer == "health"
    assert health["special_category_art9"].answer == "yes"
    edge = _answers(
        provider, make_request(TargetType.EDGE, transforms=[{"kind": "reversible_encoding"}])
    )
    assert edge["transformation"].answer == "reversible_encoding"
    retention = _answers(provider, make_request(TargetType.RETENTION))
    assert retention["has_expiry"].answer == "no"
