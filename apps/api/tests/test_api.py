"""API, webhooks, and worker jobs against a fake GitHub (Prompt 8 acceptance tests)."""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from lantern_api.services import Services
from lantern_platform.db import FindingRow, ReportRow, Run, User
from lantern_platform.egress import EgressPolicy
from lantern_platform.pr import MARKER
from lantern_worker import __main__ as worker_main
from lantern_worker.fetch import FetchError, GitFetcher
from lantern_worker.jobs import WorkerDeps, process_run

WEBHOOK_SECRET = "whsec_test_secret"
USER_TOKEN = "ghu_user_token_abcdef123456"


def sign(body: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(
    client: TestClient, event: str, payload: dict[str, Any], delivery: str | None = None
) -> Any:
    body = json.dumps(payload).encode()
    return client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-github-event": event,
            "x-github-delivery": delivery or f"d-{time.time_ns()}",
            "x-hub-signature-256": sign(body),
            "content-type": "application/json",
        },
    )


# --------------------------------------------------------------------------- webhooks


def test_webhook_signature_is_required(client: TestClient) -> None:
    body = b'{"zen": "Keep it logically awesome."}'
    headers = {"x-github-event": "ping", "content-type": "application/json"}
    assert client.post("/webhooks/github", content=body, headers=headers).status_code == 401
    bad = {**headers, "x-hub-signature-256": sign(body, "wrong-secret")}
    assert client.post("/webhooks/github", content=body, headers=bad).status_code == 401
    sha1 = {**headers, "x-hub-signature-256": "sha1=" + hashlib.sha1(body).hexdigest()}
    assert client.post("/webhooks/github", content=body, headers=sha1).status_code == 401
    tampered = {**headers, "x-hub-signature-256": sign(body)}
    assert client.post("/webhooks/github", content=body + b" ", headers=tampered).status_code == 401
    ok = client.post(
        "/webhooks/github", content=body, headers={**headers, "x-hub-signature-256": sign(body)}
    )
    assert ok.status_code == 200 and ok.json() == {"ok": True}


def test_webhook_deliveries_are_idempotent(client: TestClient, queue: Any) -> None:
    payload = push_payload("acme/canary-python", "a" * 40)
    first = deliver(client, "push", payload, delivery="same-delivery")
    second = deliver(client, "push", payload, delivery="same-delivery")
    assert first.json()["run_id"] and second.json() == {"ok": True, "duplicate": True}
    assert len(queue) == 1


def test_push_to_other_branches_is_ignored(client: TestClient, queue: Any) -> None:
    payload = push_payload("acme/canary-python", "b" * 40, ref="refs/heads/feature")
    assert "ignored" in deliver(client, "push", payload).json()
    assert len(queue) == 0


# --------------------------------------------------------------------------- auth


def test_installation_callback_signs_in_and_encrypts_the_token(
    client: TestClient, svc: Services
) -> None:
    denied = client.post("/auth/github/callback", json={"code": "bad"})
    assert denied.status_code == 401
    response = client.post("/auth/github/callback", json={"code": "good", "installation_id": 100})
    assert response.status_code == 200
    assert response.json()["installations"][0]["account"] == "acme"
    assert "lantern_session" in response.cookies or client.cookies.get("lantern_session")
    assert client.get("/auth/me").json()["login"] == "octo"
    with svc.db.session() as s:
        user = s.scalar(select(User))
        assert user is not None and user.token_encrypted
        assert USER_TOKEN not in user.token_encrypted
        raw = " ".join(str(v) for v in s.execute(text("SELECT * FROM users")).one())
        sessions = " ".join(str(v) for v in s.execute(text("SELECT * FROM sessions")).one())
    assert USER_TOKEN not in raw
    assert client.cookies.get("lantern_session") not in sessions  # stored by hash
    assert svc.cipher.decrypt(user.token_encrypted) == USER_TOKEN


def test_config_is_public_and_settings_need_a_session(client: TestClient) -> None:
    assert (
        client.get("/config").json()["install_url"].endswith("/apps/lantern-dpia/installations/new")
    )
    assert client.get("/settings").status_code == 401
    client.post("/auth/github/callback", json={"code": "good"})
    settings = client.get("/settings").json()
    assert settings["threshold"] == 0.75 and settings["question_set_version"] == "v1"
    assert settings["registry_version"] and settings["severity_version"] == "severity-v2"


