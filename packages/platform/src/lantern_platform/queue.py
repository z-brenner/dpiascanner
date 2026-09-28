"""The run queue: Redis in deployment, in memory for tests and single-process use."""

from __future__ import annotations

import threading
from collections import deque
from typing import Protocol

import redis

QUEUE_KEY = "lantern:runs"


class JobQueue(Protocol):
    def enqueue(self, run_id: str) -> None: ...

    def dequeue(self, timeout: float = 5.0) -> str | None: ...


class InMemoryQueue:
    def __init__(self) -> None:
        self._items: deque[str] = deque()
        self._ready = threading.Condition()

    def enqueue(self, run_id: str) -> None:
        with self._ready:
            self._items.append(run_id)
            self._ready.notify()

    def dequeue(self, timeout: float = 5.0) -> str | None:
        with self._ready:
            if not self._items:
                self._ready.wait(timeout)
            return self._items.popleft() if self._items else None

    def __len__(self) -> int:
        return len(self._items)


class RedisQueue:
    def __init__(self, url: str, key: str = QUEUE_KEY) -> None:
        self.client = redis.Redis.from_url(url)
        self.key = key

    def enqueue(self, run_id: str) -> None:
        self.client.lpush(self.key, run_id)

    def dequeue(self, timeout: float = 5.0) -> str | None:
        item = self.client.brpop([self.key], timeout=max(1, int(timeout)))
        if item is None:
            return None
        _, value = item
        return value.decode() if isinstance(value, bytes) else str(value)
