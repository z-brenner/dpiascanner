"""Egress allowlist for outbound HTTP and git from the API and worker.

Every outbound ``httpx`` client is built with ``guarded_client``, whose request hook
refuses hosts outside the allowlist, and the clone step checks its URL with ``check_url``.
This is an application-level control; deployments should also enforce it at the network
layer (an egress proxy or a Kubernetes NetworkPolicy). Repository code never runs here: it
runs only in the dynamic-verification sandbox, which has no network at all.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase
from typing import Any
from urllib.parse import urlsplit

import httpx


class EgressDenied(RuntimeError):
    pass


class EgressPolicy:
    def __init__(self, allowlist: Iterable[str]) -> None:
        self.allowlist = tuple(h.lower() for h in allowlist)

    def allows(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return any(host == p or fnmatchcase(host, p) for p in self.allowlist)

    def check_url(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme not in ("https", "http", "file"):
            raise EgressDenied(f"scheme not allowed: {parts.scheme}")
        if parts.scheme == "file":
            if "file" not in self.allowlist:
                raise EgressDenied("file URLs are not allowed")
            return
        if not self.allows(parts.hostname or ""):
            raise EgressDenied(f"egress to {parts.hostname} is not in the allowlist")

    def hook(self, request: httpx.Request) -> None:
        if not self.allows(request.url.host):
            raise EgressDenied(f"egress to {request.url.host} is not in the allowlist")


def guarded_client(policy: EgressPolicy, **kwargs: Any) -> httpx.Client:
    hooks = kwargs.pop("event_hooks", {})
    hooks.setdefault("request", []).append(policy.hook)
    return httpx.Client(event_hooks=hooks, **kwargs)
