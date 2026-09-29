"""Process one queued run: clone, analyze, classify, verify, report, persist, notify.

Stages (``STAGES``) are recorded in ``run_stages`` as they start and finish, and the run's
``current_stage`` always names the stage in progress, so a timeout or crash can be blamed
on a stage. Results are written in one transaction after the report is built: a run is
either ``completed`` with all of its rows or ``failed`` with none. The clone lives in a fresh
temporary directory that is deleted whatever happens.

For pull request runs, a GitHub check run is opened when the job starts and completed at
the end, and the PR comment with the findings diff is created or updated. A failure to talk
to GitHub after the results are stored does not fail the run; it is recorded in the run's
summary.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import traceback
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from lantern_analysis.analyze import AnalysisOptions
from lantern_analysis.secrets import redact
from lantern_decisions.provider import DecisionProvider
from lantern_platform.config import Settings
from lantern_platform.db import Database, PullRequestComment, Run, RunStage, utcnow
from lantern_platform.github import GitHub, parse_repo
from lantern_platform.pr import CHECK_NAME, check_output, render_pr_comment, upsert_pr_comment
from lantern_platform.storage import (
    load_decisions,
    load_findings,
    load_graph,
    save_results,
    scrub_findings,
    scrub_graph,
)
from lantern_report.context import RunInfo
from lantern_report.diff import diff_findings
from lantern_report.docx_render import render_docx
from lantern_report.dpia import build_report
from lantern_report.render import render_html, render_json, render_markdown
from lantern_worker.dynamic.base import Backend, Limits
from lantern_worker.fetch import RepoFetcher
from lantern_worker.incremental import neighbor_files
from lantern_worker.pipeline import (
    DynamicConfig,
    IncrementalScope,
    PipelineConfig,
    PipelineResult,
    run_pipeline,
)

STAGES = ("clone", "parse", "graph", "registry", "classify", "verify", "report")


@dataclass
class WorkerDeps:
    db: Database
    settings: Settings
    fetcher: RepoFetcher
    provider_factory: Callable[[], DecisionProvider]
    github: GitHub | None = None
    dynamic_backend: Callable[[], Backend] | None = None
    workdir_root: Path | None = None
    report_base_url: str | None = None  # the web app's run page, for links in PR comments


class Cancelled(Exception):
    pass


class StageTracker:
    def __init__(self, db: Database, run_id: str) -> None:
        self.db = db
        self.run_id = run_id
        self.current: str | None = None

    def start(self, stage: str, **info: Any) -> None:
        if self.current == stage:
            self.update(**info)
            return
        if self.current is not None:
            self.finish(self.current)
        self.current = stage
        with self.db.session() as s:
            row = s.get(RunStage, (self.run_id, stage))
            if row is None:
                s.add(RunStage(run_id=self.run_id, stage=stage, status="running", info=info))
            else:
                row.status, row.started_at, row.info = "running", utcnow(), info
            run = s.get(Run, self.run_id)
            if run is not None:
                if run.status == "cancelling":
                    raise Cancelled(f"cancelled before {stage}")
                run.current_stage = stage

    def update(self, **info: Any) -> None:
        if self.current is None:
            return
        with self.db.session() as s:
            row = s.get(RunStage, (self.run_id, self.current))
            if row is not None:
                row.info = {**(row.info or {}), **info}

    def finish(self, stage: str, status: str = "done", **info: Any) -> None:
        with self.db.session() as s:
            row = s.get(RunStage, (self.run_id, stage))
            if row is None:
                row = RunStage(run_id=self.run_id, stage=stage, status=status, info=info)
                s.add(row)
            row.status = status
            row.finished_at = utcnow()
            if info:
                row.info = {**(row.info or {}), **info}
        if self.current == stage:
            self.current = None

    @contextmanager
    def stage(self, name: str, **info: Any) -> Any:
        self.start(name, **info)
        yield
        self.finish(name)

    def on_pipeline_stage(self, name: str, info: dict[str, Any]) -> None:
        if name in STAGES:
            self.start(name, **info)


def _repo_url(settings: Settings, full_name: str) -> str:
    return f"{settings.github_web_url.rstrip('/')}/{full_name}"


def _base_scope(deps: WorkerDeps, run: Run) -> tuple[IncrementalScope | None, str | None]:
    """For an incremental run: the base run's decisions and the changed-file neighborhood."""
    if run.mode != "incremental" or not run.base_run_id:
        return None, None
    with deps.db.session() as s:
        base = s.get(Run, run.base_run_id)
        if base is None or base.status != "completed":
            return None, None
        graph = load_graph(s, base)
        decisions = load_decisions(s, base)
    files = neighbor_files(graph, run.changed_files or [])
    return IncrementalScope(files=files, base_decisions=decisions), base.id


