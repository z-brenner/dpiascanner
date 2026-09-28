"""Run storage. The file store keeps local and test runs; the API wires a SQL store (Postgres)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from lantern_analysis.model import DataFlowGraph
from lantern_report.findings import FindingsResult


@dataclass(frozen=True)
class DecisionRow:
    """One persisted decision, with the raw provider output for reproduction."""

    id: str
    run_id: str
    target_id: str
    target_type: str
    question_set_id: str
    question_set_version: str
    question_id: str
    answer: str
    probability: float
    distribution: dict[str, float]
    provider: str
    provider_version: str
    state_hash: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunStore(Protocol):
    def save_graph(self, run_id: str, graph: DataFlowGraph) -> None: ...

    def save_decisions(self, run_id: str, rows: list[DecisionRow]) -> None: ...

    def save_findings(self, run_id: str, findings: FindingsResult) -> None: ...

    def load_findings(self, run_id: str) -> FindingsResult: ...


class FileRunStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def _dir(self, run_id: str) -> Path:
        path = self.root / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_graph(self, run_id: str, graph: DataFlowGraph) -> None:
        (self._dir(run_id) / "graph.json").write_text(graph.to_json())

    def save_decisions(self, run_id: str, rows: list[DecisionRow]) -> None:
        lines = [json.dumps(r.to_dict(), sort_keys=True) for r in rows]
        (self._dir(run_id) / "decisions.jsonl").write_text("\n".join(lines) + "\n")

    def load_decisions(self, run_id: str) -> list[DecisionRow]:
        text = (self._dir(run_id) / "decisions.jsonl").read_text()
        return [DecisionRow(**json.loads(line)) for line in text.splitlines() if line.strip()]

    def save_findings(self, run_id: str, findings: FindingsResult) -> None:
        (self._dir(run_id) / "findings.json").write_text(
            json.dumps(findings.to_dict(), indent=2, sort_keys=True)
        )

    def load_findings(self, run_id: str) -> FindingsResult:
        data = json.loads((self._dir(run_id) / "findings.json").read_text())
        return FindingsResult.from_dict(data)

    def load_graph(self, run_id: str) -> DataFlowGraph:
        return DataFlowGraph.from_json((self._dir(run_id) / "graph.json").read_text())
