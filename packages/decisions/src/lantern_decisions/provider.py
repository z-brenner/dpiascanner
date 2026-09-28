"""The DecisionProvider interface.

Every provider (stub, local classifier, hosted Jev) implements ``_decide_batch`` for one
chunk. The base class handles chunking, result ordering, and contract validation, so a
provider can be called with thousands of requests at once.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, ClassVar

from lantern_decisions.question_sets import Question, QuestionSet, load_bundle
from lantern_decisions.types import DecisionRequest, DecisionResult

PayloadGuard = Callable[[Mapping[str, Any]], None]
"""Called on every state before it leaves the process; raises to block it (for example, a
secrets scanner injected by the worker)."""


class ProviderContractError(RuntimeError):
    """A provider returned results that violate the DecisionProvider contract."""


def chunked[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    if size < 1:
        raise ValueError("chunk size must be positive")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def resolve_question_set(request: DecisionRequest) -> QuestionSet:
    qs = load_bundle(request.question_set_version).get(request.question_set_id)
    if qs.applies_to != request.target_type:
        raise ValueError(
            f"{qs.name} applies to {qs.applies_to}, not {request.target_type} "
            f"(target {request.target_id})"
        )
    return qs


def validate_result(result: DecisionResult, question: Question, tolerance: float = 1e-6) -> None:
    options = set(question.option_ids)
    if set(result.distribution) != options:
        raise ProviderContractError(
            f"{result.target_id}/{question.id}: distribution keys {sorted(result.distribution)} "
            f"do not match options {sorted(options)}"
        )
    if result.answer not in options:
        raise ProviderContractError(f"{result.target_id}/{question.id}: bad answer {result.answer}")
    total = math.fsum(result.distribution.values())
    if abs(total - 1.0) > tolerance:
        raise ProviderContractError(
            f"{result.target_id}/{question.id}: distribution sums to {total}"
        )
    if any(p < 0 or p > 1 for p in result.distribution.values()):
        raise ProviderContractError(f"{result.target_id}/{question.id}: probability out of range")
    if abs(result.distribution[result.answer] - result.probability) > tolerance:
        raise ProviderContractError(
            f"{result.target_id}/{question.id}: probability {result.probability} differs from "
            f"distribution[{result.answer}]={result.distribution[result.answer]}"
        )


class DecisionProvider(ABC):
    """Answers fixed, typed questions about graph targets with calibrated probabilities."""

    name: ClassVar[str]
    max_batch_size: int = 256

    @property
    @abstractmethod
    def version(self) -> str:
        """Identifies the model or rules that produced the answers."""

    def decide(self, requests: Sequence[DecisionRequest]) -> list[DecisionResult]:
        """Answer every question in each request's question set.

        Results come back flattened, in request order, then question order within the set.
        """
        results: list[DecisionResult] = []
        for chunk in chunked(requests, self.max_batch_size):
            chunk_results = self._decide_batch(chunk)
            self._check_chunk(chunk, chunk_results)
            results.extend(chunk_results)
        return results

    @abstractmethod
    def _decide_batch(self, requests: Sequence[DecisionRequest]) -> list[DecisionResult]:
        """Answer one chunk. Must preserve request order and question order."""

    def _check_chunk(
        self, requests: Sequence[DecisionRequest], results: Sequence[DecisionResult]
    ) -> None:
        expected: list[tuple[str, str, Question]] = []
        for request in requests:
            qs = resolve_question_set(request)
            expected.extend((request.target_id, qs.id, q) for q in qs.questions)
        if len(expected) != len(results):
            raise ProviderContractError(
                f"{self.name}: expected {len(expected)} results, got {len(results)}"
            )
        for (target_id, qs_id, question), result in zip(expected, results, strict=True):
            if (result.target_id, result.question_set_id, result.question_id) != (
                target_id,
                qs_id,
                question.id,
            ):
                raise ProviderContractError(
                    f"{self.name}: result order mismatch at {result.target_id}/{result.question_id}"
                )
            validate_result(result, question)
