"""Everything a request handler needs, built once per app."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from lantern_platform.config import Settings
from lantern_platform.crypto import TokenCipher
from lantern_platform.db import Database
from lantern_platform.egress import EgressPolicy
from lantern_platform.github import GitHub
from lantern_platform.queue import InMemoryQueue, JobQueue, RedisQueue
from lantern_platform.tokens import DatabaseTokenStore


@dataclass
class Services:
    settings: Settings
    db: Database
    github: GitHub
    queue: JobQueue
    cipher: TokenCipher
    clock: Callable[[], float] = time.time
    repo_cache: dict[int, tuple[float, list[dict[str, Any]]]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        settings: Settings,
        queue: JobQueue | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> Services:
        db = Database(settings.database_url)
        db.create_all()
        cipher = TokenCipher(settings.token_key)
        github = GitHub(
            settings,
            EgressPolicy(settings.egress_allowlist),
            transport=transport,
            clock=clock,
            token_store=DatabaseTokenStore(db, cipher),
        )
        if queue is None:
            queue = RedisQueue(settings.redis_url) if settings.redis_url else InMemoryQueue()
        return cls(settings, db, github, queue, cipher, clock)