def _reports(result_graph: Any, findings: Any, decisions: Any, info: RunInfo) -> dict[str, bytes]:
    report = build_report(result_graph, findings, decisions, info)
    return {
        "json": render_json(report).encode(),
        "md": render_markdown(report).encode(),
        "html": render_html(report).encode(),
        "docx": render_docx(report),
    }


class _Comments:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, repo: str, pr: int) -> int | None:
        with self.db.session() as s:
            row = s.get(PullRequestComment, (repo, pr))
            return row.comment_id if row else None

    def set(self, repo: str, pr: int, comment_id: int) -> None:
        with self.db.session() as s:
            row = s.get(PullRequestComment, (repo, pr))
            if row is None:
                s.add(PullRequestComment(repo_full_name=repo, pr_number=pr, comment_id=comment_id))
            else:
                row.comment_id = comment_id


def _open_check(deps: WorkerDeps, run: Run) -> int | None:
    """Mark the PR's check run in progress, creating it if the webhook did not."""
    if deps.github is None or run.trigger != "pull_request" or run.installation_id is None:
        return None
    repo = parse_repo(run.repo_full_name)
    token = deps.github.installation_token(run.installation_id)
    existing = (run.options or {}).get("check_run_id")
    if existing:
        deps.github.update_check_run(
            repo, token, int(existing), status="in_progress", started_at=utcnow().isoformat()
        )
        return int(existing)
    return deps.github.create_check_run(
        repo,
        token,
        name=CHECK_NAME,
        head_sha=run.sha,
        status="in_progress",
        started_at=utcnow().isoformat(),
        external_id=run.id,
    )


def _notify_pull_request(deps: WorkerDeps, run_id: str, check_run_id: int | None) -> None:
    """Post the findings diff to the PR comment and complete the check run."""
    if deps.github is None:
        return
    with deps.db.session() as s:
        run = s.get(Run, run_id)
        if (
            run is None
            or run.trigger != "pull_request"
            or run.installation_id is None
            or run.pr_number is None
        ):
            return
        head = load_findings(s, run)
        base_run = s.get(Run, run.base_run_id) if run.base_run_id else None
        before = (
            load_findings(s, base_run).findings
            if base_run and base_run.status == "completed"
            else []
        )
        totals = dict(run.summary or {})
        repo, pr, sha, base_sha, installation = (
            parse_repo(run.repo_full_name),
            run.pr_number,
            run.sha,
            run.base_sha,
            run.installation_id,
        )
    diff = diff_findings(before, head.findings)
    report_url = (
        f"{deps.report_base_url.rstrip('/')}/runs/{run_id}" if deps.report_base_url else None
    )
    token = deps.github.installation_token(installation)
    body = render_pr_comment(diff, sha, base_sha, report_url, totals)
    upsert_pr_comment(deps.github, repo, pr, token, body, _Comments(deps.db))
    if check_run_id is not None:
        out = check_output(diff, totals, report_url)
        deps.github.update_check_run(
            repo,
            token,
            check_run_id,
            status="completed",
            conclusion=out["conclusion"],
            completed_at=utcnow().isoformat(),
            output=out["output"],
        )


def _fail_check(deps: WorkerDeps, run: Run, check_run_id: int | None, stage: str) -> None:
    if deps.github is None or check_run_id is None or run.installation_id is None:
        return
    token = deps.github.installation_token(run.installation_id)
    deps.github.update_check_run(
        parse_repo(run.repo_full_name),
        token,
        check_run_id,
        status="completed",
        conclusion="neutral",
        completed_at=utcnow().isoformat(),
        output={
            "title": "Lantern could not complete this run",
            "summary": f"The run failed during the {stage} stage. No findings were recorded.",
        },
    )


def mark_failed(
    db: Database, run_id: str, stage: str | None, message: str, status: str = "failed"
) -> None:
    with db.session() as s:
        run = s.get(Run, run_id)
        if run is None or run.status in ("completed", "failed", "cancelled"):
            return
        run.status = "cancelled" if run.status == "cancelling" else status
        run.error_stage = stage or run.current_stage or "clone"
        run.error_message = redact(message)[:2000]
        run.finished_at = utcnow()
        for row in s.scalars(
            select(RunStage).where(RunStage.run_id == run_id, RunStage.status == "running")
        ):
            row.status, row.finished_at = "failed", utcnow()


