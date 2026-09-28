import json
import os
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RECEIVED: list[dict[str, Any]] = []


class _Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        RECEIVED.append({"method": self.command, "path": self.path, "body": body})
        payload = b""
        if self.command != "PUT":
            payload = json.dumps({"id": "cus_stub", "object": "customer", "status": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("ETag", '"stub"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = _handle

    def log_message(self, *args: Any) -> None:
        return


_server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
threading.Thread(target=_server.serve_forever, daemon=True).start()
_base = f"http://127.0.0.1:{_server.server_address[1]}"

os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{ROOT / 'test.db'}",
        "MIXPANEL_EVENTS_URL": f"{_base}/track",
        "SENTRY_DSN": f"http://pk@127.0.0.1:{_server.server_address[1]}/1",
        "STRIPE_API_KEY": "sk_test_stub",
        "STRIPE_API_BASE": _base,
        "WEATHER_API_URL": f"{_base}/forecast",
        "PARTNER_API_URL": f"{_base}/contacts",
        "S3_ENDPOINT_URL": _base,
        "AWS_ACCESS_KEY_ID": "stub",
        "AWS_SECRET_ACCESS_KEY": "stub",
    }
)


@pytest.fixture()
def client() -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.db import Base, engine
    from app.main import app

    Base.metadata.drop_all(engine)
    RECEIVED.clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def received() -> list[dict[str, Any]]:
    return RECEIVED
