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


class _Stub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        payload = json.dumps({"rates": {"EUR": 0.5}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = _handle

    def log_message(self, *args: Any) -> None:
        return


_server = ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
threading.Thread(target=_server.serve_forever, daemon=True).start()
_port = _server.server_address[1]
os.environ.update(
    {
        "DATABASE_URL": f"sqlite:///{ROOT / 'test.db'}",
        "FX_API_URL": f"http://127.0.0.1:{_port}/latest",
        "SENTRY_DSN": f"http://pk@127.0.0.1:{_port}/1",
    }
)


@pytest.fixture()
def client() -> Iterator[Any]:
    from fastapi.testclient import TestClient

    from app.db import Base, engine
    from app.main import app

    Base.metadata.drop_all(engine)
    with TestClient(app) as test_client:
        yield test_client
