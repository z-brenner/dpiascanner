"""Installation flow and sessions.

GitHub redirects the browser to the web app after installation (the app's Setup URL) with
``installation_id`` and, because the app requests user authorization during installation,
a ``code``. The web app posts both here. The code is exchanged for a user-to-server token
(stored encrypted), the user's installations are recorded, and a session cookie is set.
Sessions are stored by hash.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, select

from lantern_api.services import Services
from lantern_platform.crypto import hash_session_token, new_session_token
from lantern_platform.db import Installation, SessionRow, User, UserInstallation, utcnow
from lantern_platform.github import GitHubError
from lantern_platform.tokens import aware

COOKIE = "lantern_session"
router = APIRouter()


def services(request: Request) -> Services:
    svc: Services = request.app.state.services
    return svc


class CallbackBody(BaseModel):
    code: str
    installation_id: int | None = None
    setup_action: str | None = None


def upsert_installation(svc: Services, data: dict[str, Any]) -> None:
    account = data.get("account") or {}
    with svc.db.session() as s:
        row = s.get(Installation, int(data["id"]))
        if row is None:
            row = Installation(id=int(data["id"]), account_login=str(account.get("login", "")))
            s.add(row)
        row.account_login = str(account.get("login", row.account_login))
        row.account_type = str(account.get("type", row.account_type or "User"))
        row.repository_selection = str(
            data.get("repository_selection", row.repository_selection or "selected")
        )
        row.suspended = bool(data.get("suspended_at"))


def sync_user_installations(svc: Services, user_id: int, user_token: str) -> list[dict[str, Any]]:
    installations = svc.github.user_installations(user_token)
    for inst in installations:
        upsert_installation(svc, inst)
    with svc.db.session() as s:
        s.execute(delete(UserInstallation).where(UserInstallation.user_id == user_id))
        for inst in installations:
            s.add(UserInstallation(user_id=user_id, installation_id=int(inst["id"])))
    svc.repo_cache.pop(user_id, None)
    return installations


@router.post("/auth/github/callback")
def github_callback(
    body: CallbackBody, response: Response, svc: Services = Depends(services)
) -> dict[str, Any]:
    try:
        grant = svc.github.exchange_code(body.code)
        profile = svc.github.get_user(grant["access_token"])
    except GitHubError as exc:
        raise HTTPException(401, f"GitHub authorization failed: {exc}") from exc
    expires_in = grant.get("expires_in")
    with svc.db.session() as s:
        user = s.scalar(select(User).where(User.github_id == int(profile["id"])))
        if user is None:
            user = User(github_id=int(profile["id"]), login=str(profile["login"]))
            s.add(user)
        user.login = str(profile["login"])
        user.name = profile.get("name")
        user.avatar_url = profile.get("avatar_url")
        user.token_encrypted = svc.cipher.encrypt(grant["access_token"])
        user.token_expires_at = (
            utcnow() + dt.timedelta(seconds=int(expires_in)) if expires_in else None
        )
        s.flush()
        user_id = user.id
        token = new_session_token()
        s.add(
            SessionRow(
                token_hash=hash_session_token(token),
                user_id=user_id,
                expires_at=utcnow() + dt.timedelta(hours=svc.settings.session_ttl_hours),
            )
        )
    installations = sync_user_installations(svc, user_id, grant["access_token"])
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=svc.settings.cookie_secure,
        samesite="lax",
        max_age=svc.settings.session_ttl_hours * 3600,
    )
    return {
        "user": {
            "login": profile["login"],
            "name": profile.get("name"),
            "avatar_url": profile.get("avatar_url"),
        },
        "installations": [_installation_view(i) for i in installations],
    }


def _installation_view(inst: dict[str, Any]) -> dict[str, Any]:
    account = inst.get("account") or {}
    return {
        "id": inst["id"],
        "account": account.get("login"),
        "account_type": account.get("type"),
        "repository_selection": inst.get("repository_selection"),
        "suspended": bool(inst.get("suspended_at")),
    }


def current_user(request: Request, svc: Services = Depends(services)) -> User:
    token = request.cookies.get(COOKIE)
    header = request.headers.get("authorization", "")
    if not token and header.lower().startswith("bearer "):
        token = header[7:].strip()
    if not token:
        raise HTTPException(401, "not signed in")
    with svc.db.session() as s:
        session = s.get(SessionRow, hash_session_token(token))
        if session is None or aware(session.expires_at) < utcnow():
            raise HTTPException(401, "session expired")
        user = s.get(User, session.user_id)
        if user is None:
            raise HTTPException(401, "unknown user")
        s.expunge(user)
        return user


def user_token(svc: Services, user: User) -> str:
    if not user.token_encrypted:
        raise HTTPException(401, "GitHub authorization missing; install or sign in again")
    if user.token_expires_at is not None and aware(user.token_expires_at) < utcnow():
        raise HTTPException(401, "GitHub authorization expired; sign in again")
    return svc.cipher.decrypt(user.token_encrypted)


def user_installation_ids(svc: Services, user: User) -> set[int]:
    with svc.db.session() as s:
        return set(
            s.scalars(
                select(UserInstallation.installation_id).where(UserInstallation.user_id == user.id)
            )
        )


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"login": user.login, "name": user.name, "avatar_url": user.avatar_url}


@router.post("/auth/logout")
def logout(
    request: Request, response: Response, svc: Services = Depends(services)
) -> dict[str, bool]:
    token = request.cookies.get(COOKIE)
    if token:
        with svc.db.session() as s:
            s.execute(delete(SessionRow).where(SessionRow.token_hash == hash_session_token(token)))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/installations")
def installations(
    user: User = Depends(current_user), svc: Services = Depends(services)
) -> dict[str, Any]:
    items = sync_user_installations(svc, user.id, user_token(svc, user))
    return {
        "installations": [_installation_view(i) for i in items],
        "install_url": svc.github.install_url(),
    }
