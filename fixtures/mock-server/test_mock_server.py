import base64
import gzip
import json
import urllib.request
from pathlib import Path

from mock_server import MockServer, split_host_prefix


def _post(url: str, body: bytes, headers: dict[str, str] | None = None) -> int:
    request = urllib.request.Request(url, data=body, headers=headers or {}, method="POST")
    with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310
        return int(response.status)


def test_split_host_prefix() -> None:
    assert split_host_prefix("/_host/api.stripe.com/v1/customers", "x") == (
        "api.stripe.com",
        "/v1/customers",
    )
    assert split_host_prefix("/v1/x", "localhost:1") == ("localhost:1", "/v1/x")


def test_records_prefix_port_and_gzip(tmp_path: Path) -> None:
    out = tmp_path / "requests.jsonl"
    with MockServer(out) as server:
        url = server.host_url("https://api.mixpanel.com/track?verbose=1")
        assert _post(url, b"data=abc") == 200
        port_url = server.host_url("https://api.stripe.com/v1/customers", rewrite="port")
        assert _post(port_url, b"email=a%40b.c") == 200
        dsn_url = server.host_url("https://pk@o0.ingest.sentry.io/api/0/envelope/")
        assert dsn_url.startswith("http://pk@127.0.0.1:")
        gz = gzip.compress(b'{"message":"hello"}')
        assert _post(dsn_url.replace("pk@", ""), gz, {"Content-Encoding": "gzip"}) == 200
    records = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["host"] for r in records] == [
        "api.mixpanel.com",
        "api.stripe.com",
        "o0.ingest.sentry.io",
    ]
    assert records[0]["path"] == "/track" and records[0]["query"] == "verbose=1"
    assert records[1]["path"] == "/v1/customers"
    assert base64.b64decode(records[2]["body_b64"]) == b'{"message":"hello"}'
