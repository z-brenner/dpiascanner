"""GitHub App client: app JWT, installation tokens, webhook signatures, and the REST calls
the API and worker make. Every request goes through the egress policy.

Installation tokens are cached until five minutes before they expire, in memory and, when a
``TokenStore`` is given, encrypted in the database so other processes can reuse them.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
import jwt

from lantern_platform.config import Settings
from lantern_platform.egress import EgressPolicy, guarded_client

API_VERSION = "2022-11-28"
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"[A-Za-z0-9._-]{1,100}"
_FORMS = [
    re.compile(
        rf"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO}?)(?:\.git)?(?:[/?#].*)?$"
    ),
    re.compile(rf"^git@github\.com:(?P<owner>{_OWNER})/(?P<repo>{_REPO}?)(?:\.git)?/?$"),
    re.compile(rf"^ssh://git@github\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO}?)(?:\.git)?/?$"),
    re.compile(rf"^(?P<owner>{_OWNER})/(?P<repo>{_REPO}?)(?:\.git)?$"),
]


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


def parse_repo(text: str) -> RepoRef:
    """Normalize a pasted GitHub URL or owner/repo string."""
    value = text.strip()
    for form in _FORMS:
        m = form.match(value)
        if m and m.group("repo") not in (".", ".."):
            return RepoRef(m.group("owner"), m.group("repo"))
    raise ValueError(f"not a GitHub repository reference: {text!r}")


def app_jwt(app_id: str, private_key: str, now: float | None = None) -> str:
    """A GitHub App JWT: RS256, issued 60 s in the past for clock drift, valid 9 minutes."""
    issued = int(now if now is not None else time.time())
    payload = {"iat": issued - 60, "exp": issued + 9 * 60, "iss": str(app_id)}
    return jwt.encode(payload, private_key, algorithm="RS256")


def verify_webhook_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check X-Hub-Signature-256 in constant time. No secret configured means no delivery
    is trusted."""
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)


class TokenStore(Protocol):
    def load(self, installation_id: int) -> tuple[str, dt.datetime] | None: ...

    def save(self, installation_id: int, token: str, expires_at: dt.datetime) -> None: ...


