"""Runs: create, follow, read findings and reports, diff, cancel."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, and_, false, func, or_, select

from lantern_api.auth import current_user, services, user_installation_ids
from lantern_api.repos import resolve
from lantern_api.services import Services
from lantern_platform.db import (
    DecisionRowDB,
    EdgeRow,
    FindingRow,
    NodeRow,
    ReportRow,
    Run,
    RunStage,
    User,
    utcnow,
)
from lantern_platform.github import GitHubError, parse_repo
from lantern_platform.retention import cutoff_for
from lantern_platform.storage import load_findings
from lantern_report.context import SourceLinker
from lantern_report.diff import diff_findings

router = APIRouter()
STAGES = ("clone", "parse", "graph", "registry", "classify", "verify", "report")
LANGUAGES = ("python", "typescript")


class RunOptions(BaseModel):
    languages: list[Literal["python", "typescript"]] | None = None
    dynamic: bool = False
    dependency_depth: int = Field(default=0, ge=0, le=1)


class CreateRun(BaseModel):
    owner: str
    repo: str
    ref: str | None = None
    options: RunOptions = RunOptions()


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def visible_run_filter(svc: Services, user: User) -> ColumnElement[bool]:
    """Runs the user may see: their own and their installations', and, with retention on,
    only runs younger than the retention period, even before a sweep deletes the rest."""
    installations = user_installation_ids(svc, user)
    clauses: list[ColumnElement[bool]] = [Run.created_by == user.id]
    if installations:
        clauses.append(Run.installation_id.in_(installations))
    visible = or_(*clauses) if clauses else false()
    cutoff = cutoff_for(svc.settings.retention_hours)
    return and_(visible, Run.created_at >= cutoff) if cutoff is not None else visible


def get_visible_run(svc: Services, user: User, run_id: str) -> Run:
    with svc.db.session() as s:
        run = s.scalar(select(Run).where(Run.id == run_id, visible_run_filter(svc, user)))
        if run is None:
            raise HTTPException(404, "run not found")
        s.expunge(run)
        return run


def run_summary(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "repo": run.repo_full_name,
        "ref": run.ref,
        "sha": run.sha,
        "status": run.status,
        "trigger": run.trigger,
        "mode": run.mode,
        "pr_number": run.pr_number,
        "current_stage": run.current_stage,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "findings": (run.summary or {}).get("findings"),
        "unresolved": (run.summary or {}).get("unresolved"),
    }


def _check_rate(svc: Services, user: User) -> None:
    since = utcnow() - dt.timedelta(hours=1)
    with svc.db.session() as s:
        count = (
            s.scalar(
                select(func.count())
                .select_from(Run)
                .where(Run.created_by == user.id, Run.created_at >= since)
            )
            or 0
        )
        if count >= svc.settings.runs_per_hour:
            oldest = s.scalar(
                select(func.min(Run.created_at)).where(
                    Run.created_by == user.id, Run.created_at >= since
                )
            )
    if count >= svc.settings.runs_per_hour:
        oldest_aware = (
            oldest if oldest and oldest.tzinfo else (oldest or utcnow()).replace(tzinfo=dt.UTC)
        )
        retry = max(1, int((oldest_aware + dt.timedelta(hours=1) - utcnow()).total_seconds()))
        raise HTTPException(
            429,
            f"run limit reached ({svc.settings.runs_per_hour} per hour)",
            headers={"Retry-After": str(retry)},
        )


@router.post("/runs", status_code=201)
def create_run(
    body: CreateRun, user: User = Depends(current_user), svc: Services = Depends(services)
) -> dict[str, Any]:
    _check_rate(svc, user)
    resolution = resolve(svc, user, f"{body.owner}/{body.repo}")
    if not resolution.scannable or resolution.full_name is None:
        raise HTTPException(403, resolution.reason or "repository is not accessible")
    ref = parse_repo(resolution.full_name)
    token = (
        svc.github.installation_token(resolution.installation_id)
        if resolution.installation_id is not None
        else None
    )
    branch = body.ref or resolution.default_branch or "main"
    try:
        sha = svc.github.commit_sha(ref, branch, token)
    except GitHubError as exc:
        raise HTTPException(422, f"cannot resolve ref {branch!r}: {exc}") from exc
    run = Run(
        id=new_run_id(),
        repo_full_name=resolution.full_name,
        installation_id=resolution.installation_id,
        ref=branch,
        sha=sha,
        trigger="manual",
        mode="full",
        options={
            **body.options.model_dump(),
            "visibility": "private" if resolution.private else "public",
        },
        created_by=user.id,
    )
    with svc.db.session() as s:
        s.add(run)
    svc.queue.enqueue(run.id)
    return run_summary(run)


@router.get("/runs/{run_id}")
def get_run(
    run_id: str, user: User = Depends(current_user), svc: Services = Depends(services)
) -> dict[str, Any]:
    run = get_visible_run(svc, user, run_id)
    with svc.db.session() as s:
        rows = {r.stage: r for r in s.scalars(select(RunStage).where(RunStage.run_id == run_id))}
    stages = []
    for name in STAGES:
        row = rows.get(name)
        stages.append(
            {
                "stage": name,
                "status": row.status if row else "pending",
                "started_at": row.started_at.isoformat() if row and row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row and row.finished_at else None,
                "info": row.info if row else {},
            }
        )
    return {
        **run_summary(run),
        "options": run.options,
        "base_sha": run.base_sha,
        "base_run_id": run.base_run_id,
        "changed_files": run.changed_files,
        "stages": stages,
        "coverage": run.coverage,
        "summary": run.summary,
        "error": {"stage": run.error_stage, "message": run.error_message}
        if run.status == "failed"
        else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
    }


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str, user: User = Depends(current_user), svc: Services = Depends(services)
) -> dict[str, Any]:
    get_visible_run(svc, user, run_id)
    with svc.db.session() as s:
        run = s.get(Run, run_id)
        assert run is not None
        if run.status == "queued":
            run.status, run.finished_at = "cancelled", utcnow()
        elif run.status == "running":
            run.status = "cancelling"
        return {"id": run.id, "status": run.status}


@router.get("/runs/{run_id}/findings")
def list_findings(
    run_id: str,
    category: list[str] = Query(default_factory=list),
    severity: list[str] = Query(default_factory=list),
    status: list[str] = Query(default_factory=list),
    verification: list[str] = Query(default_factory=list),
    q: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    user: User = Depends(current_user),
    svc: Services = Depends(services),
) -> dict[str, Any]:
    get_visible_run(svc, user, run_id)
    query = select(FindingRow).where(FindingRow.run_id == run_id)
    if category:
        query = query.where(FindingRow.category.in_(category))
    if severity:
        query = query.where(FindingRow.severity.in_(severity))
    if status:
        query = query.where(FindingRow.status.in_(status))
    if verification:
        query = query.where(FindingRow.verification.in_(verification))
    if q:
        query = query.where(FindingRow.title.ilike(f"%{q}%"))
    with svc.db.session() as s:
        rows = list(s.scalars(query.order_by(FindingRow.id)))
    items = [
        {**r.data, "verification": r.verification}
        for r in rows[(page - 1) * per_page : page * per_page]
    ]
    return {"total": len(rows), "page": page, "per_page": per_page, "items": items}


@router.get("/runs/{run_id}/findings/{finding_id}")
def finding_detail(
    run_id: str,
    finding_id: str,
    user: User = Depends(current_user),
    svc: Services = Depends(services),
) -> dict[str, Any]:
    run = get_visible_run(svc, user, run_id)
    linker = SourceLinker(f"{svc.settings.github_web_url}/{run.repo_full_name}", run.sha)
    with svc.db.session() as s:
        row = s.get(FindingRow, (run_id, finding_id))
        if row is None:
            raise HTTPException(404, "finding not found")
        data = dict(row.data)
        nodes = {
            n.id: n
            for n in s.scalars(
                select(NodeRow).where(NodeRow.run_id == run_id, NodeRow.id.in_(data["node_ids"]))
            )
        }
        edges = {
            e.id: e
            for e in s.scalars(
                select(EdgeRow).where(EdgeRow.run_id == run_id, EdgeRow.id.in_(data["edge_ids"]))
            )
        }
        decisions = list(
            s.scalars(
                select(DecisionRowDB).where(
                    DecisionRowDB.run_id == run_id, DecisionRowDB.id.in_(data["decision_ids"])
                )
            )
        )
        return {
            **data,
            "verification": row.verification,
            "nodes": [
                {
                    "id": n.id,
                    "kind": n.kind,
                    "symbol": n.symbol,
                    "file": n.file,
                    "line_start": n.line_start,
                    "line_end": n.line_end,
                    "snippet": n.snippet,
                    "url": linker.url(n.file, n.line_start),
                    "attrs": {
                        k: n.attrs.get(k)
                        for k in ("field", "family", "entry", "dynamic", "endpoints", "registry")
                        if k in n.attrs
                    },
                }
                for n in (nodes[i] for i in data["node_ids"] if i in nodes)
            ],
            "edges": [
                {
                    "id": e.id,
                    "from": e.from_node,
                    "to": e.to_node,
                    "kind": e.kind,
                    "steps": [
                        {**st, "url": linker.url(st.get("file", ""), st.get("line"))}
                        for st in e.evidence
                    ],
                }
                for e in (edges[i] for i in data["edge_ids"] if i in edges)
            ],
            "decisions": [
                {
                    "id": d.id,
                    "target_id": d.target_id,
                    "question": f"{d.question_set_id}.{d.question_id}",
                    "answer": d.answer,
                    "probability": d.probability,
                    "distribution": d.distribution,
                    "provider": d.provider,
                    "provider_version": d.provider_version,
                }
                for d in decisions
            ],
        }


@router.get("/runs/{run_id}/report")
def get_report(
    run_id: str,
    format: Literal["json", "md", "html", "docx"] = "json",
    download: bool = False,
    user: User = Depends(current_user),
    svc: Services = Depends(services),
) -> Response:
    run = get_visible_run(svc, user, run_id)
    with svc.db.session() as s:
        row = s.get(ReportRow, (run_id, format))
        if row is None:
            raise HTTPException(
                409 if run.status != "completed" else 404, f"no {format} report for this run"
            )
        content, content_type = row.content, row.content_type
    name = f"lantern-{run.repo_full_name.replace('/', '-')}-{run.sha[:12]}.{format}"
    disposition = "attachment" if download or format == "docx" else "inline"
    return Response(
        content,
        media_type=content_type,
        headers={"Content-Disposition": f'{disposition}; filename="{name}"'},
    )


@router.get("/runs/{a}/diff/{b}")
def diff_runs(
    a: str, b: str, user: User = Depends(current_user), svc: Services = Depends(services)
) -> dict[str, Any]:
    before, after = get_visible_run(svc, user, a), get_visible_run(svc, user, b)
    if before.repo_full_name != after.repo_full_name:
        raise HTTPException(422, "runs belong to different repositories")
    if before.status != "completed" or after.status != "completed":
        raise HTTPException(409, "both runs must be completed")
    with svc.db.session() as s:
        diff = diff_findings(load_findings(s, before).findings, load_findings(s, after).findings)
    return {"before": run_summary(before), "after": run_summary(after), **diff.to_dict()}
