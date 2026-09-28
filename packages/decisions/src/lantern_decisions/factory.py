"""Construct a provider by name, so callers depend only on the interface."""

from __future__ import annotations

import os
from pathlib import Path

from lantern_decisions.cache import DecisionCache, SQLiteDecisionCache
from lantern_decisions.provider import DecisionProvider, PayloadGuard

PROVIDERS = ("stub", "jev")


def default_cache_path() -> Path:
    base = os.environ.get("LANTERN_CACHE_DIR") or Path.home() / ".cache" / "lantern"
    return Path(base) / "decisions.sqlite"


def make_provider(
    name: str | None = None,
    *,
    cache: DecisionCache | None = None,
    payload_guard: PayloadGuard | None = None,
) -> DecisionProvider:
    name = name or os.environ.get("LANTERN_DECISION_PROVIDER", "stub")
    if name == "stub":
        from lantern_decisions.stub import StubProvider

        return StubProvider()
    if name == "jev":
        from lantern_decisions.jev import JevProvider

        return JevProvider(
            cache=cache if cache is not None else SQLiteDecisionCache(default_cache_path()),
            payload_guard=payload_guard,
        )
    raise ValueError(f"unknown decision provider {name!r}; choose from {PROVIDERS}")
