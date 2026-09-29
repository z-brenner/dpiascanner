"""Scoring shared by the pipeline benchmark and the LLM baseline.

Both systems produce ``Predicted`` source-to-sink pairs and are scored against the fixture
manifests with the same rules:

- A prediction matches a manifest flow when the sink is in the same file within 2 lines of
  the manifest's sink line (or of its mitigated sink, for C09), and the source is in the same
  file within 3 lines of a manifest source line or names the same field. Field names are
  compared case- and separator-insensitively (``full_name`` = ``fullName``).
- Recall: manifest flows with at least one matching prediction, over all manifest flows.
- Precision: predictions that match some manifest flow, over all predictions (duplicates of
  the same source field and sink location counted once).
- False positives on the clean fixture: every prediction there.
- Unresolved and unreachable handling: for flows the manifest marks ``resolution:
  unresolved`` (C08) or ``reachable: false`` (C11), the flow must be found and every
  matching prediction must say so.
- The CI gate requires every flow in a gated fixture to be found, except flows the manifest
  marks ``known_gap`` (patterns added fixture-first that the analyzer does not handle yet),
  and no flows at all on the clean fixture.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SINK_TOLERANCE = 2
SOURCE_TOLERANCE = 3


@dataclass(frozen=True)
class FixtureSpec:
    name: str
    group: str  # planted | clean | real-world
    gated: bool
    dependency_depth: int = 0


BENCHMARK_FIXTURES = (
    FixtureSpec("canary-python", "planted", gated=True),
    FixtureSpec("canary-typescript", "planted", gated=True),
    FixtureSpec("unregistered-sdk-python", "planted", gated=False, dependency_depth=1),
    FixtureSpec("clean-python", "clean", gated=True),
    FixtureSpec("gaps-python", "real-world", gated=True),
)


@dataclass(frozen=True)
class Predicted:
    source_file: str
    source_line: int | None
    field: str | None
    sink_file: str
    sink_line: int
    reachable: bool | None = None
    resolution: str | None = None  # resolved | unresolved

    def key(self) -> tuple[str, str, int]:
        return (
            norm_field(self.field or f"{self.source_file}:{self.source_line}"),
            self.sink_file,
            self.sink_line,
        )


def norm_field(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def norm_file(path: str) -> str:
    path = path.strip().replace("\\", "/")
    return path[2:] if path.startswith("./") else path


def load_manifest(fixture: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((FIXTURES / fixture / "MANIFEST.yaml").read_text())
    return data


def _near(a: int | None, b: int, tolerance: int) -> bool:
    return a is not None and abs(a - b) <= tolerance


def sink_matches(p: Predicted, flow: dict[str, Any]) -> bool:
    sinks = [flow["sink"]]
    if "mitigation" in flow:
        sinks.append(flow["mitigation"]["mitigated_sink"])
    return any(
        norm_file(p.sink_file) == s["file"] and _near(p.sink_line, s["line"], SINK_TOLERANCE)
        for s in sinks
    )


def source_matches(p: Predicted, flow: dict[str, Any]) -> bool:
    for s in flow["sources"]:
        if norm_file(p.source_file) == s["file"] and _near(
            p.source_line, s["line"], SOURCE_TOLERANCE
        ):
            return True
        if p.field and norm_field(p.field) == norm_field(s["field"]):
            return True
    return False


def matches(p: Predicted, flow: dict[str, Any]) -> bool:
    return sink_matches(p, flow) and source_matches(p, flow)


@dataclass
class FlowOutcome:
    id: str
    found: bool
    special: str | None  # unresolved | unreachable | None
    handled: bool | None
    known_gap: str | None
    matched: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FixtureScore:
    fixture: str
    group: str
    flows: list[FlowOutcome] = field(default_factory=list)
    predictions: int = 0
    true_predictions: int = 0
    duration_s: float = 0.0
    finding_categories_correct: int | None = None  # pipeline only

    @property
    def recall(self) -> float | None:
        return sum(f.found for f in self.flows) / len(self.flows) if self.flows else None

    @property
    def precision(self) -> float | None:
        return self.true_predictions / self.predictions if self.predictions else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "group": self.group,
            "recall": self.recall,
            "precision": self.precision,
            "predictions": self.predictions,
            "true_predictions": self.true_predictions,
            "duration_s": round(self.duration_s, 2),
            "finding_categories_correct": self.finding_categories_correct,
            "flows": [f.to_dict() for f in self.flows],
        }


def score_fixture(
    spec: FixtureSpec, predictions: list[Predicted], duration_s: float = 0.0
) -> FixtureScore:
    manifest = load_manifest(spec.name)
    flows = manifest.get("flows", [])
    unique = {p.key(): p for p in predictions}
    preds = list(unique.values())
    score = FixtureScore(spec.name, spec.group, predictions=len(preds), duration_s=duration_s)
    score.true_predictions = sum(1 for p in preds if any(matches(p, f) for f in flows))
    for flow in flows:
        hits = [p for p in preds if matches(p, flow)]
        special = (
            "unresolved"
            if flow.get("resolution") == "unresolved"
            else "unreachable"
            if flow.get("reachable") is False
            else None
        )
        handled: bool | None = None
        if special == "unresolved":
            handled = bool(hits) and all(p.resolution == "unresolved" for p in hits)
        elif special == "unreachable":
            handled = bool(hits) and all(p.reachable is False for p in hits)
        score.flows.append(
            FlowOutcome(flow["id"], bool(hits), special, handled, flow.get("known_gap"), len(hits))
        )
    return score


@dataclass
class Aggregate:
    recall_planted: float
    precision_planted: float | None
    clean_false_positives: int
    special_handled: float | None
    recall_real_world: float | None
    finding_categories: float | None
    wall_clock_s: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate(scores: list[FixtureScore]) -> Aggregate:
    planted = [s for s in scores if s.group == "planted"]
    flows = [f for s in planted for f in s.flows]
    special = [f for f in flows if f.special]
    preds = sum(s.predictions for s in planted)
    real_world = [f for s in scores if s.group == "real-world" for f in s.flows]
    categories = [s for s in planted if s.finding_categories_correct is not None]
    return Aggregate(
        recall_planted=sum(f.found for f in flows) / len(flows) if flows else 0.0,
        precision_planted=sum(s.true_predictions for s in planted) / preds if preds else None,
        clean_false_positives=sum(s.predictions for s in scores if s.group == "clean"),
        special_handled=sum(bool(f.handled) for f in special) / len(special) if special else None,
        recall_real_world=(
            sum(f.found for f in real_world) / len(real_world) if real_world else None
        ),
        finding_categories=(
            sum(s.finding_categories_correct or 0 for s in categories) / len(flows)
            if categories and flows
            else None
        ),
        wall_clock_s=round(sum(s.duration_s for s in scores), 2),
    )


GATED = frozenset(spec.name for spec in BENCHMARK_FIXTURES if spec.gated)


def gate(scores: list[FixtureScore]) -> list[str]:
    """CI gate: every flow found in the gated fixtures, known gaps aside, and none on clean."""
    failures = []
    for s in scores:
        if s.fixture not in GATED:
            continue
        if s.group == "clean":
            if s.predictions > 0:
                failures.append(f"{s.fixture}: {s.predictions} false positive flows")
            continue
        missed = [f.id for f in s.flows if not f.found and not f.known_gap]
        if missed:
            failures.append(f"{s.fixture}: recall {s.recall:.0%}, missed {', '.join(missed)}")
    return failures
