"""Scheduled maintenance jobs. Invoked from deploy/crontab."""

import sys
from datetime import datetime

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import SignupMetric


def purge_expired_signup_metrics(session: Session) -> int:
    deleted = (
        session.query(SignupMetric).filter(SignupMetric.expires_at < datetime.utcnow()).delete()
    )
    session.commit()
    return int(deleted)


if __name__ == "__main__":
    if sys.argv[1:] == ["purge-signup-metrics"]:
        with SessionLocal() as db:
            print(purge_expired_signup_metrics(db))