def test_endpoints_require_a_session(client: TestClient) -> None:
    for path in ("/repos", "/runs/run-x", "/installations"):
        assert client.get(path).status_code == 401


# --------------------------------------------------------------------------- repos


def test_repos_are_searched_server_side(signed_in: TestClient) -> None:
    everything = signed_in.get("/repos").json()
    assert [r["full_name"] for r in everything["items"]] == [
        "acme/canary-python",
        "acme/canary-typescript",
    ]
    found = signed_in.get("/repos", params={"q": "TYPESCRIPT"}).json()
    assert found["total"] == 1 and found["items"][0]["installation_id"] == 100
    paged = signed_in.get("/repos", params={"per_page": 1, "page": 2}).json()
    assert [r["full_name"] for r in paged["items"]] == ["acme/canary-typescript"]


@pytest.mark.parametrize(
    "text_input",
    [
        "https://github.com/acme/canary-python",
        "github.com/acme/canary-python",
        "acme/canary-python",
        "git@github.com:acme/canary-python.git",
        "https://github.com/acme/canary-python.git",
        "https://www.github.com/acme/canary-python/tree/main/app",
        "  acme/canary-python/  ".strip().rstrip("/"),
    ],
)
def test_resolve_accepts_every_url_form(signed_in: TestClient, text_input: str) -> None:
    result = signed_in.post("/repos/resolve", json={"input": text_input}).json()
    assert result["status"] == "installed", result
    assert result["full_name"] == "acme/canary-python"
    assert result["installation_id"] == 100 and result["scannable"] is True


def test_resolve_public_inaccessible_and_invalid(signed_in: TestClient) -> None:
    public = signed_in.post(
        "/repos/resolve", json={"input": "https://github.com/oss/public-lib"}
    ).json()
    assert (
        public["status"] == "public" and public["scannable"] and public["default_branch"] == "trunk"
    )
    private = signed_in.post("/repos/resolve", json={"input": "acme/secret-tool"}).json()
    assert private["status"] == "inaccessible" and not private["scannable"]
    assert private["install_url"] == "https://github.com/apps/lantern-dpia/installations/new"
    assert "not installed" in private["reason"]
    missing = signed_in.post("/repos/resolve", json={"input": "nobody/nothing"}).json()
    assert missing["status"] == "inaccessible" and "not found" in missing["reason"]
    for junk in ("https://gitlab.com/acme/x", "just-a-name", "a/b/c", "git@github.com:acme"):
        assert (
            signed_in.post("/repos/resolve", json={"input": junk}).json()["status"] == "invalid"
        ), junk


# --------------------------------------------------------------------------- run lifecycle


