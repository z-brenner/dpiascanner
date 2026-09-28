"""Request and result types shared by every decision provider."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from lantern_decisions.question_sets import TargetType

STATE_SCHEMA = "lantern.state/v1"


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8 preserved."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def state_hash(state: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DecisionRequest:
    """One target (node or edge) to classify with one question set.

    ``state`` is the JSON payload the decision model sees (schema ``lantern.state/v1``,
    built by the worker from the graph). It must already be redacted.
    """

    target_id: str
    target_type: TargetType
    state: Mapping[str, Any]
    question_set_id: str
    question_set_version: str

    @property
    def state_hash(self) -> str:
        return state_hash(self.state)


@dataclass(frozen=True)
class DecisionResult:
    """A provider's answer to one question about one target."""

    target_id: str
    target_type: TargetType
    question_set_id: str
    question_set_version: str
    question_id: str
    answer: str
    probability: float
    distribution: Mapping[str, float]
    provider: str
    provider_version: str
    state_hash: str
    raw: Mapping[str, Any] = field(default_factory=dict)
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_type": self.target_type.value,
            "question_set_id": self.question_set_id,
            "question_set_version": self.question_set_version,
            "question_id": self.question_id,
            "answer": self.answer,
            "probability": self.probability,
            "distribution": dict(self.distribution),
            "provider": self.provider,
            "provider_version": self.provider_version,
            "state_hash": self.state_hash,
            "raw": dict(self.raw),
            "cached": self.cached,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionResult:
        return cls(
            target_id=str(data["target_id"]),
            target_type=TargetType(data["target_type"]),
            question_set_id=str(data["question_set_id"]),
            question_set_version=str(data["question_set_version"]),
            question_id=str(data["question_id"]),
            answer=str(data["answer"]),
            probability=float(data["probability"]),
            distribution={str(k): float(v) for k, v in data["distribution"].items()},
            provider=str(data["provider"]),
            provider_version=str(data["provider_version"]),
            state_hash=str(data["state_hash"]),
            raw=dict(data.get("raw", {})),
            cached=bool(data.get("cached", False)),
        )
