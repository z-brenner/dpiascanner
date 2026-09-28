"""Fixtures for API and worker tests: an in-memory GitHub served through httpx.MockTransport,
an app wired to it with SQLite and an in-memory queue, and worker dependencies that fetch
the canary fixtures instead of cloning."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from lantern_api.main import create_app
from lantern_api.services import Services
from lantern_decisions.stub import StubProvider
from lantern_platform.config import DEFAULT_EGRESS, Settings
from lantern_platform.queue import InMemoryQueue
from lantern_worker.jobs import WorkerDeps

USER_TOKEN = "ghu_user_token_abcdef123456"
INSTALL_TOKEN = "ghs_install_token_abcdef123456"


@dataclass
class FakeGitHub:
    installation_id: int = 100
    user: dict[str, Any] = field(
        default_factory=lambda: {
            "id": 42,
            "login": "octo",
            "name": "Octo Cat",
            "avatar_url": "https://avatars.example/octo",
        }
    )
    repos: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "acme/canary-python": {
                "id": 1,
                "full_name": "acme/canary-python",
                "private": True,
                "default_branch": "main",
            },
            "acme/canary-typescript": {
                "id": 2,
                "full_name": "acme/canary-typescript",
                "private": True,
                "default_branch": "main",
            },
            "acme/secret-tool": {
                "id": 3,
                "full_name": "acme/secret-tool",
                "private": True,
                "default_branch": "main",
            },
            "oss/public-lib": {
                "id": 4,
                "full_name": "oss/public-lib",
                "private": False,
                "default_branch": "trunk",
            },
        }
    )
    installed: set[str] = field(
        default_factory=lambda: {"acme/canary-python", "acme/canary-typescript"}
    )
    user_can_see: set[str] = field(
        default_factory=lambda: {"acme/canary-python", "acme/canary-typescript", "acme/secret-tool"}
    )
    shas: dict[tuple[str, str], str] = field(default_factory=dict)
    pr_files: dict[tuple[str, int], list[str]] = field(default_factory=dict)
    comments: dict[int, dict[str, Any]] = field(default_factory=dict)
    check_runs: dict[int, dict[str, Any]] = field(default_factory=dict)
    token_requests: int = 0
    calls: list[tuple[str, str]] = field(default_factory=list)
    _next: int = 3_000_000_000  # comment ids are past 2**31 on github.com

    def next_id(self) -> int:
        self._next += 1
        return self._next

    def sha(self, repo: str, ref: str) -> str:
        return self.shas.setdefault((repo, ref), hashlib.sha1(f"{repo}@{ref}".encode()).hexdigest())

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # -------------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, f"{request.url.host}{path}"))
        auth = request.headers.get("authorization", "")
        body = (
            json.loads(request.content)
            if request.content
            and request.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        if request.url.host == "github.com" and path == "/login/oauth/access_token":
            if b"code=good" not in request.content:
                return httpx.Response(200, json={"error": "bad_verification_code"})
            return httpx.Response(
                200, json={"access_token": USER_TOKEN, "expires_in": 28800, "token_type": "bearer"}
            )
        if path == "/user":
            return self._user_only(auth) or httpx.Response(200, json=self.user)
        if path == "/user/installations":
            return self._user_only(auth) or httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "installations": [
                        {
                            "id": self.installation_id,
                            "account": {"login": "acme", "type": "Organization"},
                            "repository_selection": "selected",
                            "suspended_at": None,
                        }
                    ],
                },
            )
        m = re.fullmatch(r"/user/installations/(\d+)/repositories", path)
        if m:
            if request.url.params.get("page", "1") != "1":
                return httpx.Response(200, json={"repositories": []})
            repos = [self.repos[r] for r in sorted(self.installed & self.user_can_see)]
            return httpx.Response(200, json={"total_count": len(repos), "repositories": repos})
        m = re.fullmatch(r"/app/installations/(\d+)/access_tokens", path)
        if m and method == "POST":
            assert auth.startswith("Bearer ey"), "installation tokens are minted with the app JWT"
            self.token_requests += 1
            expires = (
                (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            )
            return httpx.Response(201, json={"token": INSTALL_TOKEN, "expires_at": expires})
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/installation", path)
        if m:
            return (
                httpx.Response(200, json={"id": self.installation_id})
                if m.group(1) in self.installed
                else httpx.Response(404, json={"message": "Not Found"})
            )
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/commits/(.+)", path)
        if m:
            if m.group(2) == "no-such-branch":
                return httpx.Response(422, json={"message": "No commit found for SHA"})
            return httpx.Response(200, text=self.sha(m.group(1), m.group(2)))
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/pulls/(\d+)/files", path)
        if m:
            if request.url.params.get("page", "1") != "1":
                return httpx.Response(200, json=[])
            files = self.pr_files.get((m.group(1), int(m.group(2))), [])
            return httpx.Response(200, json=[{"filename": f, "status": "modified"} for f in files])
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/check-runs", path)
        if m and method == "POST":
            check_id = self.next_id()
            self.check_runs[check_id] = {**body, "repo": m.group(1)}
            return httpx.Response(201, json={"id": check_id})
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/check-runs/(\d+)", path)
        if m and method == "PATCH":
            self.check_runs[int(m.group(2))].update(body)
            return httpx.Response(200, json={"id": int(m.group(2))})
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/issues/(\d+)/comments", path)
        if m and method == "GET":
            if request.url.params.get("page", "1") != "1":
                return httpx.Response(200, json=[])
            items = [
                c
                for c in self.comments.values()
                if c["repo"] == m.group(1) and c["pr"] == int(m.group(2))
            ]
            return httpx.Response(200, json=items)
        if m and method == "POST":
            comment_id = self.next_id()
            self.comments[comment_id] = {
                "id": comment_id,
                "body": body["body"],
                "repo": m.group(1),
                "pr": int(m.group(2)),
            }
            return httpx.Response(201, json=self.comments[comment_id])
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)/issues/comments/(\d+)", path)
        if m and method == "PATCH":
            comment = self.comments.get(int(m.group(2)))
            if comment is None:
                return httpx.Response(404, json={"message": "Not Found"})
            comment["body"] = body["body"]
            return httpx.Response(200, json=comment)
        m = re.fullmatch(r"/repos/([^/]+/[^/]+)", path)
        if m:
            repo = self.repos.get(m.group(1))
            visible = repo is not None and (
                not repo["private"] or (auth and m.group(1) in self.user_can_see)
            )
            return (
                httpx.Response(200, json=repo)
                if visible
                else httpx.Response(404, json={"message": "Not Found"})
            )
        return httpx.Response(404, json={"message": f"fake has no route for {method} {path}"})

    def _user_only(self, auth: str) -> httpx.Response | None:
        return (
            None
            if auth == f"Bearer {USER_TOKEN}"
            else httpx.Response(401, json={"message": "Bad credentials"})
        )


# --------------------------------------------------------------------------- fixtures


FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
PUBLIC_PEM = (
    _KEY.public_key()
    .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    .decode()
)
WEBHOOK_SECRET = "whsec_test_secret"


class MappedFetcher:
    """Serves each repository from a fixture directory instead of cloning."""

    def __init__(self, sources: dict[str, Path]) -> None:
        self.sources = sources
        self.fetched: list[tuple[str, str, int | None]] = []

    def fetch(self, full_name: str, sha: str, installation_id: int | None, dest: Path) -> None:
        self.fetched.append((full_name, sha, installation_id))
        shutil.copytree(
            self.sources[full_name],
            dest,
            ignore=shutil.ignore_patterns(
                ".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"
            ),
        )


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'lantern.db'}",
        github_app_id="12345",
        github_app_private_key=PRIVATE_PEM,
        github_webhook_secret=WEBHOOK_SECRET,
        github_client_id="Iv1.client",
        github_client_secret="client-secret",
        token_key=Fernet.generate_key().decode(),
        web_url="http://web.test",
        cookie_secure=False,
        egress_allowlist=DEFAULT_EGRESS,
    )


@pytest.fixture
def queue() -> InMemoryQueue:
    return InMemoryQueue()


@pytest.fixture
def svc(settings: Settings, fake: FakeGitHub, queue: InMemoryQueue) -> Services:
    return Services.build(settings, queue=queue, transport=fake.transport())


@pytest.fixture
def client(svc: Services) -> Iterator[TestClient]:
    with TestClient(create_app(svc)) as c:
        yield c


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    response = client.post(
        "/auth/github/callback",
        json={"code": "good", "installation_id": 100, "setup_action": "install"},
    )
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def fetcher() -> MappedFetcher:
    return MappedFetcher(
        {
            "acme/canary-python": FIXTURES / "canary-python",
            "acme/canary-typescript": FIXTURES / "canary-typescript",
            "oss/public-lib": FIXTURES / "clean-python",
        }
    )


@pytest.fixture
def deps(svc: Services, fetcher: MappedFetcher, tmp_path: Path) -> WorkerDeps:
    workdir = tmp_path / "work"
    workdir.mkdir()
    return WorkerDeps(
        db=svc.db,
        settings=svc.settings,
        fetcher=fetcher,
        provider_factory=StubProvider,
        github=svc.github,
        workdir_root=workdir,
        report_base_url="http://web.test",
    )
