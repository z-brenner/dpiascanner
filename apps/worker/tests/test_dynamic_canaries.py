import base64
import gzip
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
import yaml

from lantern_worker.dynamic.canaries import CanarySet, body_field_names
from lantern_worker.dynamic.observed import load_capture, observe

ROOT = Path(__file__).resolve().parents[3]
EMAIL = "lantern.canary+7f3a@example.com"


@pytest.fixture(scope="module")
def canaries() -> CanarySet:
    return CanarySet.default()


def kinds(hits: set) -> set[tuple[str, str]]:
    return {(h.kind, h.encoding) for h in hits}


def test_packaged_canaries_match_the_fixture_canaries():
    fixture = yaml.safe_load((ROOT / "fixtures" / "canaries.yaml").read_text())
    assert CanarySet.default().values == {k: str(v) for k, v in fixture.items()}


def test_env_exposes_every_canary(canaries):
    env = canaries.env()
    assert env["LANTERN_CANARY_EMAIL"] == EMAIL
    assert env["LANTERN_CANARY_EMAIL_2"].startswith("lantern.referee")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (json.dumps({"email": EMAIL}), ("email", "plain")),
        (json.dumps({"email": EMAIL.upper()}), ("email", "case-insensitive")),
        (urlencode({"email": EMAIL}), ("email", "url")),
        (quote(EMAIL, safe=""), ("email", "url")),
        (hashlib.sha256(EMAIL.lower().encode()).hexdigest(), ("email", "sha256")),
        (hashlib.sha256(EMAIL.encode()).hexdigest().upper(), ("email", "sha256")),
        (hashlib.md5(EMAIL.encode(), usedforsecurity=False).hexdigest(), ("email", "md5")),
        ('{"phone": "12025550147"}', ("phone", "digits")),
        (hashlib.sha256(b"12025550147").hexdigest(), ("phone", "sha256")),
        ('{"ssn":"OTg3LTY1LTQzMjE="}', ("ssn", "base64")),
        ('{"e": "lantern.canary+7f3a\\u0040example.com"}', ("email", "json>plain")),
    ],
)
def test_detects_common_encodings(canaries, payload, expected):
    assert expected in kinds(canaries.scan(payload, "body"))


def test_detects_base64_payloads_inside_query_strings(canaries):
    # mixpanel-node: GET /track?data=<base64 JSON>
    data = base64.b64encode(json.dumps({"properties": {"email": EMAIL}}).encode()).decode()
    hits = canaries.scan(f"ip=0&data={data}", "query")
    assert ("email", "base64>plain") in kinds(hits)


def test_detects_gzip_compressed_bodies(canaries):
    body = gzip.compress(json.dumps({"user": {"email": EMAIL}}).encode())
    assert ("email", "gzip>plain") in kinds(canaries.scan(body, "body"))


def test_no_hits_on_clean_traffic(canaries):
    body = json.dumps({"sku": "WIDGET-1", "name": "Blue widget", "country_of_origin": "PT"})
    assert canaries.scan(body, "body") == set()
    assert canaries.scan(base64.b64encode(body.encode()), "body") == set()


def test_plain_hit_suppresses_implied_variants(canaries):
    hits = canaries.scan('{"phone": "+1-202-555-0147"}', "body")
    assert {h.encoding for h in hits if h.kind == "phone"} == {"plain"}


def test_body_field_names_json_ndjson_form_and_nested_base64():
    assert body_field_names(b'{"a": {"b": [{"c": 1}]}}', "application/json") == [
        "a",
        "a.b",
        "a.b[].c",
    ]
    envelope = b'{"event_id":"1"}\n{"type":"event"}\n{"request":{"data":{"email":"x"}}}\n'
    assert "request.data.email" in body_field_names(envelope, "application/x-sentry-envelope")
    data = base64.b64encode(json.dumps({"event": "Signed Up", "properties": {"email": 1}}).encode())
    names = body_field_names(b"data=" + data + b"&verbose=1", "application/x-www-form-urlencoded")
    assert names == ["data", "data:event", "data:properties", "data:properties.email", "verbose"]


def _record(**overrides):
    body = overrides.pop("body", b"")
    record = {
        "ts": 1.0,
        "method": "POST",
        "host": "api.stripe.com",
        "path": "/v1/customers",
        "query": "",
        "headers": {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "[REDACTED]",
            "X-Sentry-Auth": "Sentry sentry_key=abc123",
            "User-Agent": "Stripe/v1 PythonBindings/15.6.1",
        },
        "body_b64": base64.b64encode(body).decode(),
        "body_sha256": hashlib.sha256(body).hexdigest(),
    }
    record.update(overrides)
    return record


def test_observe_keeps_shape_not_content(canaries):
    body = urlencode({"email": EMAIL, "name": "Lantern Canary"}).encode()
    obs = observe(_record(body=body, query="token=sk_live_abcdefghij1234567890&x=1"), canaries)
    assert obs.host == "api.stripe.com"
    assert obs.body_field_names == ["email", "name"]
    assert obs.query_keys == ["token", "x"]
    assert obs.canary_kinds == ["email", "name"]
    assert obs.body_size == len(body)
    assert obs.headers["x-sentry-auth"] == "[REDACTED]"
    assert obs.headers["user-agent"].startswith("Stripe/")
    dumped = json.dumps(obs.to_dict())
    assert EMAIL not in dumped and "sk_live" not in dumped and "abc123" not in dumped


def test_observe_redacts_secrets_in_paths(canaries):
    obs = observe(_record(path="/hooks/sk_live_abcdefghij1234567890/x"), canaries)
    assert "sk_live" not in obs.path


def test_load_capture_deletes_the_raw_file(tmp_path, canaries):
    capture = tmp_path / "capture.jsonl"
    capture.write_text(json.dumps(_record(body=EMAIL.encode())) + "\n")
    observed = load_capture(capture, canaries)
    assert [o.canary_kinds for o in observed] == [["email"]]
    assert not capture.exists()
