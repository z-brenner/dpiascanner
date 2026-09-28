"""Personal-data lexicon matching. Matches are hints, never findings."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

_PREFIX_EXCLUDE = frozenset(
    {"max", "min", "cache", "template", "default", "is", "has", "num", "total", "validate"}
)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")


def tokenize(identifier: str) -> tuple[str, ...]:
    """Split an identifier into lowercase tokens: refereeEmail -> (referee, email)."""
    spaced = _CAMEL.sub("_", identifier)
    return tuple(t.lower() for t in _SPLIT.split(spaced) if t)


def _contains_run(tokens: Sequence[str], run: Sequence[str]) -> int:
    """Index of the first contiguous occurrence of run in tokens, or -1."""
    n = len(run)
    for i in range(len(tokens) - n + 1):
        if tuple(tokens[i : i + n]) == tuple(run):
            return i
    return -1


@dataclass(frozen=True)
class LexiconHint:
    term: str
    category: str
    strength: str
    matched: str

    def to_dict(self) -> dict[str, str]:
        return {
            "term": self.term,
            "category": self.category,
            "strength": self.strength,
            "matched": self.matched,
        }


@dataclass(frozen=True)
class _Term:
    term: str
    tokens: tuple[str, ...]
    category: str
    strength: str
    context: str | None


class Lexicon:
    def __init__(self, data: dict[str, Any], *, track_internal_ids: bool = False) -> None:
        self.version = str(data.get("version", "lexicon"))
        self.exclude = frozenset(data.get("exclude_tokens", []))
        self.context = {k: frozenset(v) for k, v in data.get("context", {}).items()}
        self.track_internal_ids = track_internal_ids
        terms: list[_Term] = []
        for category, spec in data["categories"].items():
            if category == "internal_identifier" and not track_internal_ids:
                continue
            emitted = "identifier" if category == "internal_identifier" else category
            ctx = spec.get("weak_context")
            for strength in ("strong", "weak"):
                for term in spec.get(strength, []):
                    terms.append(_Term(term, tokenize(term), emitted, strength, ctx))
        # Longer terms first so date_of_birth wins over a shorter overlapping term.
        self.terms = sorted(terms, key=lambda t: (-len(t.tokens), t.term))

    @classmethod
    def load(cls, path: Path | None = None, *, track_internal_ids: bool = False) -> Lexicon:
        if path is None:
            text = (resources.files("lantern_analysis") / "lexicon.yaml").read_text("utf-8")
        else:
            text = path.read_text("utf-8")
        return cls(yaml.safe_load(text), track_internal_ids=track_internal_ids)

    def match(self, identifier: str, context: Iterable[str] = ()) -> list[LexiconHint]:
        """Hints for an identifier. ``context`` holds names around it (class, function, route)."""
        tokens = tokenize(identifier)
        if not tokens:
            return []
        context_tokens = frozenset(t for c in context for t in tokenize(c))
        hints: list[LexiconHint] = []
        claimed: set[int] = set()
        for term in self.terms:
            at = _contains_run(tokens, term.tokens)
            if at < 0 or at in claimed:
                continue
            tail = tokens[at + len(term.tokens) :]
            if tail and tail[0] in self.exclude:
                continue  # email_verified, phone_type: metadata about the value
            if at > 0 and tokens[at - 1] in _PREFIX_EXCLUDE:
                continue  # max_age, is_email: a qualifier that makes it metadata
            if term.strength == "weak":
                allowed = self.context.get(term.context or "", frozenset())
                own = frozenset(tokens) - set(term.tokens)
                if not (allowed & (context_tokens | own)):
                    continue
            # A term like "id" must not claim tokens already matched by a longer term.
            claimed.update(range(at, at + len(term.tokens)))
            hints.append(LexiconHint(term.term, term.category, term.strength, identifier))
        return hints

    def best(self, identifier: str, context: Iterable[str] = ()) -> LexiconHint | None:
        hints = self.match(identifier, context)
        strong = [h for h in hints if h.strength == "strong"]
        pool = strong or hints
        return pool[0] if pool else None


@cache
def default_lexicon(track_internal_ids: bool = False) -> Lexicon:
    return Lexicon.load(track_internal_ids=track_internal_ids)
