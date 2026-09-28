"""Statute references for findings, with a hook for a pluggable state-law lookup provider."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any, Protocol

import yaml


@dataclass(frozen=True)
class StatuteRef:
    id: str
    jurisdiction: str
    citation: str
    title: str
    note: str = ""
    verify: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "jurisdiction": self.jurisdiction,
            "citation": self.citation,
            "title": self.title,
            "note": self.note,
            "verify": self.verify,
        }


class StateLawProvider(Protocol):
    """Adds jurisdiction-specific references (for example from a maintained legal database)."""

    def lookup(
        self, category: str, data_categories: Sequence[str], destination: str | None
    ) -> list[str]: ...


@dataclass(frozen=True)
class StatuteMap:
    version: str
    refs: dict[str, StatuteRef]
    by_category: dict[str, list[str]]
    by_data_category: dict[str, list[str]]
    by_destination: dict[str, list[str]]

    @classmethod
    def load(cls) -> StatuteMap:
        data = yaml.safe_load(
            (resources.files("lantern_report") / "statutes.yaml").read_text("utf-8")
        )
        refs = {
            key: StatuteRef(
                id=key,
                jurisdiction=str(v["jurisdiction"]),
                citation=str(v["citation"]),
                title=str(v["title"]),
                note=str(v.get("note", "")),
                verify=bool(v.get("verify", False)),
            )
            for key, v in data["refs"].items()
        }
        return cls(
            version=str(data["version"]),
            refs=refs,
            by_category=dict(data["by_category"]),
            by_data_category=dict(data.get("by_data_category", {})),
            by_destination=dict(data.get("by_destination", {})),
        )

    def resolve(self, ids: Iterable[str]) -> list[StatuteRef]:
        return [self.refs[i] for i in ids if i in self.refs]


_PROVIDERS: list[StateLawProvider] = []


def register_state_law_provider(provider: StateLawProvider) -> None:
    _PROVIDERS.append(provider)


@cache
def default_map() -> StatuteMap:
    return StatuteMap.load()


def statute_refs_for(
    category: str, data_categories: Sequence[str], destination: str | None
) -> list[str]:
    statutes = default_map()
    ids: list[str] = list(statutes.by_category.get(category, []))
    if category not in ("unreachable_flow",):
        for cat in data_categories:
            ids.extend(statutes.by_data_category.get(cat, []))
        if destination:
            ids.extend(statutes.by_destination.get(destination, []))
    for provider in _PROVIDERS:
        ids.extend(provider.lookup(category, data_categories, destination))
    seen: set[str] = set()
    ordered = []
    for i in ids:
        if i not in seen and i in statutes.refs:
            seen.add(i)
            ordered.append(i)
    return ordered
