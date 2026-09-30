from __future__ import annotations

from pathlib import Path

from lantern_platform.config import Settings
from lantern_platform.queue import QueueError
from lantern_worker import __main__ as worker_main


class FlakyQueue:
    """Unreachable once, then empty."""

    def __init__(self) -> None:
        self.calls = 0

    def enqueue(self, run_id: str) -> None:
        raise AssertionError("not used")

    def dequeue(self, timeout: float = 5.0) -> str | None:
        self.calls += 1
        if self.calls == 1:
            raise QueueError("run queue unavailable: Timeout reading from socket")
        return None


def test_serve_outlives_an_unreachable_queue(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'worker.db'}")
    queue = FlakyQueue()
    pauses: list[float] = []
    worker_main.serve(settings, queue, iterations=2, pause=pauses.append)
    assert queue.calls == 2 and pauses == [5]
