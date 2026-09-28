from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import HealthRecord

router = APIRouter(prefix="/health", tags=["health"])


@router.post("/intake", status_code=201)
def submit_intake(
    user_id: int = Form(...),
    condition: str = Form(...),
    session: Session = Depends(get_session),
) -> dict[str, int]:
    record = HealthRecord(user_id=user_id, condition=condition)
    session.add(record)
    session.commit()
    return {"id": record.id}
