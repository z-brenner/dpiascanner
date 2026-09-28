"""Calibration harness: accuracy, expected calibration error, reliability diagram, confusion.

A provider is calibrated when, among answers given with probability p, a fraction p is
correct. Expected calibration error (ECE) is the count-weighted mean gap between accuracy
and mean confidence across equal-width confidence bins.

Run it before trusting a provider on anything but the fixtures:

    python -m lantern_decisions.calibration --provider jev --out calibration-report/
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from lantern_decisions.calibration.dataset import LabeledExample
from lantern_decisions.provider import DecisionProvider
from lantern_decisions.question_sets import QuestionKind, load_bundle

SURFACE = "#fcfcfb"
SERIES = "#2a78d6"
REFERENCE = "#52514e"
TEXT = "#0b0b0b"


@dataclass(frozen=True)
class Prediction:
    example_id: str
    question_key: str  # "<question_set>.<question_id>"
    label: str
    answer: str
    probability: float
    correct: bool


@dataclass(frozen=True)
class BinStat:
    lower: float
    upper: float
    count: int
    accuracy: float | None
    confidence: float | None


@dataclass
class QuestionMetrics:
    question_key: str
    count: int
    accuracy: float
    ece: float
    confusion: dict[str, dict[str, int]]
    mean_absolute_error: float | None = None


@dataclass
class CalibrationReport:
    provider: str
    provider_version: str
    examples: int
    predictions: int
    accuracy: float
    ece: float
    bins: list[BinStat]
    per_question: dict[str, QuestionMetrics]
    misses: list[Prediction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def expected_calibration_error(
    confidences: Sequence[float], correct: Sequence[bool], n_bins: int = 10
) -> tuple[float, list[BinStat]]:
    if len(confidences) != len(correct):
        raise ValueError("confidences and correct must be the same length")
    n = len(confidences)
    bins: list[BinStat] = []
    ece = 0.0
    for i in range(n_bins):
        lower, upper = i / n_bins, (i + 1) / n_bins
        members = [
            j for j, c in enumerate(confidences) if (lower < c <= upper) or (i == 0 and c == 0.0)
        ]
        if not members:
            bins.append(BinStat(lower, upper, 0, None, None))
            continue
        acc = sum(correct[j] for j in members) / len(members)
        conf = math.fsum(confidences[j] for j in members) / len(members)
        ece += len(members) / n * abs(acc - conf) if n else 0.0
        bins.append(BinStat(lower, upper, len(members), acc, conf))
    return ece, bins


def run_calibration(
    provider: DecisionProvider, examples: Sequence[LabeledExample], n_bins: int = 10
) -> CalibrationReport:
    results = provider.decide([e.to_request() for e in examples])
    by_target: dict[str, dict[str, tuple[str, float]]] = defaultdict(dict)
    for r in results:
        by_target[r.target_id][r.question_id] = (r.answer, r.probability)

    predictions: list[Prediction] = []
    for example in examples:
        for question_id, label in sorted(example.labels.items()):
            answer, probability = by_target[example.example_id][question_id]
            predictions.append(
                Prediction(
                    example_id=example.example_id,
                    question_key=f"{example.question_set_id}.{question_id}",
                    label=label,
                    answer=answer,
                    probability=probability,
                    correct=answer == label,
                )
            )

    ece, bins = expected_calibration_error(
        [p.probability for p in predictions], [p.correct for p in predictions], n_bins
    )
    per_question: dict[str, QuestionMetrics] = {}
    grouped: dict[str, list[Prediction]] = defaultdict(list)
    for p in predictions:
        grouped[p.question_key].append(p)
    versions = {e.question_set_version for e in examples}
    kinds = {
        f"{qs.id}.{q.id}": q.kind
        for version in versions
        for qs in load_bundle(version).sets.values()
        for q in qs.questions
    }
    for key, preds in sorted(grouped.items()):
        confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for p in preds:
            confusion[p.label][p.answer] += 1
        q_ece, _ = expected_calibration_error(
            [p.probability for p in preds], [p.correct for p in preds], n_bins
        )
        mae = None
        if kinds.get(key) is QuestionKind.SCORE:
            mae = sum(abs(int(p.answer) - int(p.label)) for p in preds) / len(preds)
        per_question[key] = QuestionMetrics(
            question_key=key,
            count=len(preds),
            accuracy=sum(p.correct for p in preds) / len(preds),
            ece=q_ece,
            confusion={k: dict(v) for k, v in confusion.items()},
            mean_absolute_error=mae,
        )

    return CalibrationReport(
        provider=provider.name,
        provider_version=provider.version,
        examples=len(examples),
        predictions=len(predictions),
        accuracy=sum(p.correct for p in predictions) / len(predictions) if predictions else 0.0,
        ece=ece,
        bins=bins,
        per_question=per_question,
        misses=[p for p in predictions if not p.correct],
    )


def plot_reliability(report: CalibrationReport, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax, hist) = plt.subplots(
        2, 1, figsize=(5.2, 6.0), gridspec_kw={"height_ratios": [3, 1]}, facecolor=SURFACE
    )
    width = 1.0 / len(report.bins)
    filled = [b for b in report.bins if b.count]
    ax.set_facecolor(SURFACE)
    ax.plot([0, 1], [0, 1], color=REFERENCE, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(0.05, 0.12, "perfect calibration", color=REFERENCE, fontsize=8, rotation=41)
    ax.bar(
        [b.lower + width / 2 for b in filled],
        [b.accuracy or 0.0 for b in filled],
        width=width - 0.01,
        color=SERIES,
        edgecolor=SURFACE,
        linewidth=2,
    )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_ylabel("Accuracy", color=TEXT)
    ax.set_title(
        f"Reliability: {report.provider} (ECE {report.ece:.3f}, n={report.predictions})",
        color=TEXT,
        fontsize=10,
        loc="left",
    )
    hist.set_facecolor(SURFACE)
    hist.bar(
        [b.lower + width / 2 for b in report.bins],
        [b.count for b in report.bins],
        width=width - 0.01,
        color=SERIES,
        edgecolor=SURFACE,
        linewidth=2,
    )
    hist.set_xlim(0, 1)
    hist.set_xlabel("Predicted probability of the chosen answer", color=TEXT)
    hist.set_ylabel("Count", color=TEXT)
    for axis in (ax, hist):
        axis.grid(axis="y", color="#e4e3df", linewidth=0.6)
        axis.set_axisbelow(True)
        for spine in ("top", "right"):
            axis.spines[spine].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def render_markdown(report: CalibrationReport) -> str:
    lines = [
        f"# Calibration report: {report.provider} ({report.provider_version})",
        "",
        f"- Examples: {report.examples}",
        f"- Predictions: {report.predictions}",
        f"- Accuracy: {report.accuracy:.3f}",
        f"- Expected calibration error: {report.ece:.3f}",
        "",
        "![Reliability diagram](reliability.png)",
        "",
        "## Per question",
        "",
        "| Question | n | Accuracy | ECE | MAE |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, m in report.per_question.items():
        mae = f"{m.mean_absolute_error:.2f}" if m.mean_absolute_error is not None else ""
        lines.append(f"| `{key}` | {m.count} | {m.accuracy:.3f} | {m.ece:.3f} | {mae} |")
    lines += ["", "## Confusion matrices", "", "Rows are labels, columns are answers.", ""]
    for key, m in report.per_question.items():
        answers = sorted({a for row in m.confusion.values() for a in row} | set(m.confusion))
        lines += [f"### `{key}`", "", "| label \\ answer | " + " | ".join(answers) + " |"]
        lines.append("|---|" + "---:|" * len(answers))
        for label in sorted(m.confusion):
            counts = [str(m.confusion[label].get(a, 0)) for a in answers]
            lines.append(f"| {label} | " + " | ".join(counts) + " |")
        lines.append("")
    if report.misses:
        lines += [
            "## Misses",
            "",
            "| Example | Question | Label | Answer | p |",
            "|---|---|---|---|---:|",
        ]
        for p in report.misses:
            lines.append(
                f"| {p.example_id} | `{p.question_key}` | {p.label} | {p.answer} "
                f"| {p.probability:.2f} |"
            )
    return "\n".join(lines) + "\n"


def write_report(report: CalibrationReport, out_dir: Path | str) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": out / "calibration.json",
        "markdown": out / "calibration.md",
        "reliability": out / "reliability.png",
    }
    paths["json"].write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    paths["markdown"].write_text(render_markdown(report))
    plot_reliability(report, paths["reliability"])
    return paths
