"""Retention: what is deleted after LANTERN_RETENTION_HOURS, and what the web app may promise."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from lantern_platform.config import Settings, normalize_database_url
from lantern_platform.db import (
    Database,
    FindingRow,
    Installation,
    NodeRow,
    PullRequestComment,
    ReportRow,
    Run,
    SessionRow,
    User,
    UserInstallation,
    WebhookDelivery,
)
from lantern_platform.retention import purge_expired, retention_status

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)
HOURS = dt.timedelta(hours=1)


def _db() -> Database:
    db = Database("sqlite://")
    db.create_all()
    return db


def _user(db: Database, uid: int, created: dt.datetime, signed_in: dt.datetime | None) -> None:
    with db.session() as s:
        s.add(User(id=uid, github_id=1000 + uid, login=f"u{uid}", created_at=created))
        s.flush()
        if signed_in is not None:
            s.add(
                SessionRow(
                    token_hash=f"h{uid}",
                    user_id=uid,
                    created_at=signed_in,
                    expires_at=signed_in + 72 * HOURS,
                )
            )


def _run(
    db: Database,
    rid: str,
    created: dt.datetime,
    by: int | None = None,
    status: str = "completed",
    started: dt.datetime | None = None,
) -> None:
    with db.session() as s:
        s.add(
            Run(
                id=rid,
                repo_full_name="o/r",
                ref="main",
                sha="a" * 40,
                status=status,
                created_by=by,
                created_at=created,
                started_at=started or created,
            )
        )
        s.flush()
        s.add(
            NodeRow(
                run_id=rid,
                id="N1",
                kind="source",
                file="app.py",
                line_start=1,
                line_end=1,
                symbol="f:x.email",
                language="python",
                snippet="x.email",
            )
        )
        s.add(
            FindingRow(
                run_id=rid,
                id="F-0001",
                key="k",
                category="personal_data_processing",
                severity="low",
                status="resolved",
                title="t",
                data={},
            )
        )
        s.add(ReportRow(run_id=rid, format="json", content_type="application/json", content=b"{}"))


def _count(db: Database, model: type) -> int:
    with db.session() as s:
        return int(s.scalar(select(func.count()).select_from(model)) or 0)


def test_runs_older_than_the_period_are_deleted_with_everything_derived_from_them() -> None:
    db = _db()
    _user(db, 1, NOW - 200 * HOURS, signed_in=NOW - 2 * HOURS)
    _run(db, "run-old", NOW - 73 * HOURS, by=1)
    _run(db, "run-new", NOW - 71 * HOURS, by=1)
    result = purge_expired(db, 72, now=NOW)
    assert result.runs == 1
    with db.session() as s:
        assert s.get(Run, "run-old") is None and s.get(Run, "run-new") is not None
        assert s.scalars(select(NodeRow.run_id)).all() == ["run-new"]
        assert s.scalars(select(FindingRow.run_id)).all() == ["run-new"]
        assert s.scalars(select(ReportRow.run_id)).all() == ["run-new"]
    assert _count(db, User) == 1  # signed in two hours ago


def test_users_who_have_not_signed_in_are_deleted_with_their_runs_and_links() -> None:
    db = _db()
    _user(db, 1, NOW - 100 * HOURS, signed_in=NOW - 80 * HOURS)  # gone
    _user(db, 2, NOW - 100 * HOURS, signed_in=NOW - 10 * HOURS)  # stays
    _user(db, 3, NOW - 1 * HOURS, signed_in=None)  # just created, stays
    with db.session() as s:
        s.add(Installation(id=7, account_login="acme"))
        s.flush()
        s.add(UserInstallation(user_id=1, installation_id=7))
    _run(db, "run-recent-by-1", NOW - 5 * HOURS, by=1)
    result = purge_expired(db, 72, now=NOW)
    assert result.users == 1 and result.runs == 1
    with db.session() as s:
        assert sorted(s.scalars(select(User.id))) == [2, 3]
        assert s.scalars(select(UserInstallation.user_id)).all() == []
        assert s.scalars(select(SessionRow.user_id)).all() == [2]  # user 1's went with them
        assert s.get(Installation, 7) is not None  # the app is still installed


def test_a_run_a_worker_may_still_be_processing_waits_for_the_next_sweep() -> None:
    db = _db()
    _user(db, 1, NOW - 200 * HOURS, signed_in=NOW - 100 * HOURS)
    _run(
        db,
        "run-live",
        NOW - 80 * HOURS,
        by=1,
        status="running",
        started=NOW - 10 * dt.timedelta(minutes=1),
    )
    _run(db, "run-dead", NOW - 80 * HOURS, status="running", started=NOW - 79 * HOURS)
    result = purge_expired(db, 72, now=NOW)
    assert result.runs == 1  # the dead one; the live one is still within the run timeout
    with db.session() as s:
        assert s.get(Run, "run-live") is not None
        assert s.get(User, 1) is not None  # kept until its last run can go
    assert purge_expired(db, 72, now=NOW + 2 * HOURS).users == 1


def test_expired_sessions_and_old_bookkeeping_go_too() -> None:
    db = _db()
    _user(db, 1, NOW - 10 * HOURS, signed_in=NOW - 100 * HOURS)
    with db.session() as s:
        s.add(
            SessionRow(token_hash="live", user_id=1, created_at=NOW - HOURS, expires_at=NOW + HOURS)
        )
        s.add(PullRequestComment(repo_full_name="o/r", pr_number=1, updated_at=NOW - 90 * HOURS))
        s.add(PullRequestComment(repo_full_name="o/r", pr_number=2, updated_at=NOW - HOURS))
        s.add(WebhookDelivery(id="d-old", event="push", received_at=NOW - 90 * HOURS))
        s.add(WebhookDelivery(id="d-new", event="push", received_at=NOW - HOURS))
    result = purge_expired(db, 72, now=NOW)
    assert (result.sessions, result.pr_comments, result.webhook_deliveries) == (1, 1, 1)
    with db.session() as s:
        assert s.scalars(select(SessionRow.token_hash)).all() == ["live"]


def test_zero_keeps_everything_and_promises_nothing() -> None:
    db = _db()
    _run(db, "run-ancient", NOW - 10_000 * HOURS)
    assert purge_expired(db, 0, now=NOW).runs == 0
    assert _count(db, Run) == 1
    assert retention_status(db, 0, now=NOW) == {"hours": 0, "active": False, "last_sweep_at": None}


def test_the_promise_holds_only_while_sweeps_keep_running() -> None:
    db = _db()
    assert retention_status(db, 72, now=NOW)["active"] is False  # configured, never ran
    purge_expired(db, 72, now=NOW)
    status = retention_status(db, 72, now=NOW + 2 * HOURS)
    assert status["active"] is True and status["hours"] == 72
    assert status["last_sweep_at"] == NOW.isoformat()
    assert retention_status(db, 72, now=NOW + 4 * HOURS)["active"] is False  # sweeps stopped


def test_settings_read_the_demo_switches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANTERN_RETENTION_HOURS", "72")
    monkeypatch.setenv("LANTERN_PUBLIC_REPOS_ONLY", "true")
    monkeypatch.setenv("LANTERN_SESSION_TTL_HOURS", "72")
    monkeypatch.setenv("LANTERN_PROXY_SECRET", "s3cret")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@db.internal:5432/lantern")
    settings = Settings.from_env()
    assert settings.retention_hours == 72 and settings.public_repos_only
    assert settings.session_ttl_hours == 72 and settings.proxy_secret == "s3cret"
    assert settings.database_url == "postgresql+psycopg://u:p@db.internal:5432/lantern"
    assert normalize_database_url("postgresql://x/y") == "postgresql+psycopg://x/y"
    assert normalize_database_url("sqlite:///a.db") == "sqlite:///a.db"
