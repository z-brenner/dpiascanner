from __future__ import annotations

import os
from pathlib import Path

import pytest

from lantern_analysis.analyze import analyze_repo
from lantern_analysis.model import DataFlowGraph

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
_CACHE: dict[str, DataFlowGraph] = {}


def graph_for(fixture: str) -> DataFlowGraph:
    if fixture not in _CACHE:
        os.environ.pop("SEMGREP_APP_TOKEN", None)
        _CACHE[fixture] = analyze_repo(FIXTURES / fixture, commit="fixture")
    return _CACHE[fixture]


@pytest.fixture(scope="session")
def canary_python() -> DataFlowGraph:
    return graph_for("canary-python")


@pytest.fixture(scope="session")
def canary_typescript() -> DataFlowGraph:
    return graph_for("canary-typescript")


@pytest.fixture(scope="session")
def clean_python() -> DataFlowGraph:
    return graph_for("clean-python")


@pytest.fixture(scope="session")
def gaps_python() -> DataFlowGraph:
    return graph_for("gaps-python")
