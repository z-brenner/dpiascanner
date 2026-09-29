"""Assemble the data-flow graph from taint hits, detection results, and evidence scanners."""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

from lantern_analysis.configres import ConfigValue, attach_config
from lantern_analysis.detect import Detection, SinkSpec, SourceSpec
from lantern_analysis.endpoints import EndpointResolver
from lantern_analysis.mitigations import MitigationEvidence
from lantern_analysis.model import DataFlowGraph, Edge, Flow, Node, Step, stable_id
from lantern_analysis.project import Project
from lantern_analysis.reach import Reachability
from lantern_analysis.retention import RetentionScanner
from lantern_analysis.secrets import redact
from lantern_analysis.taint import Hit, TaintEngine, VEdge

SNIPPET_CONTEXT = 2
MAX_SNIPPET_LINES = 16
MAX_SNIPPET_CHARS = 1600
ALWAYS_LISTED_FAMILIES = frozenset({"http", "registry", "queue", "dependency", "email"})


def snippet_for(
    project: Project, file: str, start: int, end: int, context: int = SNIPPET_CONTEXT
) -> str:
    module = project.by_file.get(file)
    if module is not None:
        text = module.source.decode("utf-8", "replace")
    else:
        path = project.root / file
        text = path.read_text(errors="replace") if path.is_file() else ""
    lines = text.splitlines()
    lo = max(1, start - context)
    hi = min(len(lines), max(end, start) + context)
    chosen = lines[lo - 1 : hi]
    truncated = False
    if len(chosen) > MAX_SNIPPET_LINES:
        chosen = chosen[:MAX_SNIPPET_LINES]
        truncated = True
    out = "\n".join(f"{lo + i:>5}  {line}" for i, line in enumerate(chosen))
    if len(out) > MAX_SNIPPET_CHARS:
        out = out[:MAX_SNIPPET_CHARS]
        truncated = True
    if truncated:
        out += "\n  ...  [truncated]"
    return redact(out)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class BuildInputs:
    project: Project
    detection: Detection
    engine: TaintEngine
    hits: dict[str, list[Hit]]
    reach: Reachability
    mitigations: list[MitigationEvidence]
    config: list[ConfigValue]


