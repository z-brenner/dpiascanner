"""Repository listing and resolution of pasted repository references."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from lantern_api.auth import current_user, services, user_installation_ids, user_token
from lantern_api.services import Services
from lantern_platform.db import Run, User
from lantern_platform.github import GitHubError, RepoRef, parse_repo

router = APIRouter()
REPO_CACHE_S = 60


def accessible_repos(svc: Services, user: User) -> list[dict[str, Any]]:
    """Repositories the user can see through the app's installations (cached briefly)."""
    cached = svc.repo_cache.get(user.id)
    if cached and svc.clock() - cached[0] < REPO_CACHE_S:
        return cached[1]
    token = user_token(svc, user)
    repos: list[dict[str, Any]] = []
    for installation_id in sorted(user_installation_ids(svc, user)):
        for repo in svc.github.user_installation_repos(token, installation_id):
            private = bool(repo.get("private"))
            repos.append(
                {
                    "id": repo.get("id"),
                    "full_name": repo["full_name"],
                    "private": private,
                    "scannable": not (private and svc.settings.public_repos_only),
                    "default_branch": repo.get("default_branch") or "main",
                    "installation_id": installation_id,
                    "updated_at": repo.get("pushed_at") or repo.get("updated_at"),
                }
            )
    repos.sort(key=lambda r: r["full_name"].lower())
    svc.repo_cache[user.id] = (svc.clock(), repos)
    return repos


@router.get("/repos")
def list_repos(
    q: str = "",
    page: int = Query(1, ge=1),
    per_page: int = Query(30, ge=1, le=100),
    user: User = Depends(current_user),
    svc: Services = Depends(services),
) -> dict[str, Any]:
    repos = accessible_repos(svc, user)
    needle = q.strip().lower()
    matched = [r for r in repos if needle in r["full_name"].lower()] if needle else repos
    start = (page - 1) * per_page
    return {
        "total": len(matched),
        "page": page,
        "per_page": per_page,
        "items": matched[start : start + per_page],
    }


@dataclass
class Resolution:
    input: str
    status: str  # installed | public | private_not_allowed | inaccessible | invalid
    full_name: str | None = None
    owner: str | None = None
    repo: str | None = None
    installation_id: int | None = None
    private: bool | None = None
    default_branch: str | None = None
    reason: str | None = None
    install_url: str | None = None

    @property
    def scannable(self) -> bool:
        return self.status in ("installed", "public")

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "scannable": self.scannable}


def resolve(svc: Services, user: User, text: str) -> Resolution:
    try:
        ref = parse_repo(text)
    except ValueError:
        return Resolution(
            text,
            "invalid",
            reason="Enter a GitHub URL (https://github.com/owner/repo), "
            "git@github.com:owner/repo.git, or owner/repo.",
        )
    for repo in accessible_repos(svc, user):
        if repo["full_name"].lower() == ref.full_name.lower():
            if not repo["scannable"]:
                return _private_not_allowed(text, ref, repo["full_name"], repo["default_branch"])
            return Resolution(
                text,
                "installed",
                full_name=repo["full_name"],
                owner=ref.owner,
                repo=ref.name,
                installation_id=repo["installation_id"],
                private=repo["private"],
                default_branch=repo["default_branch"],
            )
    install_url = svc.github.install_url()
    token = None
    try:
        token = user_token(svc, user)
    except HTTPException:
        token = None
    data = _get_repo(svc, ref, token)
    if data is not None and not data.get("private"):
        return Resolution(
            text,
            "public",
            full_name=ref.full_name,
            owner=ref.owner,
            repo=ref.name,
            private=False,
            default_branch=data.get("default_branch") or "main",
            reason="Public repository: it can be scanned without installing the app. Install the "
            "app on it to get pull request checks.",
            install_url=install_url,
        )
    if data is not None and svc.settings.public_repos_only:
        return _private_not_allowed(text, ref, ref.full_name, data.get("default_branch"))
    if data is not None:
        return Resolution(
            text,
            "inaccessible",
            full_name=ref.full_name,
            owner=ref.owner,
            repo=ref.name,
            private=True,
            default_branch=data.get("default_branch"),
            reason="Private repository that the Lantern app is not installed on. Add it to the "
            "app's installation to scan it.",
            install_url=install_url,
        )
    return Resolution(
        text,
        "inaccessible",
        full_name=ref.full_name,
        owner=ref.owner,
        repo=ref.name,
        reason="Repository not found, or private and not visible to your GitHub account.",
        install_url=install_url,
    )


def _private_not_allowed(
    text: str, ref: RepoRef, full_name: str, default_branch: str | None
) -> Resolution:
    return Resolution(
        text,
        "private_not_allowed",
        full_name=full_name,
        owner=ref.owner,
        repo=ref.name,
        private=True,
        default_branch=default_branch,
        reason="This instance scans public repositories only. Host Lantern yourself to scan "
        "private code.",
    )


def _get_repo(svc: Services, ref: RepoRef, token: str | None) -> dict[str, Any] | None:
    try:
        return svc.github.get_repo(ref, token)
    except GitHubError:
        return None


class ResolveBody(BaseModel):
    input: str


@router.post("/repos/resolve")
def resolve_repo(
    body: ResolveBody, user: User = Depends(current_user), svc: Services = Depends(services)
) -> dict[str, Any]:
    return resolve(svc, user, body.input).to_dict()


def can_read_repo(svc: Services, user: User, full_name: str) -> bool:
    return any(r["full_name"].lower() == full_name.lower() for r in accessible_repos(svc, user))


@router.get("/repos/{owner}/{repo}/runs")
def repo_runs(
    owner: str,
    repo: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(current_user),
    svc: Services = Depends(services),
) -> dict[str, Any]:
    from lantern_api.runs import run_summary, visible_run_filter

    full_name = f"{owner}/{repo}"
    with svc.db.session() as s:
        query = (
            select(Run)
            .where(Run.repo_full_name == full_name, visible_run_filter(svc, user))
            .order_by(Run.created_at.desc())
        )
        rows = list(s.scalars(query))
        items = [run_summary(r) for r in rows[(page - 1) * per_page : page * per_page]]
    return {"total": len(rows), "page": page, "per_page": per_page, "items": items}
