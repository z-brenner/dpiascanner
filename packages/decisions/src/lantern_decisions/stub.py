"""StubProvider: a deterministic, offline decision provider.

Answers come from, in order of precedence:

1. an exact lookup keyed on the state hash (pins for specific targets),
2. the first rule in the rules file whose conditions hold and that answers the question,
3. per-question defaults, which are deliberately low-confidence,
4. a uniform distribution, which always falls below any sensible threshold.

It exists so the whole pipeline runs and is testable without network access. Its rules are
tuned to the fixtures, so tests that use it prove plumbing, not classification quality.

Rules file format (YAML)::

    version: stub-rules-v1
    rules:
      - name: health lexicon hint
        applies_to: [source]
        when:                      # every condition must hold
          - {path: lexicon_hints.category, equals: health}
        unless:                    # no condition may hold
          - {path: flags.clean_context, equals: true}
        confidence: 0.92           # probability for shorthand answers
        answers:
          data_category: health
          special_category_art9: {answer: "yes", probability: 0.95}
    defaults:
      source:
        data_category: {answer: none, probability: 0.55}
    lookup:
      <state sha256>:
        data_category: {answer: contact, probability: 0.97}

Condition operators: ``equals``, ``in``, ``contains`` (substring or membership),
``matches`` (regex search), ``exists`` (true/false). Paths are dotted; lists along the path
are traversed, and a condition holds if any collected value satisfies it.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar

import yaml

from lantern_decisions.distributions import shaped_distribution, uniform_distribution
from lantern_decisions.provider import DecisionProvider, resolve_question_set
from lantern_decisions.question_sets import Question
from lantern_decisions.types import DecisionRequest, DecisionResult

DEFAULT_RULES = "v1.yaml"
DEFAULT_CONFIDENCE = 0.9
_OPERATORS = frozenset({"equals", "in", "contains", "matches", "exists"})


def collect(value: Any, path: Sequence[str]) -> list[Any]:
    """Values at a dotted path, traversing lists along the way."""
    if not path:
        if isinstance(value, list):
            return list(value)
        return [value]
    if isinstance(value, list):
        return [v for item in value for v in collect(item, path)]
    if isinstance(value, Mapping) and path[0] in value:
        return collect(value[path[0]], path[1:])
    return []


@dataclass(frozen=True)
class Condition:
    path: tuple[str, ...]
    op: str
    operand: Any

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> Condition:
        ops = [k for k in data if k != "path"]
        if len(ops) != 1 or ops[0] not in _OPERATORS:
            raise ValueError(f"condition needs exactly one of {sorted(_OPERATORS)}: {data!r}")
        op = ops[0]
        operand = data[op]
        if op == "matches":
            re.compile(operand)
        return cls(tuple(str(data["path"]).split(".")), op, operand)

    def holds(self, state: Mapping[str, Any]) -> bool:
        values = [v for v in collect(state, self.path) if v is not None]
        if self.op == "exists":
            return bool(values) is bool(self.operand)
        for v in values:
            if self.op == "equals" and v == self.operand:
                return True
            if self.op == "in" and v in self.operand:
                return True
            if self.op == "contains" and isinstance(v, str) and str(self.operand) in v:
                return True
            if self.op == "matches" and isinstance(v, str) and re.search(self.operand, v):
                return True
        return False


@dataclass(frozen=True)
class Answer:
    answer: str
    probability: float


def _parse_answer(value: Any, default_probability: float) -> Answer:
    if isinstance(value, Mapping):
        return Answer(str(value["answer"]), float(value.get("probability", default_probability)))
    return Answer(str(value), default_probability)


@dataclass(frozen=True)
class Rule:
    name: str
    applies_to: frozenset[str]
    when: tuple[Condition, ...]
    unless: tuple[Condition, ...]
    answers: Mapping[str, Answer]

    def matches(self, target_type: str, state: Mapping[str, Any]) -> bool:
        if self.applies_to and target_type not in self.applies_to:
            return False
        return all(c.holds(state) for c in self.when) and not any(
            c.holds(state) for c in self.unless
        )


@dataclass(frozen=True)
class StubRules:
    version: str
    fingerprint: str
    rules: tuple[Rule, ...]
    defaults: Mapping[str, Mapping[str, Answer]]
    lookup: Mapping[str, Mapping[str, Answer]]

    @classmethod
    def from_yaml(cls, text: str) -> StubRules:
        data = yaml.safe_load(text) or {}
        rules = []
        for raw in data.get("rules", []):
            confidence = float(raw.get("confidence", DEFAULT_CONFIDENCE))
            rules.append(
                Rule(
                    name=str(raw["name"]),
                    applies_to=frozenset(raw.get("applies_to", [])),
                    when=tuple(Condition.parse(c) for c in raw.get("when", [])),
                    unless=tuple(Condition.parse(c) for c in raw.get("unless", [])),
                    answers={q: _parse_answer(a, confidence) for q, a in raw["answers"].items()},
                )
            )
        defaults = {
            target: {q: _parse_answer(a, 0.5) for q, a in answers.items()}
            for target, answers in data.get("defaults", {}).items()
        }
        lookup = {
            str(h): {q: _parse_answer(a, 0.99) for q, a in answers.items()}
            for h, answers in data.get("lookup", {}).items()
        }
        return cls(
            version=str(data.get("version", "stub-rules")),
            fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            rules=tuple(rules),
            defaults=defaults,
            lookup=lookup,
        )

    @classmethod
    def load(cls, path: Path | str | None = None) -> StubRules:
        if path is None:
            text = (resources.files("lantern_decisions") / "stub_rules" / DEFAULT_RULES).read_text(
                encoding="utf-8"
            )
        else:
            text = Path(path).read_text(encoding="utf-8")
        return cls.from_yaml(text)


class StubProvider(DecisionProvider):
    name: ClassVar[str] = "stub"
    max_batch_size = 1024

    def __init__(self, rules: StubRules | None = None) -> None:
        self.rules = rules or StubRules.load()

    @property
    def version(self) -> str:
        return f"{self.rules.version}+{self.rules.fingerprint[:12]}"

    def _answer(
        self, request: DecisionRequest, question: Question, state_hash: str
    ) -> tuple[Answer | None, str]:
        pinned = self.rules.lookup.get(state_hash, {}).get(question.id)
        if pinned is not None:
            return pinned, "lookup"
        target = request.target_type.value
        for rule in self.rules.rules:
            if question.id in rule.answers and rule.matches(target, request.state):
                return rule.answers[question.id], f"rule:{rule.name}"
        default = self.rules.defaults.get(target, {}).get(question.id)
        if default is not None:
            return default, "default"
        return None, "uniform"

    def _decide_batch(self, requests: Sequence[DecisionRequest]) -> list[DecisionResult]:
        results: list[DecisionResult] = []
        for request in requests:
            qs = resolve_question_set(request)
            h = request.state_hash
            for question in qs.questions:
                chosen, basis = self._answer(request, question, h)
                if chosen is None or chosen.answer not in question.option_ids:
                    distribution = uniform_distribution(question)
                    answer = max(distribution, key=lambda o: (distribution[o], o))
                    basis = "uniform" if chosen is None else f"{basis}:invalid-answer"
                else:
                    seed = f"{h}|{qs.name}|{question.id}"
                    distribution = shaped_distribution(
                        question, chosen.answer, chosen.probability, seed
                    )
                    answer = chosen.answer
                results.append(
                    DecisionResult(
                        target_id=request.target_id,
                        target_type=request.target_type,
                        question_set_id=qs.id,
                        question_set_version=qs.version,
                        question_id=question.id,
                        answer=answer,
                        probability=distribution[answer],
                        distribution=distribution,
                        provider=self.name,
                        provider_version=self.version,
                        state_hash=h,
                        raw={"basis": basis},
                    )
                )
        return results
