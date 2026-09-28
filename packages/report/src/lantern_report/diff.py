"""Diff findings between two runs of the same repository.

Findings are matched by their stable key (category, anchor symbol path, rule ids, and entry
route), never by line numbers, so moving code around does not create noise.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from lantern_report.findings import Finding


@dataclass
class Change:
    key: str
    before: Finding
    after: Finding

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "before": self.before.to_dict(), "after": self.after.to_dict()}


@dataclass
class FindingsDiff:
    new: list[Finding] = field(default_factory=list)
    resolved: list[Finding] = field(default_factory=list)
    changed_severity: list[Change] = field(default_factory=list)
    changed_classification: list[Change] = field(default_factory=list)
    changed_status: list[Change] = field(default_factory=list)
    unchanged: int = 0

    @property
    def is_empty(self) -> bool:
        return not (
            self.new
            or self.resolved
            or self.changed_severity
            or self.changed_classification
            or self.changed_status
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "new": [f.to_dict() for f in self.new],
            "resolved": [f.to_dict() for f in self.resolved],
            "changed_severity": [c.to_dict() for c in self.changed_severity],
            "changed_classification": [c.to_dict() for c in self.changed_classification],
            "changed_status": [c.to_dict() for c in self.changed_status],
            "unchanged": self.unchanged,
        }


def diff_findings(before: Sequence[Finding], after: Sequence[Finding]) -> FindingsDiff:
    old = {f.key: f for f in before}
    new = {f.key: f for f in after}
    result = FindingsDiff()
    for key in sorted(new.keys() - old.keys()):
        result.new.append(new[key])
    for key in sorted(old.keys() - new.keys()):
        result.resolved.append(old[key])
    for key in sorted(old.keys() & new.keys()):
        a, b = old[key], new[key]
        changed = False
        if a.severity != b.severity:
            result.changed_severity.append(Change(key, a, b))
            changed = True
        if a.classification != b.classification:
            result.changed_classification.append(Change(key, a, b))
            changed = True
        if a.status != b.status:
            result.changed_status.append(Change(key, a, b))
            changed = True
        if not changed:
            result.unchanged += 1
    return result