def process_run(run_id: str, deps: WorkerDeps) -> str:
    """Run one job end to end. Returns the final status."""
    with deps.db.session() as s:
        run = s.get(Run, run_id)
        if run is None:
            return "missing"
        if run.status != "queued":
            return run.status
        run.status, run.started_at = "running", utcnow()
        snapshot = Run(**{c.key: getattr(run, c.key) for c in Run.__table__.columns})
    tracker = StageTracker(deps.db, run_id)
    workdir = Path(tempfile.mkdtemp(prefix="lantern-run-", dir=deps.workdir_root))
    check_run_id: int | None = None
    try:
        try:
            check_run_id = _open_check(deps, snapshot)
        except Exception as exc:
            _note(deps.db, run_id, "github_error", f"check run: {exc}")
        with tracker.stage("clone"):
            public_only = deps.settings.public_repos_only
            if public_only and (snapshot.options or {}).get("visibility") == "private":
                raise PermissionError("this instance scans public repositories only")
            deps.fetcher.fetch(
                snapshot.repo_full_name,
                snapshot.sha,
                # Anonymous in public-only mode, so a private repository cannot be cloned even
                # if one got past the API.
                None if public_only else snapshot.installation_id,
                workdir / "repo",
            )
        scope, base_id = _base_scope(deps, snapshot)
        options = snapshot.options or {}
        dynamic = None
        if options.get("dynamic") and deps.dynamic_backend is not None:
            dynamic = DynamicConfig(deps.dynamic_backend(), Limits())
        config = PipelineConfig(
            provider=deps.provider_factory(),
            question_set_version=snapshot.question_set_version or "v1",
            analysis=AnalysisOptions(
                languages=list(options.get("languages") or []) or None,
                dependency_depth=int(options.get("dependency_depth", 0)),
            ),
            dynamic=dynamic,
            incremental=scope,
        )
        result: PipelineResult = run_pipeline(
            workdir / "repo",
            snapshot.sha,
            config,
            run_id=run_id,
            on_stage=tracker.on_pipeline_stage,
        )
        if tracker.current:
            tracker.finish(tracker.current)
        if dynamic is None:
            tracker.finish(
                "verify",
                status="skipped",
                reason="not requested" if not options.get("dynamic") else "no sandbox",
            )
        elif result.verification is not None:
            tracker.finish(
                "verify",
                status=result.verification.status
                if result.verification.status != "completed"
                else "done",
            )
        with tracker.stage("report"):
            graph = scrub_graph(result.graph)
            findings = scrub_findings(result.findings)
            graph.repo = snapshot.repo_full_name
            info = RunInfo(
                repo=snapshot.repo_full_name,
                commit=snapshot.sha,
                run_id=run_id,
                repo_url=_repo_url(deps.settings, snapshot.repo_full_name),
            )
            reports = _reports(graph, findings, result.decisions, info)
            with deps.db.session() as s:
                run = s.get(Run, run_id)
                assert run is not None
                save_results(s, run, graph, result.decisions, findings, reports)
                run.summary = {
                    **run.summary,
                    "classification": result.classification,
                    "timings": result.timings,
                    "base_run_id": base_id,
                }
                run.provider = config.provider.name
        with deps.db.session() as s:
            run = s.get(Run, run_id)
            assert run is not None
            run.status, run.finished_at, run.current_stage = "completed", utcnow(), None
    except BaseException as exc:
        stage = tracker.current
        detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        if isinstance(exc, Cancelled):
            mark_failed(deps.db, run_id, stage, detail, status="cancelled")
            return "cancelled"
        mark_failed(deps.db, run_id, stage, detail + "\n" + traceback.format_exc(limit=3))
        with contextlib.suppress(Exception):
            _fail_check(deps, snapshot, check_run_id, stage or "clone")
        if not isinstance(exc, Exception):
            raise
        return "failed"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    try:
        _notify_pull_request(deps, run_id, check_run_id)
    except Exception as exc:
        _note(deps.db, run_id, "github_error", f"pull request update: {exc}")
    return "completed"


def _note(db: Database, run_id: str, key: str, message: str) -> None:
    with db.session() as s:
        run = s.get(Run, run_id)
        if run is not None:
            run.summary = {**(run.summary or {}), key: redact(message)[:500]}
