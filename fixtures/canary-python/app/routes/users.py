import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analytics import track_signup
from app.billing import create_billing_customer
from app.db import get_session
from app.errors import DuplicateAccountError
from app.models import SignupMetric, User
from app.observability import identify_user
from app.schemas import SignupRequest
from app.storage import upload_export

router = APIRouter(prefix="/users", tags=["users"])

SIGNUP_METRIC_TTL = timedelta(days=90)


def _age_on(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


@router.post("", status_code=201)
def create_user(
    payload: SignupRequest, session: Session = Depends(get_session)
) -> dict[str, object]:
    if session.query(User).filter(User.email == payload.email).first() is not None:
        raise DuplicateAccountError(f"account already exists for {payload.email}")

    user = User(email=payload.email, full_name=payload.full_name, plan=payload.plan)
    session.add(user)
    session.flush()

    age = _age_on(payload.date_of_birth, date.today())
    metric = SignupMetric(
        age=age, cohort=payload.plan, expires_at=datetime.utcnow() + SIGNUP_METRIC_TTL
    )
    session.add(metric)

    identify_user(user.id, payload.email)
    track_signup(user.id, payload.email, payload.plan)
    if payload.payment_method_id:
        user.stripe_customer_id = create_billing_customer(user, payload.payment_method_id)
    session.commit()
    return {"id": user.id, "plan": user.plan}


@router.post("/{user_id}/export", status_code=202)
def export_user_data(user_id: int, session: Session = Depends(get_session)) -> dict[str, str]:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    document = {
        "email": user.email,
        "full_name": user.full_name,
        "plan": user.plan,
        "created_at": user.created_at.isoformat(),
    }
    key = upload_export(json.dumps(document).encode("utf-8"))
    return {"key": key}
