"""Labeled examples for calibration: a state plus the expected answer per question."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from lantern_decisions.question_sets import TargetType
from lantern_decisions.types import DecisionRequest


@dataclass(frozen=True)
class LabeledExample:
    example_id: str
    target_type: TargetType
    question_set_id: str
    question_set_version: str
    state: Mapping[str, Any]
    labels: Mapping[str, str]
    provenance: str = ""
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_request(self) -> DecisionRequest:
        return DecisionRequest(
            target_id=self.example_id,
            target_type=self.target_type,
            state=self.state,
            question_set_id=self.question_set_id,
            question_set_version=self.question_set_version,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "target_type": self.target_type.value,
            "question_set_id": self.question_set_id,
            "question_set_version": self.question_set_version,
            "state": dict(self.state),
            "labels": dict(self.labels),
            "provenance": self.provenance,
            "notes": self.notes,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LabeledExample:
        return cls(
            example_id=str(data["example_id"]),
            target_type=TargetType(data["target_type"]),
            question_set_id=str(data["question_set_id"]),
            question_set_version=str(data["question_set_version"]),
            state=dict(data["state"]),
            labels={str(k): str(v) for k, v in data["labels"].items()},
            provenance=str(data.get("provenance", "")),
            notes=str(data.get("notes", "")),
            tags=tuple(data.get("tags", [])),
        )


def load_dataset(path: Path | str | None = None) -> list[LabeledExample]:
    """Load a JSONL dataset; defaults to the fixture-derived dataset shipped with the package."""
    if path is None:
        text = (
            resources.files("lantern_decisions") / "calibration" / "data" / "fixtures_v1.jsonl"
        ).read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    return [
        LabeledExample.from_dict(json.loads(line)) for line in text.splitlines() if line.strip()
    ]


def save_dataset(path: Path | str, examples: Iterable[LabeledExample]) -> None:
    lines = [json.dumps(e.to_dict(), sort_keys=True, ensure_ascii=False) for e in examples]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