def test_run_lifecycle_with_the_stub_provider(
    signed_in: TestClient, svc: Services, queue: Any, deps: WorkerDeps, fake: Any
) -> None:
    created = signed_in.post(
        "/runs", json={"owner": "acme", "repo": "canary-python", "options": {"dependency_depth": 0}}
    )
    assert created.status_code == 201, created.text
    run = created.json()
    assert (
        run["status"] == "queued"
        and run["ref"] == "main"
        and run["sha"] == fake.sha("acme/canary-python", "main")
    )
    assert queue.dequeue(0) == run["id"]

    assert process_run(run["id"], deps) == "completed"
    detail = signed_in.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "completed" and detail["error"] is None
    stages = {s["stage"]: s["status"] for s in detail["stages"]}
    assert stages == {
        "clone": "done",
        "parse": "done",
        "graph": "done",
        "registry": "done",
        "classify": "done",
        "verify": "skipped",
        "report": "done",
    }
    assert detail["coverage"]["tainted_paths"] == 19
    assert deps.fetcher.fetched == [("acme/canary-python", run["sha"], 100)]  # type: ignore[attr-defined]
    assert list(deps.workdir_root.iterdir()) == []  # type: ignore[union-attr]  # clone deleted

    findings = signed_in.get(f"/runs/{run['id']}/findings").json()
    assert findings["total"] == 17
    critical = signed_in.get(f"/runs/{run['id']}/findings", params={"severity": "critical"}).json()
    assert critical["total"] == 3 and all(f["severity"] == "critical" for f in critical["items"])
    unresolved = signed_in.get(
        f"/runs/{run['id']}/findings", params={"status": "unresolved"}
    ).json()
    assert {f["category"] for f in unresolved["items"]} == {
        "unresolved_flow",
        "sale_or_share_candidate",
    }
    both = signed_in.get(
        f"/runs/{run['id']}/findings",
        params=[("category", "indefinite_retention"), ("severity", "high")],
    ).json()
    assert both["total"] == 1

    c08 = next(f for f in unresolved["items"] if f["category"] == "unresolved_flow")
    drawer = signed_in.get(f"/runs/{run['id']}/findings/{c08['id']}").json()
    sink = next(n for n in drawer["nodes"] if n["kind"] == "sink")
    assert sink["file"] == "app/partners.py" and "httpx.post" in sink["snippet"]
    assert (
        sink["url"]
        == f"https://github.com/acme/canary-python/blob/{run['sha']}/app/partners.py#L11"
    )
    assert drawer["decisions"] and all(0 <= d["probability"] <= 1 for d in drawer["decisions"])
    for step in (st for e in drawer["edges"] for st in e["steps"]):
        assert step["text"] == ""  # only node snippets are kept from the repository

    for fmt, ctype in (
        ("json", "application/json"),
        ("md", "text/markdown"),
        ("html", "text/html"),
        ("docx", "application/vnd.openxmlformats"),
    ):
        report = signed_in.get(f"/runs/{run['id']}/report", params={"format": fmt})
        assert report.status_code == 200 and report.headers["content-type"].startswith(ctype), fmt
    assert (
        "attachment"
        in signed_in.get(f"/runs/{run['id']}/report", params={"format": "docx"}).headers[
            "content-disposition"
        ]
    )
    report_json = signed_in.get(f"/runs/{run['id']}/report", params={"format": "json"}).json()
    assert report_json["schema"] == "lantern.report/v1"

    listed = signed_in.get("/repos/acme/canary-python/runs").json()
    assert [r["id"] for r in listed["items"]] == [run["id"]]

    again = signed_in.post("/runs", json={"owner": "acme", "repo": "canary-python"}).json()
    assert process_run(again["id"], deps) == "completed"
    diff = signed_in.get(f"/runs/{run['id']}/diff/{again['id']}").json()
    assert diff["new"] == [] and diff["resolved"] == [] and diff["unchanged"] == 17


def test_public_repositories_run_without_an_installation(
    signed_in: TestClient, deps: WorkerDeps
) -> None:
    run = signed_in.post("/runs", json={"owner": "oss", "repo": "public-lib"}).json()
    assert run["ref"] == "trunk"
    assert process_run(run["id"], deps) == "completed"
    assert deps.fetcher.fetched[-1][2] is None  # type: ignore[attr-defined]  # no token for public clones
    assert signed_in.get(f"/runs/{run['id']}/findings").json()["total"] == 0


def test_inaccessible_repositories_and_unknown_refs_are_refused(signed_in: TestClient) -> None:
    assert signed_in.post("/runs", json={"owner": "acme", "repo": "secret-tool"}).status_code == 403
    bad_ref = signed_in.post(
        "/runs", json={"owner": "acme", "repo": "canary-python", "ref": "no-such-branch"}
    )
    assert bad_ref.status_code == 422
    bad_option = signed_in.post(
        "/runs",
        json={"owner": "acme", "repo": "canary-python", "options": {"languages": ["cobol"]}},
    )
    assert bad_option.status_code == 422


def test_run_creation_is_rate_limited(signed_in: TestClient, svc: Services) -> None:
    object.__setattr__(svc.settings, "runs_per_hour", 2)
    for _ in range(2):
        assert (
            signed_in.post("/runs", json={"owner": "acme", "repo": "canary-python"}).status_code
            == 201
        )
    limited = signed_in.post("/runs", json={"owner": "acme", "repo": "canary-python"})
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0


