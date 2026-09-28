"""Deterministic probability distributions for providers that pick an answer by rule."""

from __future__ import annotations

import hashlib

from lantern_decisions.question_sets import Question, QuestionKind

PRECISION = 6


def _jitter(seed: str, option: str) -> float:
    """A deterministic weight in [0.5, 1.5) derived from the seed and the option."""
    digest = hashlib.sha256(f"{seed}|{option}".encode()).digest()
    return 0.5 + int.from_bytes(digest[:4], "big") / 2**32


def shaped_distribution(
    question: Question, answer: str, probability: float, seed: str
) -> dict[str, float]:
    """Put ``probability`` on ``answer`` and spread the rest plausibly.

    Score questions spread the remainder toward adjacent scores (a unimodal shape). Choice
    and yes/no questions spread it with deterministic jitter so distributions look like model
    output rather than a flat floor. Same inputs always give the same output.
    """
    options = question.option_ids
    if answer not in options:
        raise ValueError(f"{answer!r} is not an option of {question.id}")
    if len(options) == 1:
        return {answer: 1.0}
    probability = min(max(probability, 1.0 / len(options)), 1.0)
    others = [o for o in options if o != answer]
    if question.kind is QuestionKind.SCORE:
        a = int(answer)
        weights = {o: _jitter(seed, o) / (1 + abs(int(o) - a)) ** 2 for o in others}
    else:
        weights = {o: _jitter(seed, o) for o in others}
    total = sum(weights.values())
    probability = round(probability, PRECISION)
    remainder = 1.0 - probability
    dist = {o: round(remainder * w / total, PRECISION) for o, w in weights.items()}
    # Keep the chosen answer's probability exact; absorb rounding in the largest other option.
    largest = max(others, key=lambda o: (dist[o], o))
    dist[largest] = round(max(0.0, dist[largest] + remainder - sum(dist.values())), PRECISION)
    dist[answer] = probability
    return {o: dist[o] for o in options}


def uniform_distribution(question: Question) -> dict[str, float]:
    n = len(question.options)
    dist = {o: round(1.0 / n, PRECISION) for o in question.option_ids}
    first = question.option_ids[0]
    dist[first] = round(1.0 - sum(v for k, v in dist.items() if k != first), PRECISION)
    return dist
