"""Entry point: analyze a checked-out repository into a data-flow graph."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lantern_analysis.builder import BuildInputs, GraphBuilder
from lantern_analysis.configres import collect_config
from lantern_analysis.detect import Detector
from lantern_analysis.lexicon import Lexicon
from lantern_analysis.mitigations import find_mitigations
from lantern_analysis.model import DataFlowGraph
from lantern_analysis.project import Project
from lantern_analysis.reach import compute_reachability
from lantern_analysis.repo import RepoSettings, discover
from lantern_analysis.rules import RuleMatch, run_semgrep
from lantern_analysis.taint import TaintEngine
from lantern_registry import Registry, load_registry


@dataclass
class AnalysisOptions:
    depth_limit: int = 12
    track_internal_ids: bool = False
    exclude: list[str] = field(default_factory=list)
    languages: list[str] | None = None
    library_mode: str = "auto"
    max_states_per_source: int = 250_000


def analyze_repo(
    root: Path | str,
    commit: str = "",
    options: AnalysisOptions | None = None,
    registry: Registry | None = None,
    repo_name: str | None = None,
) -> DataFlowGraph:
    started = time.monotonic()
    root = Path(root).resolve()
    options = options or AnalysisOptions()
    settings = RepoSettings.load(root)
    files = discover(root, settings, options.exclude)
    if options.languages:
        files = {k: v for k, v in files.items() if k in options.languages}
    project = Project.load(root, files)
    matches: list[RuleMatch] = []
    for language, paths in files.items():
        matches.extend(run_semgrep(root, paths, language))
    lexicon = Lexicon.load(track_internal_ids=options.track_internal_ids)
    registry = registry or load_registry()
    detection = Detector(project, matches, lexicon, registry).run()
    engine = TaintEngine(
        project, detection, lexicon, options.depth_limit, options.max_states_per_source
    )
    hits = engine.run()
    reach = compute_reachability(project, detection, options.library_mode)
    mitigations = find_mitigations(project, registry)
    config = collect_config(root)
    graph = GraphBuilder(
        BuildInputs(project, detection, engine, hits, reach, mitigations, config),
        repo=repo_name or root.name,
        commit=commit,
        options={**asdict(options), "lexicon": lexicon.version, "registry": registry.version},
    ).build()
    graph.summary["duration_s"] = round(time.monotonic() - started, 3)
    graph.summary["entry_points_detail"] = [
        {
            "function": project.functions[e.fid].qualname,
            "file": project.functions[e.fid].span.file,
            "line": project.functions[e.fid].span.start_line,
            "kind": e.kind,
            "method": e.method,
            "path": e.path,
        }
        for e in reach.roots.values()
        if e.kind != "module" and e.fid in project.functions
    ]
    return graph
