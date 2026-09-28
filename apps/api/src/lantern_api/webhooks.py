"""GitHub webhooks.

- ``installation``: record, suspend, or delete installations.
- ``push`` to the default branch: enqueue a full run.
- ``pull_request`` opened, reopened, or synchronize: enqueue an incremental run on the
  changed files and their graph neighbors from the latest completed base-branch run (a full
  run if there is none), and open a queued check run so the PR shows Lantern immediately.
  The worker completes the check and upserts the single PR comment.

Deliveries are verified with X-Hub-Signature-256 and de-duplicated by X-GitHub-Delivery.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from lantern_api.auth import services, upsert_installation
from lantern_api.runs import new_run_id
from lantern_api.services import Services
from lantern_platform.db import Installation, Run, WebhookDelivery
from lantern_platform.github import GitHubError, parse_repo, verify_webhook_signature
from lantern_platform.pr import CHECK_NAME

router = APIRouter()
ZERO_SHA = "0" * 40


@router.post("/webhooks/github")
async def github_webhook(request: Request, svc: Services = Depends(services)) -> dict[str, Any]:
    body = await request.body()
    if not verify_webhook_signature(
        svc.settings.github_webhook_secret, body, request.headers.get("x-hub-signature-256")
    ):
        raise HTTPException(401, "invalid signature")
    event = request.headers.get("x-github-event", "")
    delivery = request.headers.get("x-github-delivery", "")
    if delivery:
        try:
            with svc.db.session() as s:
                s.add(WebhookDelivery(id=delivery[:64], event=event[:40]))
        except IntegrityError:
            return {"ok": True, "duplicate": True}
    payload = json.loads(body or b"{}")
    if event == "ping":
        return {"ok": True}
    if event == "installation":
        return handle_installation(svc, payload)
    if event == "push":
        return handle_push(svc, payload)
    if event == "pull_request":
        return handle_pull_request(svc, payload)
    return {"ok": True, "ignored": event}


def handle_installation(svc: Services, payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    inst = payload.get("installation") or {}
    if action == "deleted":
        with svc.db.session() as s:
            s.execute(delete(Installation).where(Installation.id == int(inst["id"])))
    else:
        upsert_installation(svc, inst)
    svc.repo_cache.clear()
    return {"ok": True, "installation": inst.get("id"), "action": action}


def _enqueue(svc: Services, run: Run) -> dict[str, Any]:
    with svc.db.session() as s:
        s.add(run)
    svc.queue.enqueue(run.id)
    return {"ok": True, "run_id": run.id, "mode": run.mode}


def handle_push(svc: Services, payload: dict[str, Any]) -> dict[str, Any]:
    repo = payload.get("repository") or {}
    default = repo.get("default_branch")
    ref = str(payload.get("ref", ""))
    after = str(payload.get("after", ""))
    if payload.get("deleted") or after == ZERO_SHA or ref != f"refs/heads/{default}":
        return {"ok": True, "ignored": "not a push to the default branch"}
    run = Run(
        id=new_run_id(),
        repo_full_name=repo["full_name"],
        installation_id=(payload.get("installation") or {}).get("id"),
        ref=str(default),
        sha=after,
        trigger="push",
        mode="full",
        options={"visibility": "private" if repo.get("private") else "public"},
    )
    return _enqueue(svc, run)


def base_run_for(svc: Services, full_name: str, base_sha: str, base_ref: str) -> Run | None:
    """The completed run at the PR's base commit, else the latest completed base-branch run."""
    with svc.db.session() as s:
        exact = s.scalar(
            select(Run)
            .where(Run.repo_full_name == full_name, Run.sha == base_sha, Run.status == "completed")
            .order_by(Run.finished_at.desc())
        )
        run = exact or s.scalar(
            select(Run)
            .where(Run.repo_full_name == full_name, Run.ref == base_ref, Run.status == "completed")
            .order_by(Run.finished_at.desc())
        )
        if run is not None:
            s.expunge(run)
        return run


def handle_pull_request(svc: Services, payload: dict[str, Any]) -> dict[str, Any]:
    action = payload.get("action")
    if action not in ("opened", "reopened", "synchronize"):
        return {"ok": True, "ignored": f"pull_request.{action}"}
    pr = payload["pull_request"]
    repo = payload["repository"]
    installation_id = int((payload.get("installation") or {})["id"])
    ref = parse_repo(repo["full_name"])
    head_sha, base_sha, base_ref = pr["head"]["sha"], pr["base"]["sha"], pr["base"]["ref"]
    token = svc.github.installation_token(installation_id)
    try:
        changed = svc.github.pull_files(ref, int(pr["number"]), token)
    except GitHubError:
        changed = []
    base = base_run_for(svc, repo["full_name"], base_sha, base_ref)
    options: dict[str, Any] = {"visibility": "private" if repo.get("private") else "public"}
    with contextlib.suppress(GitHubError):  # the worker creates the check if this fails
        options["check_run_id"] = svc.github.create_check_run(
            ref,
            token,
            name=CHECK_NAME,
            head_sha=head_sha,
            status="queued",
            output={
                "title": "Queued",
                "summary": "Lantern will post a findings diff when the run completes.",
            },
        )
    run = Run(
        id=new_run_id(),
        repo_full_name=repo["full_name"],
        installation_id=installation_id,
        ref=str(pr["head"]["ref"]),
        sha=head_sha,
        trigger="pull_request",
        mode="incremental" if base is not None and changed else "full",
        pr_number=int(pr["number"]),
        base_sha=base_sha,
        base_run_id=base.id if base else None,
        changed_files=changed,
        options=options,
    )
    return _enqueue(svc, run)
