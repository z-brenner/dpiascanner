from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from lantern_decisions.calibration import (
    expected_calibration_error,
    load_dataset,
    run_calibration,
    write_report,
)
from lantern_decisions.calibration.from_manifests import build
from lantern_decisions.distributions import shaped_distribution
from lantern_decisions.provider import DecisionProvider, resolve_question_set
from lantern_decisions.stub import StubProvider
from lantern_decisions.types import DecisionRequest, DecisionResult

REPO = Path(__file__).resolve().parents[3]


def test_ece_on_a_hand_computed_example() -> None:
    ece, bins = expected_calibration_error([0.9, 0.9, 0.6, 0.6], [True, False, True, True], 10)
    # bin (0.8, 0.9]: acc 0.5 vs conf 0.9 -> 0.4 * 2/4; bin (0.5, 0.6]: 1.0 vs 0.6 -> 0.4 * 2/4
    assert abs(ece - 0.4) < 1e-9
    assert sum(b.count for b in bins) == 4


def test_shipped_dataset_is_derived_from_manifests_and_current() -> None:
    shipped = load_dataset()
    rebuilt = build(REPO / "fixtures")
    assert [e.to_dict() for e in shipped] == [e.to_dict() for e in rebuilt], (
        "calibration dataset is stale; rerun lantern_decisions.calibration.from_manifests"
    )
    assert len(shipped) >= 60
    kinds = {e.target_type.value for e in shipped}
    assert kinds == {"source", "edge", "sink", "retention"}
    for example in shipped:
        assert "lexicon_hints" not in example.state, "hints would leak labels"


def test_stub_calibration_report(tmp_path: Path) -> None:
    report = run_calibration(StubProvider(), load_dataset())
    assert report.predictions > report.examples
    assert 0.0 <= report.ece <= 1.0
    assert "source.data_category" in report.per_question
    confusion = report.per_question["source.data_category"].confusion
    assert (
        sum(sum(row.values()) for row in confusion.values())
        == report.per_question["source.data_category"].count
    )
    paths = write_report(report, tmp_path)
    assert paths["reliability"].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert "Confusion matrices" in paths["markdown"].read_text()


class _Oracle(DecisionProvider):
    """Knows every label and reports 0.7 confidence; wrong 30% of the time by design."""

    name: ClassVar[str] = "oracle"

    def __init__(self, labels: dict[str, dict[str, str]]) -> None:
        self.labels = labels
        self.counter = 0

    @property
    def version(self) -> str:
        return "1"

    def _decide_batch(self, requests: Sequence[DecisionRequest]) -> list[DecisionResult]:
        out = []
        for r in requests:
            qs = resolve_question_set(r)
            for q in qs.questions:
                labeled = q.id in self.labels[r.target_id]
                self.counter += labeled
                truth = self.labels[r.target_id].get(q.id, q.option_ids[0])
                wrong = next(o for o in q.option_ids if o != truth)
                answer = truth if (self.counter % 10 < 7 or not labeled) else wrong
                dist = shaped_distribution(q, answer, 0.7, r.target_id + q.id)
                out.append(
                    DecisionResult(
                        r.target_id,
                        r.target_type,
                        qs.id,
                        qs.version,
                        q.id,
                        answer,
                        dist[answer],
                        dist,
                        self.name,
                        self.version,
                        r.state_hash,
                    )
                )
        return out


def test_a_calibrated_provider_has_low_ece() -> None:
    examples = load_dataset()
    oracle = _Oracle({e.example_id: dict(e.labels) for e in examples})
    report = run_calibration(oracle, examples)
    assert abs(report.accuracy - 0.7) < 0.08
    assert report.ece < 0.03
