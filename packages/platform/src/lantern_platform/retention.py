"""Automatic deletion after a retention period (``LANTERN_RETENTION_HOURS``).

- A run, and everything derived from it (stages, nodes, edges, decisions, findings, reports),
  is deleted once it is older than the period. A run a worker may still be processing (running
  and started within the run timeout) is left for the next sweep.
- A user who has not signed in for the period is deleted, with the runs they started; their
  sessions and installation links go with them (``ON DELETE CASCADE``).
- Expired sessions, pull request comment records, and webhook delivery ids older than the
  period are deleted.

The worker sweeps every hour, the API sweeps when it starts and every hour while it runs, and
``python -m lantern_worker purge`` sweeps once. Each sweep records when it ran, so the web app
states the retention promise only while sweeps are actually happening (``retention_status``).
The API also stops serving runs older than the period before a sweep reaches them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import and_, delete, not_, or_, select

from lantern_platform.db import (
    Database,
    PullRequestComment,
    Run,
    SessionRow,
    SystemState,
    User,
    WebhookDelivery,
    utcnow,
)
from lantern_platform.tokens import aware

STATE_KEY = "retention"
SWEEP_INTERVAL = dt.timedelta(hours=1)
# A sweep older than this means deletion has stopped: the promise is no longer shown.
HEALTHY_WITHIN = 3 * SWEEP_INTERVAL


@dataclass
class PurgeResult:
    runs: int = 0
    users: int = 0
    sessions: int = 0
    pr_comments: int = 0
    webhook_deliveries: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


def cutoff_for(retention_hours: int, now: dt.datetime | None = None) -> dt.datetime | None:
    if retention_hours <= 0:
        return None
    return (now or utcnow()) - dt.timedelta(hours=retention_hours)


def purge_expired(
    db: Database,
    retention_hours: int,
    *,
    run_timeout: dt.timedelta = dt.timedelta(hours=1),
    now: dt.datetime | None = None,
) -> PurgeResult:
    """Delete everything older than the retention period. Does nothing when it is 0."""
    result = PurgeResult()
    now = now or utcnow()
    cutoff = cutoff_for(retention_hours, now)
    if cutoff is None:
        return result
    with db.session() as s:
        signed_in_since = select(SessionRow.user_id).where(SessionRow.created_at >= cutoff)
        stale_users = list(
            s.scalars(
                select(User.id).where(User.created_at < cutoff, User.id.not_in(signed_in_since))
            )
        )
        in_progress = and_(
            Run.status.in_(("running", "cancelling")),
            Run.started_at.is_not(None),
            Run.started_at >= now - run_timeout,
        )
        expired = Run.created_at < cutoff
        if stale_users:
            expired = or_(expired, Run.created_by.in_(stale_users))
        result.runs = _count(s.execute(delete(Run).where(expired, not_(in_progress))))
        if stale_users:
            still_running = select(Run.created_by).where(Run.created_by.is_not(None))
            result.users = _count(
                s.execute(
                    delete(User).where(User.id.in_(stale_users), User.id.not_in(still_running))
                )
            )
        result.sessions = _count(s.execute(delete(SessionRow).where(SessionRow.expires_at < now)))
        result.pr_comments = _count(
            s.execute(delete(PullRequestComment).where(PullRequestComment.updated_at < cutoff))
        )
        result.webhook_deliveries = _count(
            s.execute(delete(WebhookDelivery).where(WebhookDelivery.received_at < cutoff))
        )
        state = s.get(SystemState, STATE_KEY)
        value: dict[str, Any] = {
            "last_sweep_at": now.isoformat(),
            "retention_hours": retention_hours,
            "last_result": result.to_dict(),
        }
        if state is None:
            s.add(SystemState(key=STATE_KEY, value=value, updated_at=now))
        else:
            state.value, state.updated_at = value, now
    return result


def retention_status(
    db: Database, retention_hours: int, now: dt.datetime | None = None
) -> dict[str, Any]:
    """What the web app may promise: the period, when deletion last ran, and whether that is
    recent enough to state the promise."""
    if retention_hours <= 0:
        return {"hours": 0, "active": False, "last_sweep_at": None}
    now = now or utcnow()
    with db.session() as s:
        state = s.get(SystemState, STATE_KEY)
        last = aware(state.updated_at) if state is not None else None
    return {
        "hours": retention_hours,
        "active": last is not None and now - last <= HEALTHY_WITHIN,
        "last_sweep_at": last.isoformat() if last else None,
    }


def _count(result: Any) -> int:
    return int(getattr(result, "rowcount", 0) or 0)
