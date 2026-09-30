"""The public demo's switches: public repositories only, retention, and the proxy secret."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from lantern_api.main import PROXY_HEADER, create_app
from lantern_api.services import Services
from lantern_platform.config import Settings
from lantern_platform.db import Run, User, utcnow
from lantern_platform.queue import InMemoryQueue
from lantern_worker.jobs import WorkerDeps, process_run

from .conftest import WEBHOOK_SECRET, FakeGitHub, MappedFetcher


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return dataclasses.replace(
        settings, public_repos_only=True, retention_hours=72, session_ttl_hours=72
    )


def _deliver(client: TestClient, event: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-github-event": event,
            "x-github-delivery": f"d-{time.time_ns()}",
            "x-hub-signature-256": signature,
            "content-type": "application/json",
        },
    )


def _add_run(svc: Services, rid: str, age: dt.timedelta, **kw: Any) -> None:
    with svc.db.session() as s:
        s.add(
            Run(
                id=rid,
                repo_full_name=kw.pop("repo", "oss/public-lib"),
                ref="trunk",
                sha="b" * 40,
                created_at=utcnow() - age,
                **kw,
            )
        )


def _user_id(svc: Services) -> int:
    with svc.db.session() as s:
        return int(s.scalar(select(User.id).where(User.login == "octo")) or 0)


def test_config_states_the_switches_and_that_deletion_is_running(client: TestClient) -> None:
    config = client.get("/config").json()
    assert config["public_repos_only"] is True
    assert config["session_ttl_hours"] == 72
    # The app's startup sweep ran, so the promise may be shown.
    assert config["retention"]["hours"] == 72 and config["retention"]["active"] is True


def test_private_repositories_cannot_be_scanned(
    signed_in: TestClient, queue: InMemoryQueue
) -> None:
    installed_private = signed_in.post(
        "/repos/resolve", json={"input": "acme/canary-python"}
    ).json()
    assert installed_private["status"] == "private_not_allowed"
    assert installed_private["scannable"] is False
    assert "public repositories only" in installed_private["reason"]
    visible_private = signed_in.post("/repos/resolve", json={"input": "acme/secret-tool"}).json()
    assert visible_private["status"] == "private_not_allowed"
    public = signed_in.post("/repos/resolve", json={"input": "oss/public-lib"}).json()
    assert public["status"] == "public" and public["scannable"] is True

    refused = signed_in.post("/runs", json={"owner": "acme", "repo": "canary-python"})
    assert refused.status_code == 403 and "public repositories only" in refused.json()["detail"]
    assert signed_in.post("/runs", json={"owner": "oss", "repo": "public-lib"}).status_code == 201

    listed = {r["full_name"]: r["scannable"] for r in signed_in.get("/repos").json()["items"]}
    assert listed == {"acme/canary-python": False, "acme/canary-typescript": False}


def test_webhooks_for_private_repositories_start_nothing(
    client: TestClient, queue: InMemoryQueue, fake: FakeGitHub
) -> None:
    repo = {"full_name": "acme/canary-python", "private": True, "default_branch": "main"}
    push = {
        "ref": "refs/heads/main",
        "after": "c" * 40,
        "repository": repo,
        "installation": {"id": fake.installation_id},
    }
    response = _deliver(client, "push", push).json()
    assert "private repository" in response["ignored"]
    pr = {
        "action": "opened",
        "pull_request": {
            "number": 3,
            "head": {"sha": "d" * 40, "ref": "f"},
            "base": {"sha": "e" * 40, "ref": "main"},
        },
        "repository": repo,
        "installation": {"id": fake.installation_id},
    }
    assert "private repository" in _deliver(client, "pull_request", pr).json()["ignored"]
    assert queue.dequeue(timeout=0) is None


def test_the_worker_refuses_private_runs_and_clones_anonymously(
    svc: Services, deps: WorkerDeps, fetcher: MappedFetcher
) -> None:
    _add_run(
        svc,
        "run-private",
        dt.timedelta(0),
        repo="acme/canary-python",
        installation_id=100,
        options={"visibility": "private"},
    )
    assert process_run("run-private", deps) == "failed"
    with svc.db.session() as s:
        run = s.get(Run, "run-private")
        assert run is not None and "public repositories only" in (run.error_message or "")
    assert fetcher.fetched == []

    _add_run(
        svc, "run-public", dt.timedelta(0), installation_id=100, options={"visibility": "public"}
    )
    assert process_run("run-public", deps) == "completed"
    assert fetcher.fetched == [("oss/public-lib", "b" * 40, None)]  # no installation token


def test_expired_runs_are_not_served_even_before_a_sweep(
    signed_in: TestClient, svc: Services
) -> None:
    uid = _user_id(svc)
    _add_run(svc, "run-expired", dt.timedelta(hours=73), created_by=uid, status="completed")
    _add_run(svc, "run-fresh", dt.timedelta(hours=1), created_by=uid, status="completed")
    assert signed_in.get("/runs/run-expired").status_code == 404
    assert signed_in.get("/runs/run-fresh").status_code == 200
    listed = signed_in.get("/repos/oss/public-lib/runs").json()["items"]
    assert [r["id"] for r in listed] == ["run-fresh"]


def test_the_api_sweeps_when_it_starts(svc: Services) -> None:
    _add_run(svc, "run-expired", dt.timedelta(hours=100), status="completed")
    with TestClient(create_app(svc)), svc.db.session() as s:
        assert s.get(Run, "run-expired") is None


def test_only_the_proxy_may_call_the_api_when_a_secret_is_set(
    settings: Settings, fake: FakeGitHub
) -> None:
    locked = dataclasses.replace(settings, proxy_secret="s3cret")
    app = create_app(Services.build(locked, queue=InMemoryQueue(), transport=fake.transport()))
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200  # the host's health check
        assert client.get("/config").status_code == 404
        assert client.get("/config", headers={PROXY_HEADER: "wrong"}).status_code == 404
        assert (
            client.get("/config", headers={PROXY_HEADER: "s3crét".encode("latin-1")}).status_code
            == 404
        )
        assert client.get("/config", headers={PROXY_HEADER: "s3cret"}).status_code == 200


def test_the_worker_sweeps_as_it_serves(svc: Services, settings: Settings) -> None:
    from lantern_worker import __main__ as worker_main

    _add_run(svc, "run-expired", dt.timedelta(hours=100), status="completed")
    ticks = iter([0.0, 0.0, 10.0])
    worker_main.serve(settings, InMemoryQueue(), clock=lambda: next(ticks), iterations=1)
    with svc.db.session() as s:
        assert s.get(Run, "run-expired") is None
