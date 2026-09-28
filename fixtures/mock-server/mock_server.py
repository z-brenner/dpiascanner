"""A tiny HTTP server that records every request it receives to a JSONL file.

Fixture apps point their third-party endpoints at this server through environment
variables. To keep the original destination visible, endpoint URLs carry the vendor
host as a path prefix:

    https://api.stripe.com            ->  http://127.0.0.1:PORT/_host/api.stripe.com
    https://pk@o0.ingest.sentry.io/0  ->  http://pk@127.0.0.1:PORT/_host/o0.ingest.sentry.io/0

The server strips the prefix and records ``host=api.stripe.com`` and the remaining path.
Requests without the prefix are recorded with the Host header as ``host``.

Some SDKs accept only host, port, and protocol (stripe-node, for example), so a path prefix
cannot survive. For those, ``MockServer.host_url(url, rewrite="port")`` binds a dedicated
port for that vendor host and records every request on that port under the vendor host.

Standard library only, so it runs anywhere the fixtures run.

    python mock_server.py --port 8099 --out requests.jsonl
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

HOST_PREFIX = "/_host/"
REDACTED_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "x-api-key"})


def split_host_prefix(raw_path: str, host_header: str) -> tuple[str, str]:
    """Return (host, path) for a request path that may carry a /_host/<host> prefix."""
    if raw_path.startswith(HOST_PREFIX):
        rest = raw_path[len(HOST_PREFIX) :]
        host, sep, path = rest.partition("/")
        return host, "/" + path if sep else "/"
    return host_header, raw_path


def decode_body(raw: bytes, content_encoding: str) -> bytes:
    encoding = content_encoding.lower().strip()
    try:
        if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        if encoding == "deflate":
            return zlib.decompress(raw)
    except (OSError, zlib.error):
        return raw
    return raw


class _Recorder:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.lock = threading.Lock()
        self.out.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, sort_keys=True)
        with self.lock, self.out.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _response_for(method: str, host: str, path: str) -> tuple[int, dict[str, str], bytes]:
    """Plausible minimal responses so SDKs do not raise on the mock."""
    if host.startswith("s3.") or ".s3." in host or host.endswith("amazonaws.com"):
        if method == "PUT":
            return 200, {"ETag": '"d41d8cd98f00b204e9800998ecf8427e"'}, b""
        return 200, {"Content-Type": "application/xml"}, b"<ok/>"
    if "stripe" in host:
        body = {"id": "cus_mock_lantern", "object": "customer", "livemode": False}
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
    if "sentry" in host:
        return 200, {"Content-Type": "application/json"}, b'{"id":"0"}'
    if "mixpanel" in host:
        return 200, {"Content-Type": "application/json"}, b'{"status":1,"error":null}'
    body = {"ok": True, "forecast": "clear", "temperature_c": 18}
    return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()


def make_handler(recorder: _Recorder, vhost: str | None = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "lantern-mock/1"

        def _read_body(self) -> bytes:
            if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                chunks = []
                while True:
                    size_line = self.rfile.readline().strip()
                    size = int(size_line.split(b";")[0] or b"0", 16)
                    if size == 0:
                        self.rfile.readline()
                        break
                    chunks.append(self.rfile.read(size))
                    self.rfile.readline()
                return b"".join(chunks)
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _handle(self) -> None:
            raw = self._read_body()
            body = decode_body(raw, self.headers.get("Content-Encoding", ""))
            url = urlsplit(self.path)
            if vhost is not None:
                host, path = vhost, url.path
            else:
                host, path = split_host_prefix(url.path, self.headers.get("Host", ""))
            headers = {
                k.lower(): ("[REDACTED]" if k.lower() in REDACTED_HEADERS else v)
                for k, v in self.headers.items()
            }
            recorder.write(
                {
                    "ts": time.time(),
                    "method": self.command,
                    "host": host,
                    "path": path,
                    "query": url.query,
                    "headers": headers,
                    "body_b64": base64.b64encode(body).decode("ascii"),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                }
            )
            status, extra, payload = _response_for(self.command, host, path)
            self.send_response(status)
            for key, value in extra.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _handle

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

    return Handler


class MockServer:
    """Run the recorder in a background thread. Usable from tests."""

    def __init__(self, out: Path, host: str = "127.0.0.1", port: int = 0) -> None:
        self.bind_host = host
        self.recorder = _Recorder(out)
        self.httpd = ThreadingHTTPServer((host, port), make_handler(self.recorder))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._vhosts: dict[str, ThreadingHTTPServer] = {}

    @property
    def port(self) -> int:
        return int(self.httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def vhost_port(self, vendor_host: str) -> int:
        """Bind (once) a dedicated port whose requests are all recorded under vendor_host."""
        if vendor_host not in self._vhosts:
            server = ThreadingHTTPServer(
                (self.bind_host, 0), make_handler(self.recorder, vhost=vendor_host)
            )
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self._vhosts[vendor_host] = server
        return int(self._vhosts[vendor_host].server_address[1])

    def host_url(self, original_url: str, rewrite: str = "prefix") -> str:
        """Rewrite an original https URL so it targets this server.

        rewrite="prefix" keeps one port and carries the vendor host as a /_host/ prefix.
        rewrite="port" gives the vendor host its own port and keeps the original path.
        """
        parts = urlsplit(original_url)
        if parts.hostname is None:
            raise ValueError(f"not an absolute URL: {original_url}")
        userinfo = f"{parts.username}@" if parts.username else ""
        query = f"?{parts.query}" if parts.query else ""
        if rewrite == "port":
            port = self.vhost_port(parts.hostname)
            return f"http://{userinfo}127.0.0.1:{port}{parts.path}{query}"
        if rewrite != "prefix":
            raise ValueError(f"unknown rewrite mode: {rewrite}")
        path = f"{HOST_PREFIX}{parts.hostname}{parts.path}"
        return f"http://{userinfo}127.0.0.1:{self.port}{path}{query}"

    def __enter__(self) -> MockServer:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        for server in [self.httpd, *self._vhosts.values()]:
            server.shutdown()
            server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--out", type=Path, default=Path("requests.jsonl"))
    parser.add_argument("--port-file", type=Path, help="write the bound port here once listening")
    args = parser.parse_args()
    server = MockServer(args.out, args.host, args.port)
    if args.port_file:
        args.port_file.write_text(str(server.port))
    print(f"lantern mock server on {args.host}:{server.port}, recording to {args.out}", flush=True)
    server.httpd.serve_forever()


if __name__ == "__main__":
    main()
