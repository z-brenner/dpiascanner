"""Severity from the documented table in severity.yaml."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import yaml

SENSITIVE = frozenset(
    {"government_id", "health", "biometric", "financial", "precise_location", "credentials"}
)
RISKY_DESTINATIONS = frozenset(
    {"ad_tech", "unknown_third_party", "ai_model_provider", "cross_border"}
)


@dataclass(frozen=True)
class SeverityInputs:
    special_category: bool
    relates_to_minor: bool
    data_categories: frozenset[str]
    destination_class: str | None
    min_identifiability: int | None
    reachable: bool

    def conditions(self) -> set[str]:
        out: set[str] = set()
        if self.special_category:
            out.add("special_category")
        if self.relates_to_minor:
            out.add("relates_to_minor")
        if self.data_categories & SENSITIVE and not self.special_category:
            out.add("sensitive_data")  # special category already raised it once
        if self.destination_class in RISKY_DESTINATIONS:
            out.add("risky_destination")
        if self.min_identifiability is not None and self.min_identifiability <= 2:
            out.add("low_identifiability")
        if not self.reachable:
            out.add("unreachable")
        return out


@dataclass(frozen=True)
class SeverityTable:
    version: str
    levels: tuple[str, ...]
    base: dict[str, str]
    modifiers: tuple[tuple[str, int], ...]
    never_raise: frozenset[str]

    @classmethod
    def load(cls) -> SeverityTable:
        data: dict[str, Any] = yaml.safe_load(
            (resources.files("lantern_report") / "severity.yaml").read_text("utf-8")
        )
        return cls(
            version=str(data["version"]),
            levels=tuple(data["levels"]),
            base=dict(data["base"]),
            modifiers=tuple((m["when"], int(m["change"])) for m in data["modifiers"]),
            never_raise=frozenset(data.get("never_raise", [])),
        )

    def rank(self, level: str) -> int:
        return self.levels.index(level)

    def compute(self, category: str, inputs: SeverityInputs) -> tuple[str, list[str]]:
        """Severity and the list of modifiers that applied, for the report's explanation."""
        if "unreachable" in inputs.conditions():
            return self.levels[0], ["unreachable"]
        index = self.rank(self.base[category])
        applied: list[str] = []
        if category not in self.never_raise:
            for condition, change in self.modifiers:
                if condition in inputs.conditions():
                    index += change
                    applied.append(f"{condition} ({change:+d})")
        index = max(0, min(len(self.levels) - 1, index))
        return self.levels[index], applied

    def highest(self, levels: Iterable[str]) -> str:
        return max(levels, key=self.rank, default=self.levels[0])


@cache
def default_table() -> SeverityTable:
    return SeverityTable.load()
