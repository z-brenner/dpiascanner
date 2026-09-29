from fastapi import APIRouter
from pydantic import BaseModel

from app.notify import send_account_email

router = APIRouter(prefix="/invites")


class Invite(BaseModel):
    email: str
    password: str


@router.post("/")
def invite(invite_in: Invite) -> dict[str, bool]:
    send_account_email(invite_in.email, invite_in.password)
    return {"ok": True}
