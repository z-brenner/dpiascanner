"""The state payload schema (``lantern.state/v1``): what a decision model sees about a target.

The worker builds states from the graph; providers only read them. Every field is optional
except ``schema`` and ``target_type``, so providers must tolerate missing keys. States are
redacted and bounded before they reach this package: snippets carry at most a few lines of
context around the target span, and secrets are replaced with ``[REDACTED:<kind>]``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from lantern_decisions.question_sets import TargetType
from lantern_decisions.types import STATE_SCHEMA


class Location(TypedDict):
    file: str
    line_start: int
    line_end: int


class LexiconHint(TypedDict):
    term: str
    category: str
    strength: str  # "strong" or "weak"
    matched: str  # the identifier that matched, e.g. "referee_email"


class RegistryInfo(TypedDict, total=False):
    package: str
    vendor: str
    destination_class: str
    method: str
    event_path: str
    processor_claims: Mapping[str, Any]
    mitigations: list[Mapping[str, Any]]


class ConfigValue(TypedDict, total=False):
    key: str
    value: str
    file: str
    line: int
    conflict: bool


class EvidenceStep(TypedDict, total=False):
    file: str
    line: int
    kind: str  # the syntactic form that carried the value, e.g. "call-arg", "return"
    text: str


class TransformHint(TypedDict, total=False):
    rule_id: str
    kind: str  # hash | encryption | reversible_encoding | truncation | aggregation | deletion
    file: str
    line: int


class DecisionState(TypedDict, total=False):
    schema: str
    target_type: str
    language: str
    symbol: str
    call_chain: list[str]
    location: Location
    snippet: str
    snippet_truncated: bool
    field: str
    lexicon_hints: list[LexiconHint]
    registry: RegistryInfo
    config: list[ConfigValue]
    rule_ids: list[str]
    evidence_chain: list[EvidenceStep]
    transforms: list[TransformHint]
    retention_evidence: list[EvidenceStep]
    flags: dict[str, bool]
    upstream: Mapping[str, Any]
    downstream: Mapping[str, Any]


def check_state(state: Mapping[str, Any]) -> None:
    """Minimal structural check; raises ValueError."""
    if state.get("schema") != STATE_SCHEMA:
        raise ValueError(f"state schema must be {STATE_SCHEMA!r}, got {state.get('schema')!r}")
    TargetType(str(state.get("target_type")))
