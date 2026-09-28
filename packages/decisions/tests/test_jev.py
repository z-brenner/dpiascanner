import logging

import httpx
import pytest

from lantern_decisions.cache import InMemoryDecisionCache, SQLiteDecisionCache
from lantern_decisions.jev import JevConfig, JevConfigError, JevProvider, JevRequestError, adapter
from lantern_decisions.question_sets import TargetType, load_bundle
from lantern_decisions.ratelimit import TokenBucket
from lantern_decisions.testing import FakeJev, make_request

CONFIG = JevConfig(api_key="test-key", base_url="https://jev.test", backoff_base_s=0.01)


def _provider(handler: FakeJev, cache=None, sleep=None, **kw):  # type: ignore[no-untyped-def]
    return JevProvider(
        CONFIG,
        cache=cache,
        transport=httpx.MockTransport(handler),
        sleep=sleep or (lambda s: None),
        rate_limiter=TokenBucket(1e9, burst=10**9),
        **kw,
    )


def test_config_requires_key_and_url() -> None:
    with pytest.raises(JevConfigError):
        JevConfig.from_env({})
    cfg = JevConfig.from_env({"TYPESAFE_API_KEY": "sekrit-123", "TYPESAFE_BASE_URL": "https://x/"})
    assert cfg.base_url == "https://x"
    assert "sekrit-123" not in repr(cfg)


def test_rerun_on_unchanged_state_makes_zero_calls(fake_jev: FakeJev) -> None:
    cache = InMemoryDecisionCache()
    requests = [
        make_request(TargetType.SOURCE, target_id=f"s{i}", field=f"f{i}") for i in range(40)
    ]
    first = _provider(fake_jev, cache).decide(requests)
    calls_after_first = fake_jev.calls
    assert calls_after_first == 2  # 40 items at 32 per request
    rerun = _provider(fake_jev, cache)
    second = rerun.decide(requests)
    assert fake_jev.calls == calls_after_first
    assert rerun.api_calls == 0
    assert all(r.cached for r in second)
    assert [r.answer for r in first] == [r.answer for r in second]


def test_sqlite_cache_survives_restart(tmp_path, fake_jev: FakeJev) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "decisions.sqlite"
    requests = [make_request(TargetType.SINK, target_id="k1")]
    _provider(fake_jev, SQLiteDecisionCache(path)).decide(requests)
    again = _provider(fake_jev, SQLiteDecisionCache(path)).decide(requests)
    assert fake_jev.calls == 1
    assert all(r.cached for r in again)


def test_duplicate_states_are_sent_once(fake_jev: FakeJev) -> None:
    same = [make_request(TargetType.EDGE, target_id=f"e{i}") for i in range(5)]
    results = _provider(fake_jev, InMemoryDecisionCache()).decide(same)
    assert fake_jev.items_seen == 1
    assert {r.target_id for r in results} == {f"e{i}" for i in range(5)}


def test_retries_rate_limited_requests_then_succeeds() -> None:
    handler = FakeJev(fail_first=2, status=429, retry_after="3")
    slept: list[float] = []
    results = _provider(handler, sleep=slept.append).decide([make_request(TargetType.SOURCE)])
    assert handler.calls == 3
    assert slept == [3.0, 3.0]
    assert results[0].provider_version == "jev-test-1"


def test_exponential_backoff_without_retry_after() -> None:
    handler = FakeJev(fail_first=3, status=503, retry_after=None)
    slept: list[float] = []
    _provider(handler, sleep=slept.append).decide([make_request(TargetType.SOURCE)])
    assert len(slept) == 3
    assert slept[0] < slept[1] < slept[2]


def test_gives_up_after_max_attempts() -> None:
    handler = FakeJev(fail_first=99, status=500, retry_after=None)
    with pytest.raises(JevRequestError, match="after 5 attempts"):
        _provider(handler).decide([make_request(TargetType.SOURCE)])
    assert handler.calls == 5


def test_client_errors_are_not_retried() -> None:
    handler = FakeJev(fail_first=99, status=400, retry_after=None)
    with pytest.raises(JevRequestError, match="400"):
        _provider(handler).decide([make_request(TargetType.SOURCE)])
    assert handler.calls == 1


def test_logs_never_contain_state(fake_jev: FakeJev, caplog: pytest.LogCaptureFixture) -> None:
    secret_snippet = "customer_ssn = '987-65-4321'"
    caplog.set_level(logging.DEBUG, logger="lantern.decisions.jev")
    _provider(fake_jev).decide([make_request(TargetType.SOURCE, snippet=secret_snippet)])
    assert "redacted" in caplog.text
    assert "987-65-4321" not in caplog.text
    assert "test-key" not in caplog.text


def test_payload_guard_blocks_before_sending(fake_jev: FakeJev) -> None:
    def guard(state):  # type: ignore[no-untyped-def]
        if "AKIA" in state.get("snippet", ""):
            raise PermissionError("secret in state")

    provider = _provider(fake_jev, payload_guard=guard)
    with pytest.raises(PermissionError):
        provider.decide([make_request(TargetType.SINK, snippet="key = 'AKIAIOSFODNN7EXAMPLE'")])
    assert fake_jev.calls == 0


def test_adapter_encodes_typed_questions() -> None:
    qs = load_bundle("v1").for_target("source")
    body = adapter.encode_batch([adapter.JevItem("i1", {"schema": "x"}, qs)])
    types = [q["type"] for q in body["items"][0]["questions"]]
    assert types == ["choice", "yes_no", "yes_no", "score"]
    score = body["items"][0]["questions"][3]
    assert (score["min"], score["max"]) == (1, 5) and set(score["anchors"]) == {
        "1",
        "2",
        "3",
        "4",
        "5",
    }


def test_adapter_rejects_malformed_responses() -> None:
    qs = load_bundle("v1").for_target("sink")
    items = [adapter.JevItem("i1", {}, qs)]
    with pytest.raises(adapter.JevResponseError):
        adapter.decode_batch({"results": [{"id": "other", "answers": []}]}, items)
    with pytest.raises(adapter.JevResponseError):
        adapter.decode_batch({"results": []}, items)


def test_token_bucket_paces_requests() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now[0] += s

    bucket = TokenBucket(2.0, burst=2, clock=lambda: now[0], sleep=sleep)
    for _ in range(6):
        bucket.acquire()
    assert pytest.approx(sum(slept), rel=1e-6) == 2.0
