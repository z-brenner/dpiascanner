"""JevProvider: the hosted TypeSafe AI Jev decision model.

Wire-format assumptions are isolated in ``lantern_decisions.jev.adapter``. This module owns
transport concerns: configuration, caching, rate limiting, retries with exponential
backoff, and logging with state payloads redacted.
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

import httpx

from lantern_decisions.cache import CacheKey, DecisionCache
from lantern_decisions.jev import adapter
from lantern_decisions.provider import (
    DecisionProvider,
    PayloadGuard,
    ProviderContractError,
    chunked,
    resolve_question_set,
)
from lantern_decisions.question_sets import Question, QuestionSet
from lantern_decisions.ratelimit import TokenBucket
from lantern_decisions.types import DecisionRequest, DecisionResult

log = logging.getLogger("lantern.decisions.jev")

DISTRIBUTION_TOLERANCE = 1e-3


class JevConfigError(RuntimeError):
    pass


class JevRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class JevConfig:
    api_key: str
    base_url: str
    timeout_s: float = 30.0
    max_attempts: int = 5
    backoff_base_s: float = 0.5
    backoff_max_s: float = 30.0
    requests_per_second: float = 5.0
    batch_size: int = adapter.MAX_ITEMS_PER_REQUEST

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> JevConfig:
        env = os.environ if env is None else env
        api_key = env.get("TYPESAFE_API_KEY", "")
        base_url = env.get("TYPESAFE_BASE_URL", "")
        if not api_key or not base_url:
            raise JevConfigError("TYPESAFE_API_KEY and TYPESAFE_BASE_URL must both be set")
        return cls(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            timeout_s=float(env.get("TYPESAFE_TIMEOUT_S", "30")),
            requests_per_second=float(env.get("TYPESAFE_RPS", "5")),
            batch_size=int(env.get("TYPESAFE_BATCH_SIZE", str(adapter.MAX_ITEMS_PER_REQUEST))),
        )

    def __repr__(self) -> str:  # never print the key
        return f"JevConfig(base_url={self.base_url!r}, api_key=***)"


def redact_for_log(body: Mapping[str, Any]) -> dict[str, Any]:
    """Replace each item's state with its hash so logs never carry code or data."""
    from lantern_decisions.types import state_hash

    items = []
    for item in body.get("items", []):
        items.append(
            {
                "id": item.get("id"),
                "state": {"redacted": True, "sha256": state_hash(item.get("state", {}))},
                "questions": [q.get("id") for q in item.get("questions", [])],
            }
        )
    return {"items": items}


def _normalize(answer: adapter.JevAnswer, question: Question) -> tuple[str, dict[str, float]]:
    options = question.option_ids
    unknown = set(answer.distribution) - set(options)
    if unknown:
        raise ProviderContractError(f"{question.id}: Jev returned unknown options {unknown}")
    if answer.selected not in options:
        raise ProviderContractError(f"{question.id}: Jev selected unknown option {answer.selected}")
    dist = {o: max(0.0, answer.distribution.get(o, 0.0)) for o in options}
    total = math.fsum(dist.values())
    if total <= 0 or abs(total - 1.0) > DISTRIBUTION_TOLERANCE:
        raise ProviderContractError(f"{question.id}: Jev distribution sums to {total}")
    dist = {o: round(p / total, 6) for o, p in dist.items()}
    dist[answer.selected] = round(
        1.0 - math.fsum(p for o, p in dist.items() if o != answer.selected), 6
    )
    return answer.selected, dist


