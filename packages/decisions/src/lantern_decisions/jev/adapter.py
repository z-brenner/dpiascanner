"""Every assumption about the TypeSafe AI Jev wire format lives in this module.

Final API documentation was not available when this was written. The shape below is
assumed from the product description: a request is a state object plus a list of typed
questions (choice, score, yes_no); the response returns, per question, the selected option,
its probability, the distribution over options, and a confidence value.

When the real documentation arrives, change only this module and its tests. Each TODO marks
one assumption to confirm.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from lantern_decisions.question_sets import Question, QuestionKind, QuestionSet

# TODO(jev-api): confirm the batch endpoint path and HTTP method.
BATCH_PATH = "/v1/decisions:batch"
# TODO(jev-api): confirm the auth scheme (bearer token assumed) and header name.
AUTH_HEADER = "Authorization"
AUTH_SCHEME = "Bearer"
# TODO(jev-api): confirm the maximum number of items per batch request.
MAX_ITEMS_PER_REQUEST = 32
# TODO(jev-api): confirm which status codes are retryable; 408, 409 and 425 assumed safe.
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

# TODO(jev-api): confirm the type names Jev uses for each question kind.
_KIND_NAMES = {
    QuestionKind.CHOICE: "choice",
    QuestionKind.YES_NO: "yes_no",
    QuestionKind.SCORE: "score",
}


class JevResponseError(ValueError):
    """The response did not match the assumed wire format."""


@dataclass(frozen=True)
class JevItem:
    """One (target, question set) pair to send."""

    item_id: str
    state: Mapping[str, Any]
    question_set: QuestionSet


@dataclass(frozen=True)
class JevAnswer:
    question_id: str
    selected: str
    probability: float
    distribution: dict[str, float]
    confidence: float | None
    raw: dict[str, Any]


def encode_question(question: Question) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": question.id,
        "type": _KIND_NAMES[question.kind],
        "prompt": question.description,
    }
    if question.kind is QuestionKind.SCORE:
        scores = [int(o.id) for o in question.options]
        # TODO(jev-api): confirm score questions take min/max plus per-level anchors.
        body["min"], body["max"] = min(scores), max(scores)
        body["anchors"] = {o.id: o.description for o in question.options}
    else:
        # TODO(jev-api): confirm options are sent as id plus description objects.
        body["options"] = [{"id": o.id, "description": o.description} for o in question.options]
    return body


def encode_batch(items: Sequence[JevItem]) -> dict[str, Any]:
    # TODO(jev-api): confirm the envelope. Assumed: {"items": [{"id", "state", "questions"}]}.
    return {
        "items": [
            {
                "id": item.item_id,
                "state": item.state,
                "questions": [encode_question(q) for q in item.question_set.questions],
                "metadata": {"question_set": item.question_set.name},
            }
            for item in items
        ]
    }


def _normalize_option(value: Any) -> str:
    # Score answers may come back as integers; yes/no may come back as booleans.
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def decode_batch(
    payload: Mapping[str, Any], items: Sequence[JevItem]
) -> tuple[dict[str, list[JevAnswer]], str]:
    """Return answers keyed by item id, plus the model version string."""
    # TODO(jev-api): confirm the response envelope and the model version field name.
    model_version = str(payload.get("model_version") or payload.get("model") or "unknown")
    results = payload.get("results")
    if not isinstance(results, list):
        raise JevResponseError("response has no 'results' list")
    by_id: dict[str, list[JevAnswer]] = {}
    expected = {item.item_id: item for item in items}
    for result in results:
        item_id = str(result.get("id"))
        if item_id not in expected:
            raise JevResponseError(f"unexpected item id {item_id!r}")
        answers = []
        for answer in result.get("answers", []):
            # TODO(jev-api): confirm per-question fields. Assumed: question_id, selected,
            # probability, distribution (option -> probability), confidence.
            distribution = {
                _normalize_option(k): float(v)
                for k, v in (answer.get("distribution") or {}).items()
            }
            answers.append(
                JevAnswer(
                    question_id=str(answer["question_id"]),
                    selected=_normalize_option(answer["selected"]),
                    probability=float(answer["probability"]),
                    distribution=distribution,
                    confidence=(
                        float(answer["confidence"])
                        if answer.get("confidence") is not None
                        else None
                    ),
                    raw=dict(answer),
                )
            )
        by_id[item_id] = answers
    missing = set(expected) - set(by_id)
    if missing:
        raise JevResponseError(f"response is missing items {sorted(missing)}")
    return by_id, model_version
