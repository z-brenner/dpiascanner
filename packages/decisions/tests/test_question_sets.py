from dataclasses import replace

import pytest
import yaml

from lantern_decisions.question_sets import (
    QuestionKind,
    QuestionSet,
    QuestionSetError,
    TargetType,
    available_versions,
    load_bundle,
)


def test_v1_covers_every_target_and_matches_lock() -> None:
    bundle = load_bundle("v1")
    assert set(bundle.sets) == set(TargetType)
    assert {qs.name for qs in bundle.sets.values()} == {
        "source_v1",
        "edge_v1",
        "sink_v1",
        "retention_v1",
    }
    assert "v1" in available_versions()


def test_v1_questions_match_the_spec() -> None:
    bundle = load_bundle("v1")
    source = bundle.for_target("source")
    assert [q.id for q in source.questions] == [
        "data_category",
        "special_category_art9",
        "relates_to_minor",
        "identifiability",
    ]
    assert source.question("data_category").option_ids == (
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
    )
    assert source.question("identifiability").option_ids == ("1", "2", "3", "4", "5")
    edge = bundle.for_target("edge")
    assert "reversible_encoding" in edge.question("transformation").option_ids
    sink = bundle.for_target("sink")
    assert len(sink.question("destination_class").options) == 11
    assert [q.id for q in sink.questions] == ["destination_class", "sale_or_share_cpra", "purpose"]
    retention = bundle.for_target("retention")
    assert retention.question("plausible_retention").option_ids == (
        "session",
        "days",
        "months",
        "years",
        "indefinite",
    )


def test_every_question_and_option_is_described() -> None:
    for qs in load_bundle("v1").sets.values():
        for q in qs.questions:
            assert len(q.description) > 20, q.id
            for option in q.options:
                assert option.description, f"{q.id}.{option.id}"
            if q.kind is QuestionKind.YES_NO:
                assert q.option_ids == ("yes", "no")


def test_yaml_round_trip_is_lossless() -> None:
    for qs in load_bundle("v1").sets.values():
        assert QuestionSet.from_yaml(qs.to_yaml()) == qs
        assert QuestionSet.from_yaml(qs.to_yaml()).fingerprint() == qs.fingerprint()


def test_any_edit_changes_the_fingerprint() -> None:
    qs = load_bundle("v1").for_target("source")
    q = qs.questions[0]
    edited = replace(qs, questions=(replace(q, description=q.description + " "), *qs.questions[1:]))
    assert edited.fingerprint() != qs.fingerprint()


def test_malformed_question_sets_are_rejected() -> None:
    base = load_bundle("v1").for_target("retention").to_dict()
    bad = yaml.safe_load(yaml.safe_dump(base))
    bad["questions"][0]["options"] = [
        {"id": "true", "description": "x"},
        {"id": "false", "description": "y"},
    ]
    with pytest.raises(QuestionSetError):
        QuestionSet.from_dict(bad)
    bad = yaml.safe_load(yaml.safe_dump(load_bundle("v1").for_target("source").to_dict()))
    del bad["questions"][3]["anchors"][3]
    with pytest.raises(QuestionSetError):
        QuestionSet.from_dict(bad)