class GitHub:
    def __init__(
        self,
        settings: Settings,
        policy: EgressPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        token_store: TokenStore | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.token_store = token_store
        self._tokens: dict[int, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self.http = guarded_client(
            policy or EgressPolicy(settings.egress_allowlist),
            base_url=settings.github_api_url,
            timeout=30.0,
            transport=transport,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "lantern-dpia",
            },
        )
        self.web = guarded_client(
            policy or EgressPolicy(settings.egress_allowlist),
            base_url=settings.github_web_url,
            timeout=30.0,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "lantern-dpia"},
        )

    # --- plumbing -------------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        token: str | None = None,
        bearer: bool = False,
        ok: tuple[int, ...] = (200, 201),
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        if token:
            headers["Authorization"] = f"{'Bearer' if bearer else 'token'} {token}"
        response = self.http.request(method, path, headers=headers, **kwargs)
        if response.status_code not in ok:
            message = ""
            try:
                message = str(response.json().get("message", ""))
            except ValueError:
                message = response.text[:200]
            raise GitHubError(response.status_code, message)
        return response

    def _paginate(
        self,
        path: str,
        token: str | None,
        key: str | None = None,
        bearer: bool = False,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            data = self._call(
                "GET", path, token, bearer, params={"per_page": 100, "page": page}
            ).json()
            batch = data[key] if key else data
            items.extend(batch)
            if len(batch) < 100:
                break
        return items

    # --- app authentication ---------------------------------------------------------------

    def app_token(self) -> str:
        if not self.settings.github_app_id or not self.settings.github_app_private_key:
            raise GitHubError(0, "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY are not configured")
        return app_jwt(
            self.settings.github_app_id, self.settings.github_app_private_key, self.clock()
        )

    def installation_token(self, installation_id: int) -> str:
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached and cached[1] - self.clock() > 300:
                return cached[0]
            if self.token_store is not None:
                stored = self.token_store.load(installation_id)
                if stored and stored[1].timestamp() - self.clock() > 300:
                    self._tokens[installation_id] = (stored[0], stored[1].timestamp())
                    return stored[0]
            data = self._call(
                "POST",
                f"/app/installations/{installation_id}/access_tokens",
                self.app_token(),
                bearer=True,
            ).json()
            expires = dt.datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00"))
            self._tokens[installation_id] = (data["token"], expires.timestamp())
            if self.token_store is not None:
                self.token_store.save(installation_id, data["token"], expires)
            return str(data["token"])

    # --- repositories ---------------------------------------------------------------------

    def repo_installation(self, repo: RepoRef) -> dict[str, Any] | None:
        try:
            return dict(
                self._call(
                    "GET", f"/repos/{repo.full_name}/installation", self.app_token(), bearer=True
                ).json()
            )
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise

    def get_repo(self, repo: RepoRef, token: str | None = None) -> dict[str, Any] | None:
        try:
            return dict(self._call("GET", f"/repos/{repo.full_name}", token).json())
        except GitHubError as exc:
            if exc.status in (404, 403, 401):
                return None
            raise

    def commit_sha(self, repo: RepoRef, ref: str, token: str | None = None) -> str:
        response = self._call(
            "GET",
            f"/repos/{repo.full_name}/commits/{ref}",
            token,
            headers={"Accept": "application/vnd.github.sha"},
        )
        return response.text.strip()

    def pull_files(self, repo: RepoRef, number: int, token: str) -> list[str]:
        files = self._paginate(f"/repos/{repo.full_name}/pulls/{number}/files", token, max_pages=30)
        out = []
        for f in files:
            out.append(str(f["filename"]))
            if f.get("previous_filename"):
                out.append(str(f["previous_filename"]))
        return out

    # --- checks and comments --------------------------------------------------------------

    def create_check_run(self, repo: RepoRef, token: str, **fields: Any) -> int:
        return int(
            self._call("POST", f"/repos/{repo.full_name}/check-runs", token, json=fields).json()[
                "id"
            ]
        )

    def update_check_run(self, repo: RepoRef, token: str, check_run_id: int, **fields: Any) -> None:
        self._call(
            "PATCH", f"/repos/{repo.full_name}/check-runs/{check_run_id}", token, json=fields
        )

    def list_issue_comments(self, repo: RepoRef, number: int, token: str) -> list[dict[str, Any]]:
        return self._paginate(f"/repos/{repo.full_name}/issues/{number}/comments", token)

    def create_issue_comment(
        self, repo: RepoRef, number: int, token: str, body: str
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "POST",
                f"/repos/{repo.full_name}/issues/{number}/comments",
                token,
                json={"body": body},
            ).json()
        )

    def update_issue_comment(
        self, repo: RepoRef, comment_id: int, token: str, body: str
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "PATCH",
                f"/repos/{repo.full_name}/issues/comments/{comment_id}",
                token,
                json={"body": body},
            ).json()
        )

    # --- user authorization (installation flow) -------------------------------------------

    def exchange_code(self, code: str) -> dict[str, Any]:
        response = self.web.post(
            "/login/oauth/access_token",
            data={
                "client_id": self.settings.github_client_id,
                "client_secret": self.settings.github_client_secret,
                "code": code,
            },
        )
        data = response.json() if response.status_code == 200 else {}
        if "access_token" not in data:
            raise GitHubError(
                response.status_code,
                str(data.get("error_description") or data.get("error") or "code exchange failed"),
            )
        return dict(data)

    def get_user(self, user_token: str) -> dict[str, Any]:
        return dict(self._call("GET", "/user", user_token, bearer=True).json())

    def user_installations(self, user_token: str) -> list[dict[str, Any]]:
        return self._paginate("/user/installations", user_token, key="installations", bearer=True)

    def user_installation_repos(
        self, user_token: str, installation_id: int
    ) -> list[dict[str, Any]]:
        return self._paginate(
            f"/user/installations/{installation_id}/repositories",
            user_token,
            key="repositories",
            bearer=True,
        )

    def install_url(self) -> str:
        return (
            f"{self.settings.github_web_url}/apps/{self.settings.github_app_slug}/installations/new"
        )

    def signin_url(self) -> str | None:
        """GitHub's user authorization page for someone who installed the app before.

        The callback is the one installation uses, so it must be among the app's callback
        URLs. None until the app's client id is configured.
        """
        if not self.settings.github_client_id:
            return None
        query = urlencode(
            {
                "client_id": self.settings.github_client_id,
                "redirect_uri": f"{self.settings.web_url.rstrip('/')}/auth/github/callback",
            }
        )
        return f"{self.settings.github_web_url}/login/oauth/authorize?{query}"
