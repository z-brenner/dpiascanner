"""Scope of an incremental (pull request) run: changed files plus their graph neighbors."""

from __future__ import annotations

from collections.abc import Iterable

from lantern_analysis.model import DataFlowGraph


def flow_files(graph: DataFlowGraph, flow_index: int) -> set[str]:
    flow = graph.flows[flow_index]
    files = {graph.nodes[n].file for n in flow.node_ids if n in graph.nodes}
    for edge_id in flow.edge_ids:
        edge = graph.edges.get(edge_id)
        if edge is not None:
            files.update(step.file for step in edge.evidence)
    return files


def neighbor_files(graph: DataFlowGraph, changed: Iterable[str]) -> set[str]:
    """Changed files, plus every file on a base-run flow that passes through one of them."""
    changed_set = set(changed)
    scope = set(changed_set)
    for index in range(len(graph.flows)):
        files = flow_files(graph, index)
        if files & changed_set:  # one hop: neighbors of changed files, not of neighbors
            scope |= files
    return {f for f in scope if f and not f.startswith("<")}
