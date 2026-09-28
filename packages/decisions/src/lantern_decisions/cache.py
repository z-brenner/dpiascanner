"""Decision cache keyed on provider, state hash, and question set version.

Re-running on unchanged code produces the same state hashes, so every answer is served from
the cache and the hosted provider receives zero calls. The cache stores results only, never
state payloads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheKey:
    provider: str
    state_hash: str
    question_set_id: str
    question_set_version: str

    def as_tuple(self) -> tuple[str, str, str, str]:
        return (self.provider, self.state_hash, self.question_set_id, self.question_set_version)


CachedAnswers = list[dict[str, Any]]
"""Serialized per-question results for one (state, question set) pair."""


class DecisionCache(ABC):
    @abstractmethod
    def get_many(self, keys: Sequence[CacheKey]) -> dict[CacheKey, CachedAnswers]: ...

    @abstractmethod
    def put_many(self, items: Mapping[CacheKey, CachedAnswers]) -> None: ...


class InMemoryDecisionCache(DecisionCache):
    def __init__(self) -> None:
        self._data: dict[CacheKey, CachedAnswers] = {}

    def get_many(self, keys: Sequence[CacheKey]) -> dict[CacheKey, CachedAnswers]:
        return {k: self._data[k] for k in keys if k in self._data}

    def put_many(self, items: Mapping[CacheKey, CachedAnswers]) -> None:
        self._data.update(items)

    def __len__(self) -> int:
        return len(self._data)


class SQLiteDecisionCache(DecisionCache):
    """Durable cache for local runs and single-worker deployments."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS decisions ("
            " provider TEXT, state_hash TEXT, question_set_id TEXT, question_set_version TEXT,"
            " answers TEXT NOT NULL,"
            " PRIMARY KEY (provider, state_hash, question_set_id, question_set_version))"
        )
        self._conn.commit()

    def get_many(self, keys: Sequence[CacheKey]) -> dict[CacheKey, CachedAnswers]:
        found: dict[CacheKey, CachedAnswers] = {}
        with self._lock:
            for key in keys:
                row = self._conn.execute(
                    "SELECT answers FROM decisions WHERE provider=? AND state_hash=? "
                    "AND question_set_id=? AND question_set_version=?",
                    key.as_tuple(),
                ).fetchone()
                if row is not None:
                    found[key] = json.loads(row[0])
        return found

    def put_many(self, items: Mapping[CacheKey, CachedAnswers]) -> None:
        rows: Iterable[tuple[str, str, str, str, str]] = (
            (*k.as_tuple(), json.dumps(v, sort_keys=True)) for k, v in items.items()
        )
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO decisions VALUES (?, ?, ?, ?, ?)", list(rows)
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