def test_failed_runs_record_the_stage_and_keep_no_partial_results(
    signed_in: TestClient, svc: Services, deps: WorkerDeps
) -> None:
    class Broken:
        def fetch(self, *args: Any) -> None:
            raise FetchError("git fetch failed: repository unavailable")

    deps.fetcher = Broken()
    run = signed_in.post("/runs", json={"owner": "acme", "repo": "canary-python"}).json()
    assert process_run(run["id"], deps) == "failed"
    detail = signed_in.get(f"/runs/{run['id']}").json()
    assert detail["status"] == "failed" and detail["error"]["stage"] == "clone"
    assert "repository unavailable" in detail["error"]["message"]
    assert {s["stage"]: s["status"] for s in detail["stages"]}["clone"] == "failed"
    with svc.db.session() as s:
        assert s.scalar(select(FindingRow).where(FindingRow.run_id == run["id"])) is None
        assert s.scalar(select(ReportRow).where(ReportRow.run_id == run["id"])) is None
    assert list(deps.workdir_root.iterdir()) == []  # type: ignore[union-attr]


def test_cancelled_runs_are_skipped(signed_in: TestClient, deps: WorkerDeps) -> None:
    run = signed_in.post("/runs", json={"owner": "acme", "repo": "canary-python"}).json()
    assert signed_in.post(f"/runs/{run['id']}/cancel").json()["status"] == "cancelled"
    assert process_run(run["id"], deps) == "cancelled"


def test_runs_are_private_to_their_installation(
    signed_in: TestClient, svc: Services, fake: Any
) -> None:
    run = signed_in.post("/runs", json={"owner": "acme", "repo": "canary-python"}).json()
    other = TestClient(signed_in.app)
    fake.user = {"id": 7, "login": "mallory"}
    fake.installation_id = 999
    assert other.post("/auth/github/callback", json={"code": "good"}).status_code == 200
    assert other.get(f"/runs/{run['id']}").status_code == 404
    assert other.get(f"/runs/{run['id']}/findings").status_code == 404