class GraphBuilder:
    def __init__(
        self, inputs: BuildInputs, repo: str, commit: str, options: dict[str, Any]
    ) -> None:
        self.i = inputs
        self.project = inputs.project
        self.graph = DataFlowGraph(
            repo=repo,
            commit=commit,
            languages=sorted({m.language for m in self.project.modules.values()}),
            options=options,
        )
        self.retention = RetentionScanner(self.project, inputs.detection)
        self.endpoints = EndpointResolver(self.project)

    # ------------------------------------------------------------------ nodes

    def _language(self, fid: str) -> str:
        fn = self.project.functions.get(fid)
        return self.project.language(fn) if fn is not None else "unknown"

    def source_node(self, spec: SourceSpec) -> Node:
        node_id = stable_id("N-src", spec.span.file, spec.symbol, spec.field or "")
        fn = self.project.functions.get(spec.fid)
        entry = self.i.reach.roots.get(spec.fid)
        snippet = snippet_for(
            self.project, spec.span.file, spec.span.start_line, spec.span.end_line
        )
        return self.graph.add_node(
            Node(
                id=node_id,
                kind="source",
                file=spec.span.file,
                line_start=spec.span.start_line,
                line_end=spec.span.end_line,
                symbol=spec.symbol,
                language=self._language(spec.fid),
                snippet=snippet,
                snippet_hash=_hash(snippet),
                rule_ids=list(spec.rule_ids),
                attrs={
                    "source_kind": spec.kind,
                    "field": spec.field,
                    "model": spec.model,
                    "lexicon_hints": [h.to_dict() for h in spec.hints],
                    "function": fn.qualname if fn else None,
                    "entry": (
                        {"kind": entry.kind, "method": entry.method, "path": entry.path}
                        if entry is not None
                        and entry.kind in ("route", "handler", "main", "error_middleware")
                        else None
                    ),
                    "reachable": self.i.reach.is_reachable(spec.fid),
                    "context": spec.context,
                },
            )
        )

    def sink_node(self, sink: SinkSpec) -> Node:
        call = sink.call
        fn = self.project.functions[sink.fid]
        callee = call.func.text if len(call.func.text) < 80 else call.func.text[:77] + "..."
        symbol = f"{fn.qualname}:{callee}"
        node_id = stable_id("N-snk", call.span.file, symbol, call.span.start_line)
        snippet = snippet_for(
            self.project, call.span.file, call.span.start_line, call.span.end_line
        )
        config, conflict = attach_config(self.project, sink, self.i.config)
        endpoints = (
            self.endpoints.resolve(sink, config)
            if sink.family in ALWAYS_LISTED_FAMILIES or sink.registry is not None
            else {"hosts": [], "evidence": {}}
        )
        attrs: dict[str, Any] = {
            "family": sink.family,
            "method": sink.method,
            "external": sink.external,
            "model": sink.model,
            "persists": sink.persists,
            "event_path": sink.event_path,
            "function": fn.qualname,
            "reachable": self.i.reach.is_reachable(sink.fid),
            "config": config,
            "config_conflict": conflict,
            "endpoints": endpoints,
            "registry": sink.registry.summary() if sink.registry is not None else None,
            "dependency": (
                {
                    "package": sink.dependency.name,
                    "ecosystem": sink.dependency.ecosystem,
                    "version": sink.dependency.version,
                    "endpoints": sink.dependency.endpoints,
                    "evidence": sink.dependency.evidence,
                    "lockfile": sink.dependency.lockfile,
                }
                if sink.dependency is not None
                else None
            ),
            "mitigations": [
                m.to_dict()
                for m in self.i.mitigations
                if sink.registry is not None and m.registry_id == sink.registry.id
            ],
        }
        return self.graph.add_node(
            Node(
                id=node_id,
                kind="sink",
                file=call.span.file,
                line_start=call.span.start_line,
                line_end=call.span.end_line,
                symbol=symbol,
                language=self._language(sink.fid),
                snippet=snippet,
                snippet_hash=_hash(snippet),
                rule_ids=list(sink.rule_ids),
                attrs=attrs,
            )
        )

    def transform_node(self, key: str) -> Node | None:
        _, file, start, end = key.split("|")
        spec = self.i.detection.transforms.get((file, int(start), int(end)))
        if spec is None:
            return None
        fn = self.project.functions[spec.fid]
        snippet = snippet_for(self.project, file, spec.span.start_line, spec.span.end_line)
        return self.graph.add_node(
            Node(
                id=stable_id("N-trf", file, fn.qualname, spec.text),
                kind="transform",
                file=file,
                line_start=spec.span.start_line,
                line_end=spec.span.end_line,
                symbol=f"{fn.qualname}:{spec.text[:80]}",
                language=self._language(spec.fid),
                snippet=snippet,
                snippet_hash=_hash(snippet),
                rule_ids=[spec.rule_id],
                attrs={
                    "transform_kind": spec.kind,
                    "function": fn.qualname,
                    "reachable": self.i.reach.is_reachable(spec.fid),
                },
            )
        )

    def retention_node(self, sink_node: Node, sink: SinkSpec) -> Node:
        evidence = [e.to_dict() for e in self.retention.evidence_for(sink)]
        node = self.graph.add_node(
            Node(
                id=stable_id("N-ret", sink_node.id),
                kind="retention",
                file=sink_node.file,
                line_start=sink_node.line_start,
                line_end=sink_node.line_end,
                symbol=f"{sink_node.symbol}#retention",
                language=sink_node.language,
                snippet=sink_node.snippet,
                snippet_hash=sink_node.snippet_hash,
                rule_ids=list(sink_node.rule_ids),
                attrs={
                    "sink_id": sink_node.id,
                    "model": sink.model,
                    "store": sink.family,
                    "retention_evidence": evidence,
                    "reachable": sink_node.attrs.get("reachable", True),
                },
            )
        )
        self.graph.add_edge(
            Edge(
                id=stable_id("E", sink_node.id, node.id, "persists"),
                from_node=sink_node.id,
                to_node=node.id,
                kind="persists",
                evidence=[Step(sink_node.file, sink_node.line_start, "persists", sink_node.symbol)],
            )
        )
        return node

    # ------------------------------------------------------------------ flows

    def _steps(self, edges: list[VEdge]) -> list[Step]:
        steps: list[Step] = []
        for edge in edges:
            s = edge.step
            if not s.file:
                continue
            fn = self.project.functions.get(edge.fid)
            function = fn.qualname if fn is not None else ""
            if steps and (steps[-1].file, steps[-1].line, steps[-1].kind) == (
                s.file,
                s.line,
                s.kind,
            ):
                last = steps[-1]
                steps[-1] = Step(
                    last.file,
                    last.line,
                    last.kind,
                    last.text,
                    last.unresolved or s.unresolved,
                    last.note or s.note,
                    last.function or function,
                )
                continue
            steps.append(
                Step(s.file, s.line, s.kind, redact(s.text), s.unresolved, s.note, function)
            )
        return steps

    def add_hit(self, spec: SourceSpec, hit: Hit, sink: SinkSpec) -> None:
        src = self.source_node(spec)
        snk = self.sink_node(sink)
        segments: list[tuple[str, list[VEdge]]] = []
        current: list[VEdge] = []
        for edge in hit.edges:
            current.append(edge)
            if edge.dst.startswith("t|"):
                segments.append((edge.dst, current))
                current = []
        segments.append(("sink", current))
        node_ids = [src.id]
        edge_ids: list[str] = []
        previous = src
        for dst_key, seg in segments:
            target = snk if dst_key == "sink" else self.transform_node(dst_key)
            if target is None:
                continue
            steps = self._steps(seg)
            unresolved = any(s.unresolved for s in steps)
            edge_id = stable_id("E", previous.id, target.id)
            existing = self.graph.edges.get(edge_id)
            if existing is None:
                self.graph.add_edge(
                    Edge(
                        id=edge_id,
                        from_node=previous.id,
                        to_node=target.id,
                        kind="flow",
                        evidence=steps,
                        attrs={
                            "unresolved": unresolved,
                            "files": sorted({s.file for s in steps}),
                            "via_transform": target.kind == "transform",
                        },
                    )
                )
            elif existing.attrs.get("unresolved") and not unresolved:
                existing.evidence = steps
                existing.attrs["unresolved"] = False
            node_ids.append(target.id)
            edge_ids.append(edge_id)
            previous = target
        if sink.persists:
            ret = self.retention_node(snk, sink)
            node_ids.append(ret.id)
            edge_ids.append(stable_id("E", snk.id, ret.id, "persists"))
        reachable = bool(src.attrs.get("reachable")) and bool(snk.attrs.get("reachable"))
        mitigated, mitigation = self._mitigated(snk, hit)
        flow = Flow(
            id=stable_id("F", *node_ids),
            node_ids=node_ids,
            edge_ids=edge_ids,
            reachable=reachable,
            unresolved=hit.unresolved,
            mitigated=mitigated,
            attrs={
                "path_at_sink": list(hit.path_at_sink),
                "field": spec.field,
                "mitigation": mitigation,
                "files": sorted(
                    {
                        s.file
                        for e in edge_ids
                        if e in self.graph.edges
                        for s in self.graph.edges[e].evidence
                    }
                ),
            },
        )
        if all(f.id != flow.id for f in self.graph.flows):
            self.graph.flows.append(flow)
        if hit.unresolved:
            snk.attrs["inherited_unresolved"] = True

    def _mitigated(self, sink_node: Node, hit: Hit) -> tuple[bool, dict[str, Any] | None]:
        event_path = sink_node.attrs.get("event_path")
        for m in sink_node.attrs.get("mitigations", []):
            if m["kind"] != "event_scrubber" or not event_path:
                continue
            scrubbed = {tuple(s) for s in m["scrubs"]}
            key = hit.path_at_sink[0] if hit.path_at_sink else None
            if key is not None and (event_path, key) in scrubbed:
                return True, {
                    "hook": m["hook"],
                    "file": m["file"],
                    "line": m["line"],
                    "scrubs": f"{event_path}.{key}",
                }
        return False, None

    # ------------------------------------------------------------------ driver

    def build(self) -> DataFlowGraph:
        det = self.i.detection
        sources = self.i.engine.sources
        for sid, hits in sorted(self.i.hits.items()):
            spec = sources[sid]
            self.source_node(spec)
            for hit in hits:
                sink = det.sinks.get(hit.sink)
                if sink is not None:
                    self.add_hit(spec, hit, sink)
        for sink in det.sinks.values():
            if sink.family in ALWAYS_LISTED_FAMILIES or sink.registry is not None:
                self.sink_node(sink)
        self.graph.config = [c.to_dict() for c in self.i.config]
        self.graph.summary = self.summary()
        return self.graph

    def summary(self) -> dict[str, Any]:
        g = self.graph
        kinds = Counter(n.kind for n in g.nodes.values())
        flows = g.flows
        unresolved = [f for f in flows if f.unresolved]
        stats = self.i.engine.stats
        languages = Counter(m.language for m in self.project.modules.values())
        return {
            "files": dict(languages),
            "functions": len(self.project.functions),
            "entry_points": sum(1 for e in self.i.reach.roots.values() if e.kind != "module"),
            "library_mode": self.i.reach.library_mode,
            "nodes": dict(kinds),
            "edges": sum(1 for e in g.edges.values() if e.kind == "flow"),
            "flows": len(flows),
            "reachable_flows": sum(1 for f in flows if f.reachable),
            "unreachable_flows": sum(1 for f in flows if not f.reachable),
            "unresolved_flows": len(unresolved),
            "mitigated_flows": sum(1 for f in flows if f.mitigated),
            "coverage": {
                "tainted_paths": len(flows),
                "paths_with_unresolved_step": len(unresolved),
                "unresolved_fraction": round(len(unresolved) / len(flows), 4) if flows else 0.0,
                "resolved_fraction": round(1 - len(unresolved) / len(flows), 4) if flows else 1.0,
                "depth_truncations": stats.depth_truncated,
                "state_limit_hits": stats.state_limited,
                "propagation_states": stats.states,
                "rule_matches_without_ir": len(self.i.detection.unmatched_rules),
                "parse_errors": sum(m.parse_errors for m in self.project.modules.values()),
            },
        }
