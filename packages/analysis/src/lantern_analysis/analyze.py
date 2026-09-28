"""Entry point: analyze a checked-out repository into a data-flow graph."""

from __future__ import annotations

import tempfile
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
from lantern_registry.resolver import (
    DependencyProfile,
    PackageSource,
    RegistrySources,
    resolve_unregistered,
)


@dataclass
class AnalysisOptions:
    depth_limit: int = 12
    track_internal_ids: bool = False
    exclude: list[str] = field(default_factory=list)
    languages: list[str] | None = None
    library_mode: str = "auto"
    max_states_per_source: int = 250_000
    dependency_depth: int = 0  # 1: profile unregistered dependencies from their source
    dependency_cap: int = 10


def analyze_repo(
    root: Path | str,
    commit: str = "",
    options: AnalysisOptions | None = None,
    registry: Registry | None = None,
    repo_name: str | None = None,
    package_source: PackageSource | None = None,
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
    dependencies = (
        _profile_dependencies(project, registry, options, package_source or RegistrySources())
        if options.dependency_depth >= 1
        else []
    )
    detection = Detector(project, matches, lexicon, registry, dependencies).run()
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
    graph.summary["dependencies"] = {
        "analyzed": sum(1 for d in dependencies if d.status in ("profiled", "no-network")),
        "skipped": sum(1 for d in dependencies if d.status not in ("profiled", "no-network")),
        "profiles": [d.to_dict() for d in dependencies],
        "depth": options.dependency_depth,
    }
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


def imported_packages(project: Project) -> dict[str, set[str]]:
    """Import specifiers that resolve outside the project, by language."""
    out: dict[str, set[str]] = {}
    for module in project.modules.values():
        for imp in module.imports.values():
            if imp.type_only:
                continue
            sym = project.resolve_import(module, imp)
            if type(sym).__name__ != "ExternalSym":
                continue
            spec = sym.spec  # type: ignore[union-attr]
            if module.language == "python":
                spec = imp.module if imp.level == 0 else ""
            if spec and not spec.startswith("."):
                out.setdefault(module.language, set()).add(spec)
    return out


def _profile_dependencies(
    project: Project, registry: Registry, options: AnalysisOptions, source: PackageSource
) -> list[DependencyProfile]:
    from lantern_analysis.deps import profile_package

    with tempfile.TemporaryDirectory(prefix="lantern-deps-") as workdir:
        return resolve_unregistered(
            project.root,
            imported_packages(project),
            registry,
            lambda path, language, name: profile_package(path, language, name, registry),
            source,
            Path(workdir),
            cap=options.dependency_cap,
        )
