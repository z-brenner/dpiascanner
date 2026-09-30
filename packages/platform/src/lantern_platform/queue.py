"""The run queue: Redis in deployment, in memory for tests and single-process use."""

from __future__ import annotations

import threading
from collections import deque
from typing import Protocol

import redis

QUEUE_KEY = "lantern:runs"

# A blocking pop may wait up to MAX_BLOCK_S; the socket must wait longer, or an empty queue
# reads as a dead connection. redis-py 8 defaults socket_timeout to 5 s, exactly the worker's
# 5 s BRPOP, so the first idle wait timed out and killed the worker.
SOCKET_TIMEOUT_S = 30.0
MAX_BLOCK_S = 25


class QueueError(RuntimeError):
    """The queue could not be reached. Callers may retry."""


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
        self.client = redis.Redis.from_url(
            url,
            socket_timeout=SOCKET_TIMEOUT_S,
            socket_connect_timeout=10,
            # Ping a connection idle this long before reusing it, in case the network dropped it.
            health_check_interval=30,
        )
        self.key = key

    def enqueue(self, run_id: str) -> None:
        self.client.lpush(self.key, run_id)

    def dequeue(self, timeout: float = 5.0) -> str | None:
        block = max(1, min(int(timeout), MAX_BLOCK_S))
        try:
            item = self.client.brpop([self.key], timeout=block)
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as exc:
            raise QueueError(f"run queue unavailable: {exc}") from exc
        if item is None:
            return None
        _, value = item
        return value.decode() if isinstance(value, bytes) else str(value)