class JevProvider(DecisionProvider):
    name: ClassVar[str] = "jev"
    max_batch_size = 512

    def __init__(
        self,
        config: JevConfig | None = None,
        *,
        cache: DecisionCache | None = None,
        transport: httpx.BaseTransport | None = None,
        payload_guard: PayloadGuard | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rate_limiter: TokenBucket | None = None,
    ) -> None:
        self.config = config or JevConfig.from_env()
        self.cache = cache
        self.payload_guard = payload_guard
        self._sleep = sleep
        self._limiter = rate_limiter or TokenBucket(self.config.requests_per_second)
        self._client = httpx.Client(
            base_url=self.config.base_url,
            timeout=self.config.timeout_s,
            transport=transport,
            headers={
                adapter.AUTH_HEADER: f"{adapter.AUTH_SCHEME} {self.config.api_key}",
                "User-Agent": "lantern-decisions/0.1",
            },
        )
        self._model_version = "unknown"
        self.api_calls = 0

    @property
    def version(self) -> str:
        return self._model_version

    def close(self) -> None:
        self._client.close()

    # -- transport -----------------------------------------------------------------------

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            self._limiter.acquire()
            log.debug("jev request attempt=%d body=%s", attempt, redact_for_log(body))
            self.api_calls += 1
            try:
                response = self._client.post(adapter.BATCH_PATH, json=body)
            except httpx.TransportError as exc:
                last_error = exc
                log.warning("jev transport error attempt=%d: %s", attempt, type(exc).__name__)
            else:
                if response.status_code < 400:
                    payload: dict[str, Any] = response.json()
                    log.debug(
                        "jev response status=%d items=%d model=%s",
                        response.status_code,
                        len(payload.get("results", [])),
                        payload.get("model_version"),
                    )
                    return payload
                if response.status_code not in adapter.RETRYABLE_STATUS:
                    raise JevRequestError(
                        f"Jev returned {response.status_code}: {response.text[:200]}"
                    )
                last_error = JevRequestError(f"Jev returned {response.status_code}")
                retry_after = response.headers.get("Retry-After")
                if retry_after is not None and attempt < self.config.max_attempts:
                    try:
                        self._sleep(min(float(retry_after), self.config.backoff_max_s))
                        continue
                    except ValueError:
                        pass
                log.warning("jev retryable status=%d attempt=%d", response.status_code, attempt)
            if attempt < self.config.max_attempts:
                delay = min(
                    self.config.backoff_max_s, self.config.backoff_base_s * 2 ** (attempt - 1)
                )
                self._sleep(delay * (0.5 + random.random() / 2))  # noqa: S311 - jitter only
        raise JevRequestError(
            f"Jev request failed after {self.config.max_attempts} attempts"
        ) from (last_error)

    # -- provider ------------------------------------------------------------------------

    def _decide_batch(self, requests: Sequence[DecisionRequest]) -> list[DecisionResult]:
        question_sets: list[QuestionSet] = [resolve_question_set(r) for r in requests]
        keys = [
            CacheKey(self.name, r.state_hash, qs.id, qs.version)
            for r, qs in zip(requests, question_sets, strict=True)
        ]
        cached = self.cache.get_many(keys) if self.cache else {}
        answers_by_key: dict[CacheKey, list[dict[str, Any]]] = dict(cached)

        pending: dict[CacheKey, adapter.JevItem] = {}
        for request, qs, key in zip(requests, question_sets, keys, strict=True):
            if key in answers_by_key or key in pending:
                continue
            if self.payload_guard is not None:
                self.payload_guard(request.state)
            pending[key] = adapter.JevItem(
                item_id=key.state_hash[:16] + qs.id, state=request.state, question_set=qs
            )

        fresh: dict[CacheKey, list[dict[str, Any]]] = {}
        pending_items = list(pending.items())
        for group in chunked(pending_items, max(1, self.config.batch_size)):
            items = [item for _, item in group]
            payload = self._post(adapter.encode_batch(items))
            decoded, model_version = adapter.decode_batch(payload, items)
            self._model_version = model_version
            for key, item in group:
                serialized = []
                for question in item.question_set.questions:
                    matching = [a for a in decoded[item.item_id] if a.question_id == question.id]
                    if not matching:
                        raise ProviderContractError(f"Jev did not answer {question.id}")
                    answer, dist = _normalize(matching[0], question)
                    serialized.append(
                        {
                            "question_id": question.id,
                            "answer": answer,
                            "distribution": dist,
                            "provider_version": model_version,
                            "raw": matching[0].raw,
                        }
                    )
                fresh[key] = serialized
        if fresh and self.cache is not None:
            self.cache.put_many(fresh)
        answers_by_key.update(fresh)

        results: list[DecisionResult] = []
        for request, qs, key in zip(requests, question_sets, keys, strict=True):
            by_question = {a["question_id"]: a for a in answers_by_key[key]}
            for question in qs.questions:
                a = by_question[question.id]
                results.append(
                    DecisionResult(
                        target_id=request.target_id,
                        target_type=request.target_type,
                        question_set_id=qs.id,
                        question_set_version=qs.version,
                        question_id=question.id,
                        answer=a["answer"],
                        probability=a["distribution"][a["answer"]],
                        distribution=a["distribution"],
                        provider=self.name,
                        provider_version=a["provider_version"],
                        state_hash=key.state_hash,
                        raw=a["raw"],
                        cached=key in cached,
                    )
                )
        return results
