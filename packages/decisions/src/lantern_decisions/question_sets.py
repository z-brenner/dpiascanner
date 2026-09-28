"""Versioned question sets.

Structure lives here as frozen dataclasses. Content (descriptions the decision model reads)
lives in YAML under ``question_sets/<version>/``. A version is immutable once released:
``question_sets/<version>/LOCK.yaml`` pins a fingerprint of every set, and loading fails if
the YAML no longer matches. To change a question, add a new version.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Any

import yaml


class QuestionKind(StrEnum):
    CHOICE = "choice"
    YES_NO = "yes_no"
    SCORE = "score"


class TargetType(StrEnum):
    SOURCE = "source"
    EDGE = "edge"
    SINK = "sink"
    RETENTION = "retention"


class QuestionSetError(ValueError):
    """A question set is malformed or does not match its lock."""


@dataclass(frozen=True)
class Option:
    id: str
    description: str


@dataclass(frozen=True)
class Question:
    id: str
    kind: QuestionKind
    description: str
    options: tuple[Option, ...]

    @property
    def option_ids(self) -> tuple[str, ...]:
        return tuple(o.id for o in self.options)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"id": self.id, "kind": self.kind.value}
        data["description"] = self.description
        if self.kind is QuestionKind.SCORE:
            scores = [int(o.id) for o in self.options]
            data["min"], data["max"] = min(scores), max(scores)
            data["anchors"] = {int(o.id): o.description for o in self.options}
        else:
            data["options"] = [{"id": o.id, "description": o.description} for o in self.options]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Question:
        try:
            kind = QuestionKind(data["kind"])
            qid = str(data["id"])
            description = str(data["description"]).strip()
        except (KeyError, ValueError) as exc:
            raise QuestionSetError(f"bad question {data!r}: {exc}") from exc
        if kind is QuestionKind.SCORE:
            low, high = int(data["min"]), int(data["max"])
            anchors = {int(k): str(v).strip() for k, v in data["anchors"].items()}
            if sorted(anchors) != list(range(low, high + 1)):
                raise QuestionSetError(f"{qid}: score anchors must cover {low}..{high}")
            options = tuple(Option(str(i), anchors[i]) for i in range(low, high + 1))
        else:
            options = tuple(
                Option(str(o["id"]), str(o["description"]).strip()) for o in data["options"]
            )
            if kind is QuestionKind.YES_NO and tuple(o.id for o in options) != ("yes", "no"):
                raise QuestionSetError(f"{qid}: yes_no options must be exactly yes, no")
        ids = [o.id for o in options]
        if len(ids) < 2 or len(set(ids)) != len(ids):
            raise QuestionSetError(f"{qid}: options must be at least two and unique")
        return cls(id=qid, kind=kind, description=description, options=options)


@dataclass(frozen=True)
class QuestionSet:
    id: str
    version: str
    applies_to: TargetType
    description: str
    questions: tuple[Question, ...]

    @property
    def name(self) -> str:
        return f"{self.id}_{self.version}"

    def question(self, question_id: str) -> Question:
        for q in self.questions:
            if q.id == question_id:
                return q
        raise KeyError(f"{self.name} has no question {question_id!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "applies_to": self.applies_to.value,
            "description": self.description,
            "questions": [q.to_dict() for q in self.questions],
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, width=92, allow_unicode=True)

    def fingerprint(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QuestionSet:
        questions = tuple(Question.from_dict(q) for q in data["questions"])
        if len({q.id for q in questions}) != len(questions):
            raise QuestionSetError(f"{data.get('id')}: duplicate question ids")
        return cls(
            id=str(data["id"]),
            version=str(data["version"]),
            applies_to=TargetType(data["applies_to"]),
            description=str(data["description"]).strip(),
            questions=questions,
        )

    @classmethod
    def from_yaml(cls, text: str) -> QuestionSet:
        return cls.from_dict(yaml.safe_load(text))


@dataclass(frozen=True)
class QuestionSetBundle:
    """All question sets released under one version, keyed by the target type they apply to."""

    version: str
    sets: Mapping[TargetType, QuestionSet]

    def for_target(self, target_type: TargetType | str) -> QuestionSet:
        return self.sets[TargetType(target_type)]

    def get(self, question_set_id: str) -> QuestionSet:
        for qs in self.sets.values():
            if qs.id == question_set_id:
                return qs
        raise KeyError(f"bundle {self.version} has no question set {question_set_id!r}")


def available_versions() -> list[str]:
    root = resources.files("lantern_decisions") / "question_sets"
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))


@cache
def load_bundle(version: str = "v1", *, verify_lock: bool = True) -> QuestionSetBundle:
    root = resources.files("lantern_decisions") / "question_sets" / version
    if not root.is_dir():
        raise QuestionSetError(f"unknown question set version {version!r}")
    sets: dict[TargetType, QuestionSet] = {}
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.name.endswith(".yaml") or entry.name == "LOCK.yaml":
            continue
        qs = QuestionSet.from_yaml(entry.read_text(encoding="utf-8"))
        if qs.version != version:
            raise QuestionSetError(f"{entry.name} declares version {qs.version}, not {version}")
        if entry.name != f"{qs.name}.yaml":
            raise QuestionSetError(f"{entry.name} should be named {qs.name}.yaml")
        if qs.applies_to in sets:
            raise QuestionSetError(f"two question sets apply to {qs.applies_to}")
        sets[qs.applies_to] = qs
    if set(sets) != set(TargetType):
        raise QuestionSetError(f"version {version} must cover every target type")
    if verify_lock:
        lock = yaml.safe_load((root / "LOCK.yaml").read_text(encoding="utf-8"))
        for qs in sets.values():
            pinned = lock.get("fingerprints", {}).get(qs.name)
            if pinned != qs.fingerprint():
                raise QuestionSetError(
                    f"{qs.name} changed after release (fingerprint {qs.fingerprint()[:12]} "
                    f"does not match LOCK.yaml). Released versions are immutable: add a new "
                    f"version instead of editing this one."
                )
    return QuestionSetBundle(version=version, sets=sets)
