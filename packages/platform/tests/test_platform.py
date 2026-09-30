from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import json
from typing import Any

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from lantern_platform.config import DEFAULT_EGRESS, Settings
from lantern_platform.crypto import TokenCipher, TokenCipherError, hash_session_token
from lantern_platform.egress import EgressDenied, EgressPolicy, guarded_client
from lantern_platform.github import GitHub, RepoRef, app_jwt, parse_repo, verify_webhook_signature
from lantern_platform.pr import MARKER, check_output, render_pr_comment, upsert_pr_comment
from lantern_platform.storage import scrub
from lantern_report.diff import FindingsDiff
from lantern_report.findings import Finding

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://github.com/o/r", ("o", "r")),
        ("github.com/o/r", ("o", "r")),
        ("o/r", ("o", "r")),
        ("git@github.com:o/r.git", ("o", "r")),
        ("https://github.com/Octo-Org/my.repo.git", ("Octo-Org", "my.repo")),
        ("http://www.github.com/o/r/pull/12", ("o", "r")),
        ("ssh://git@github.com/o/r.git", ("o", "r")),
        (" o/r ", ("o", "r")),
    ],
)
def test_parse_repo_forms(text: str, expected: tuple[str, str]) -> None:
    ref = parse_repo(text)
    assert (ref.owner, ref.name) == expected


@pytest.mark.parametrize(
    "text", ["", "o", "o/r/x", "https://gitlab.com/o/r", "-bad/r", "o/..", "git@github.com:o"]
)
def test_parse_repo_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_repo(text)


def test_webhook_signature() -> None:
    body = b'{"action":"opened"}'
    good = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature("s3cret", body, good)
    assert not verify_webhook_signature("s3cret", body + b"x", good)
    assert not verify_webhook_signature("other", body, good)
    assert not verify_webhook_signature("", body, good)  # no secret configured: trust nothing
    assert not verify_webhook_signature("s3cret", body, None)
    assert not verify_webhook_signature("s3cret", body, good.replace("sha256=", "sha1="))


def test_app_jwt_claims() -> None:
    token = app_jwt("12345", PEM, now=1_700_000_000)
    claims = jwt.decode(
        token, KEY.public_key(), algorithms=["RS256"], options={"verify_exp": False}
    )
    assert claims == {"iss": "12345", "iat": 1_700_000_000 - 60, "exp": 1_700_000_000 + 540}


def _settings(**kw: Any) -> Settings:
    return Settings(
        github_app_id="12345",
        github_app_private_key=PEM,
        token_key=Fernet.generate_key().decode(),
        **kw,
    )


def test_installation_tokens_are_cached_until_near_expiry() -> None:
    now = [1_700_000_000.0]
    minted: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Bearer ")
        minted.append(request.url.path)
        expires = dt.datetime.fromtimestamp(now[0] + 3600, dt.UTC).isoformat()
        return httpx.Response(201, json={"token": f"ghs_{len(minted)}", "expires_at": expires})

    gh = GitHub(_settings(), transport=httpx.MockTransport(handler), clock=lambda: now[0])
    assert gh.installation_token(7) == "ghs_1"
    now[0] += 3000  # 10 minutes left: still cached
    assert gh.installation_token(7) == "ghs_1"
    now[0] += 400  # under 5 minutes left: refresh
    assert gh.installation_token(7) == "ghs_2"
    assert minted == ["/app/installations/7/access_tokens"] * 2


def test_egress_policy() -> None:
    policy = EgressPolicy([*DEFAULT_EGRESS, "*.example.org"])
    assert policy.allows("api.github.com") and policy.allows("cdn.example.org")
    assert not policy.allows("evil.com") and not policy.allows("api.github.com.evil.com")
    with pytest.raises(EgressDenied):
        policy.check_url("https://attacker.example/steal")
    with pytest.raises(EgressDenied):
        policy.check_url("file:///etc/passwd")
    client = guarded_client(policy, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    assert client.get("https://api.github.com/zen").status_code == 200
    with pytest.raises(EgressDenied):
        client.get("https://exfil.example.net/")


def test_token_cipher() -> None:
    key = Fernet.generate_key().decode()
    cipher = TokenCipher(key)
    blob = cipher.encrypt("ghs_secret")
    assert "ghs_secret" not in blob and cipher.decrypt(blob) == "ghs_secret"
    with pytest.raises(TokenCipherError):
        TokenCipher(Fernet.generate_key().decode()).decrypt(blob)
    with pytest.raises(TokenCipherError):
        TokenCipher("")
    assert hash_session_token("abc") != "abc" and len(hash_session_token("abc")) == 64


def test_token_cipher_accepts_host_generated_secrets() -> None:
    # A real Fernet key is used as is, so existing deployments keep decrypting their tokens.
    key = Fernet.generate_key().decode()
    assert Fernet(key.encode()).decrypt(TokenCipher(key).encrypt("ghs_a").encode()) == b"ghs_a"
    # Render's generateValue (base64 of 256 bits) may come without padding; other hosts use hex.
    raw = bytes(range(32))
    for secret in (
        base64.b64encode(raw).decode().rstrip("="),
        raw.hex(),
        f"  {base64.b64encode(raw).decode()}\n",
    ):
        blob = TokenCipher(secret).encrypt("ghs_b")
        # The API and the worker build their ciphers separately and must agree.
        assert TokenCipher(secret).decrypt(blob) == "ghs_b"
    with pytest.raises(TokenCipherError, match="at least 32 characters"):
        TokenCipher("too-short-to-be-a-secret")


def test_scrub_blanks_code_but_keeps_facts() -> None:
    evidence = {
        "unresolved_steps": [
            {"file": "a.py", "line": 3, "text": "x = d[k]", "note": "non-literal key `k`"}
        ],
        "conflicting_config": [
            {"key": "region", "value": "eu-west-1", "file": "s.yaml", "line": 4}
        ],
    }
    scrubbed = scrub(evidence)
    assert scrubbed["unresolved_steps"][0] == {
        "file": "a.py",
        "line": 3,
        "text": "",
        "note": "non-literal key `k`",
    }
    assert scrubbed["conflicting_config"] == evidence["conflicting_config"]


class _Comments:
    def __init__(self) -> None:
        self.ids: dict[tuple[str, int], int] = {}

    def get(self, repo: str, pr: int) -> int | None:
        return self.ids.get((repo, pr))

    def set(self, repo: str, pr: int, comment_id: int) -> None:
        self.ids[(repo, pr)] = comment_id


def _comment_github(existing: list[dict[str, Any]]) -> tuple[GitHub, list[dict[str, Any]]]:
    comments = list(existing)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200, json=comments if request.url.params.get("page") == "1" else []
            )
        if request.method == "POST":
            comment = {
                "id": 5_000_000_000 + len(comments),
                "body": json.loads(request.content)["body"],
            }
            comments.append(comment)
            return httpx.Response(201, json=comment)
        comment_id = int(request.url.path.rsplit("/", 1)[1])
        match = [c for c in comments if c["id"] == comment_id]
        if not match:
            return httpx.Response(404, json={"message": "Not Found"})
        match[0]["body"] = json.loads(request.content)["body"]
        return httpx.Response(200, json=match[0])

    return GitHub(_settings(), transport=httpx.MockTransport(handler)), comments