def test_supervisor_times_out_runaway_jobs(svc: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    with svc.db.session() as s:
        s.add(
            Run(
                id="run-slow",
                repo_full_name="acme/canary-python",
                ref="main",
                sha="c" * 40,
                status="running",
                current_stage="classify",
            )
        )
    monkeypatch.setattr(worker_main, "job_command", lambda run_id, settings: ["sleep", "30"])
    object.__setattr__(svc.settings, "run_timeout_s", 1)
    started = time.monotonic()
    assert worker_main.supervise("run-slow", svc.settings, svc.db) == -1
    assert time.monotonic() - started < 10
    with svc.db.session() as s:
        run = s.get(Run, "run-slow")
        assert run is not None and run.status == "failed" and run.error_stage == "classify"
        assert "timed out" in (run.error_message or "")


def test_container_isolation_never_puts_secrets_in_argv(
    svc: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANTERN_TOKEN_KEY", "super-secret-key")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----")
    object.__setattr__(svc.settings, "job_isolation", "container")
    argv = worker_main.job_command("run-1", svc.settings)
    assert "--env" in argv and "LANTERN_TOKEN_KEY" in argv
    assert not any("super-secret" in a or "BEGIN PRIVATE" in a for a in argv)
    assert "--read-only" in argv and "--cap-drop" in argv


# --------------------------------------------------------------------------- pull requests


def push_payload(repo: str, sha: str, ref: str = "refs/heads/main") -> dict[str, Any]:
    return {
        "ref": ref,
        "after": sha,
        "deleted": False,
        "repository": {"full_name": repo, "default_branch": "main", "private": True},
        "installation": {"id": 100},
    }


def pr_payload(repo: str, number: int, head: str, base: str, action: str) -> dict[str, Any]:
    return {
        "action": action,
        "number": number,
        "pull_request": {
            "number": number,
            "head": {"sha": head, "ref": "feature"},
            "base": {"sha": base, "ref": "main"},
        },
        "repository": {"full_name": repo, "default_branch": "main", "private": True},
        "installation": {"id": 100},
    }


def test_pull_requests_get_incremental_runs_a_check_and_one_comment(
    client: TestClient, svc: Services, queue: Any, deps: WorkerDeps, fake: Any
) -> None:
    base_sha = "1" * 40
    push = deliver(client, "push", push_payload("acme/canary-python", base_sha)).json()
    assert process_run(queue.dequeue(0), deps) == "completed"

    fake.pr_files[("acme/canary-python", 7)] = ["app/partners.py"]
    opened = deliver(
        client, "pull_request", pr_payload("acme/canary-python", 7, "2" * 40, base_sha, "opened")
    ).json()
    assert opened["mode"] == "incremental"
    run_id = queue.dequeue(0)
    with svc.db.session() as s:
        run = s.get(Run, run_id)
        assert (
            run is not None
            and run.base_run_id == push["run_id"]
            and run.changed_files == ["app/partners.py"]
        )
        check_id = run.options["check_run_id"]
    assert fake.check_runs[check_id]["status"] == "queued"
    assert process_run(run_id, deps) == "completed"

    with svc.db.session() as s:
        summary = s.get(Run, run_id).summary  # type: ignore[union-attr]
    assert summary["classification"]["reused_targets"] > 0
    assert summary["classification"]["classified_targets"] > 0
    assert fake.check_runs[check_id]["status"] == "completed"
    assert fake.check_runs[check_id]["conclusion"] in ("success", "neutral")
    [comment] = fake.comments.values()
    assert MARKER in comment["body"] and "0 new" in comment["body"]
    comment_id = comment["id"]

    deliver(
        client,
        "pull_request",
        pr_payload("acme/canary-python", 7, "3" * 40, base_sha, "synchronize"),
    )
    assert process_run(queue.dequeue(0), deps) == "completed"
    assert list(fake.comments) == [comment_id]  # updated, not duplicated
    assert fake.comments[comment_id]["body"].count(MARKER) == 1

    # A comment deleted by someone is recreated once, not duplicated on the next push.
    fake.comments.clear()
    deliver(
        client,
        "pull_request",
        pr_payload("acme/canary-python", 7, "4" * 40, base_sha, "synchronize"),
    )
    assert process_run(queue.dequeue(0), deps) == "completed"
    assert len(fake.comments) == 1
    assert fake.token_requests == 1  # the installation token is cached until near expiry


# --------------------------------------------------------------------------- git clone


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_git_fetcher_shallow_clones_exactly_the_sha(tmp_path: Path) -> None:
    source = tmp_path / "origin"
    source.mkdir()
    (source / "app.py").write_text("print('v1')\n")
    _git("init", "-q", "-b", "main", cwd=source)
    _git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
        cwd=source,
    )
    _git("add", ".", cwd=source)
    _git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "v1",
        cwd=source,
    )
    first = _git("rev-parse", "HEAD", cwd=source)
    (source / "app.py").write_text("print('v2')\n")
    _git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-am",
        "v2",
        cwd=source,
    )
    _git("config", "uploadpack.allowReachableSHA1InWant", "true", cwd=source)

    class LocalGit(GitFetcher):
        def clone_url(self, full_name: str) -> str:
            return source.as_uri()

    fetcher = LocalGit(EgressPolicy(["file"]), max_mb=5)
    dest = tmp_path / "work" / "repo"
    fetcher.fetch("acme/app", first, None, dest)
    assert (dest / "app.py").read_text() == "print('v1')\n"
    assert not (dest / ".git").exists()

    denied = GitFetcher(EgressPolicy(["github.com"]), web_url="https://evil.example")
    with pytest.raises(Exception, match="allowlist"):
        denied.fetch("acme/app", first, None, tmp_path / "other")

    (source / "big.bin").write_bytes(b"\0" * (2 * 1024 * 1024))
    _git("add", ".", cwd=source)
    _git(
        "-c",
        "user.email=t@example.com",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "-m",
        "big",
        cwd=source,
    )
    big = _git("rev-parse", "HEAD", cwd=source)
    capped = LocalGit(EgressPolicy(["file"]), max_mb=1)
    with pytest.raises(FetchError, match="cap"):
        capped.fetch("acme/app", big, None, tmp_path / "capped" / "repo")
