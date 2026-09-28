"""Test helpers shared across packages: request builders and a fake Jev server."""

from __future__ import annotations

import json
from typing import Any

import httpx

from lantern_decisions.question_sets import TargetType
from lantern_decisions.types import STATE_SCHEMA, DecisionRequest


def make_state(target: TargetType, **extra: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "schema": STATE_SCHEMA,
        "target_type": target.value,
        "language": "python",
        "snippet": "def f(payload):\n    return payload.email",
    }
    state.update(extra)
    return state


def make_request(target: TargetType, target_id: str = "t1", **extra: Any) -> DecisionRequest:
    return DecisionRequest(
        target_id=target_id,
        target_type=target,
        state=make_state(target, **extra),
        question_set_id=target.value,
        question_set_version="v1",
    )


class FakeJev:
    """An httpx handler speaking the assumed Jev batch format."""

    def __init__(
        self, fail_first: int = 0, status: int = 429, retry_after: str | None = "0"
    ) -> None:
        self.calls = 0
        self.items_seen = 0
        self.fail_first = fail_first
        self.status = status
        self.retry_after = retry_after
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fail_first:
            headers = {"Retry-After": self.retry_after} if self.retry_after is not None else {}
            return httpx.Response(self.status, headers=headers, json={"error": "slow down"})
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        self.bodies.append(body)
        results = []
        for item in body["items"]:
            self.items_seen += 1
            answers = []
            for q in item["questions"]:
                if q["type"] == "score":
                    options = [str(i) for i in range(q["min"], q["max"] + 1)]
                else:
                    options = [o["id"] for o in q["options"]]
                selected = options[-1] if q["type"] == "score" else options[0]
                rest = 0.2 / (len(options) - 1)
                dist = {o: (0.8 if o == selected else rest) for o in options}
                answers.append(
                    {
                        "question_id": q["id"],
                        "selected": int(selected) if q["type"] == "score" else selected,
                        "probability": 0.8,
                        "distribution": dist,
                        "confidence": 0.77,
                    }
                )
            results.append({"id": item["id"], "answers": answers})
        return httpx.Response(200, json={"model_version": "jev-test-1", "results": results})
