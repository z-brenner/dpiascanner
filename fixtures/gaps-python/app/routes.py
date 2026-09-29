import logging

from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import select

from app.deps import SessionDep
from app.mailer import send_welcome
from app.models import Member, Subscriber, User

router = APIRouter(prefix="/users")
logger = logging.getLogger("gaps")


class UserCreate(BaseModel):
    email: str
    full_name: str


@router.post("/")
def create_user(session: SessionDep, user_in: UserCreate) -> dict[str, int | None]:
    user = User(email=user_in.email, full_name=user_in.full_name)
    session.add(user)
    session.commit()
    send_welcome(user_in.email, user_in.full_name)
    return {"id": user.id}


@router.post("/digest")
def queue_digest(session: SessionDep) -> dict[str, bool]:
    for subscriber in session.exec(select(Subscriber)).all():
        logger.info("digest queued for %s", subscriber.email)
    return {"ok": True}


@router.get("/members/{member_id}")
def read_member(member_id: int, session: SessionDep) -> dict[str, str]:
    statement = select(Member).where(Member.id == member_id)
    member = session.exec(statement).first()
    if member is not None:
        logger.info("member viewed: %s", member.full_name)
    return {"status": "ok"}