def test_upsert_creates_then_updates_one_comment() -> None:
    gh, comments = _comment_github([])
    store = _Comments()
    repo = RepoRef("acme", "app")
    first = upsert_pr_comment(gh, repo, 3, "t", f"{MARKER}\none", store)
    second = upsert_pr_comment(gh, repo, 3, "t", f"{MARKER}\ntwo", store)
    assert first[1] == "created" and second == (first[0], "updated")
    assert [c["body"] for c in comments] == [f"{MARKER}\ntwo"]


def test_upsert_finds_an_unrecorded_comment_by_its_marker() -> None:
    gh, comments = _comment_github(
        [
            {"id": 11, "body": "LGTM"},
            {"id": 12, "body": f"{MARKER}\nold"},
        ]
    )
    store = _Comments()  # the id was never stored (worker crashed after creating it)
    comment_id, action = upsert_pr_comment(
        gh, RepoRef("acme", "app"), 3, "t", f"{MARKER}\nnew", store
    )
    assert (comment_id, action) == (12, "updated") and store.get("acme/app", 3) == 12
    assert len(comments) == 2 and comments[1]["body"].endswith("new")


def _finding(fid: str, severity: str) -> Finding:
    return Finding(
        id=fid,
        key=f"K-{fid}",
        category="personal_data_to_third_party",
        title="Personal data sent to a third party",
        severity=severity,
        severity_modifiers=[],
        status="resolved",
        anchor_id="N-1",
        node_ids=[],
        edge_ids=[],
        flow_ids=[],
        decision_ids=[],
        statute_refs=[],
        reachable=True,
        data_categories=["contact"],
        destination_class="analytics",
        classification={},
    )


def test_pr_comment_and_check_summarize_the_diff() -> None:
    diff = FindingsDiff(
        new=[_finding("F-0002", "high")], resolved=[_finding("F-0009", "low")], unchanged=4
    )
    body = render_pr_comment(
        diff, "a" * 40, "b" * 40, "https://web/runs/r1", {"findings": 5, "unresolved": 1}
    )
    assert body.startswith(MARKER)
    assert "**1 new**, **1 resolved**" in body and "[F-0002](https://web/runs/r1#f-0002)" in body
    out = check_output(diff, {"findings": 5, "unresolved": 1}, None)
    assert out["conclusion"] == "neutral" and out["output"]["title"] == "1 new finding"
    quiet = check_output(FindingsDiff(unchanged=5), {"findings": 5}, None)
    assert quiet["conclusion"] == "success" and quiet["output"]["title"] == "No new findings"


def test_redis_queue_socket_outlasts_its_blocking_pop(monkeypatch: pytest.MonkeyPatch) -> None:
    # redis-py 8 defaults socket_timeout to 5 s; a 5 s BRPOP on an empty queue then timed out
    # and killed the worker on its first idle wait.
    import redis

    from lantern_platform.queue import MAX_BLOCK_S, SOCKET_TIMEOUT_S, QueueError, RedisQueue

    queue = RedisQueue("redis://queue.invalid:6379/0")
    assert queue.client.connection_pool.connection_kwargs["socket_timeout"] == SOCKET_TIMEOUT_S
    waits: list[int] = []

    def brpop(keys: list[str], timeout: int) -> None:
        waits.append(timeout)
        return None

    monkeypatch.setattr(queue.client, "brpop", brpop)
    assert queue.dequeue(timeout=5) is None and queue.dequeue(timeout=600) is None
    assert waits == [5, MAX_BLOCK_S] and max(waits) < SOCKET_TIMEOUT_S

    def unreachable(keys: list[str], timeout: int) -> None:
        raise redis.exceptions.TimeoutError("Timeout reading from socket")

    monkeypatch.setattr(queue.client, "brpop", unreachable)
    with pytest.raises(QueueError):
        queue.dequeue()
