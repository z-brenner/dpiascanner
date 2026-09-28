"""mitmproxy addon: record every request and answer it without contacting the destination.

Writes one JSON line per request to /capture/capture.jsonl in the same format as
fixtures/mock-server. Bodies are recorded only so the worker can scan them for canaries;
the worker deletes the raw capture after reducing it.
"""

import base64
import hashlib
import json
import time

from mitmproxy import http, tcp

OUT = "/capture/capture.jsonl"
MAX_BODY = 1 << 20
REDACTED = frozenset({"authorization", "proxy-authorization", "cookie", "x-api-key"})


def _write(record):
    with open(OUT, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _response_for(method, host):
    if host.startswith("s3.") or ".s3." in host or host.endswith("amazonaws.com"):
        if method == "PUT":
            return 200, {"ETag": '"d41d8cd98f00b204e9800998ecf8427e"'}, b""
        return 200, {"Content-Type": "application/xml"}, b"<ok/>"
    if "stripe" in host:
        body = {"id": "cus_lantern_sandbox", "object": "customer", "livemode": False}
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()
    if "mixpanel" in host:
        return 200, {"Content-Type": "application/json"}, b'{"status":1,"error":null}'
    return 200, {"Content-Type": "application/json"}, b'{"ok":true,"id":"0"}'


class Recorder:
    def running(self):
        print("LANTERN_PROXY_READY", flush=True)

    def request(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        body = (req.get_content(strict=False) or b"")[:MAX_BODY]
        host = req.pretty_host or (flow.client_conn.sni or "")
        _write(
            {
                "ts": time.time(),
                "method": req.method,
                "host": host,
                "path": req.path.split("?", 1)[0],
                "query": req.path.split("?", 1)[1] if "?" in req.path else "",
                "scheme": req.scheme,
                "tls": bool(flow.client_conn.tls_established),
                "headers": {
                    k.lower(): ("[REDACTED]" if k.lower() in REDACTED else v)
                    for k, v in req.headers.items()
                },
                "body_b64": base64.b64encode(body).decode("ascii"),
                "body_sha256": hashlib.sha256(body).hexdigest(),
                "recorder": "mitmproxy",
            }
        )
        status, headers, payload = _response_for(req.method, host)
        flow.response = http.Response.make(status, payload, headers)

    def tcp_start(self, flow: tcp.TCPFlow) -> None:
        # Not HTTP. Record the destination and close; nothing is forwarded.
        _write(
            {
                "ts": time.time(),
                "method": "TCP",
                "host": flow.client_conn.sni or "",
                "path": "",
                "query": "",
                "scheme": "tcp",
                "tls": bool(flow.client_conn.tls_established),
                "headers": {},
                "body_b64": "",
                "body_sha256": hashlib.sha256(b"").hexdigest(),
                "recorder": "mitmproxy",
                "port": flow.server_conn.address[1] if flow.server_conn.address else None,
            }
        )
        flow.kill()


addons = [Recorder()]
