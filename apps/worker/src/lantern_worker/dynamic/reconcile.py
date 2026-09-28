"""Reconcile observed requests with the static sink nodes of a data-flow graph.

Each sink node carries ``attrs["endpoints"]["hosts"]``: the hosts it is expected to reach,
from registry entries, URL literals, config values, and environment-variable defaults
(``lantern_analysis.endpoints``). Host patterns may carry a leading ``*.`` wildcard.

Statuses, per sink node:

- ``verified``: a request to one of the sink's hosts carried a canary value.
- ``inferred``: the sink is known only from static analysis. ``reason`` says why:
  ``observed-without-canary`` (a request reached the host but carried no canary),
  ``not-exercised`` (no request reached the host), ``unreachable`` (static analysis found no
  entry point that reaches it), ``no-known-host`` (static analysis could not name a host),
  ``not-network-observable`` (a first-party store or file write), or
  ``not-observed-in-output`` (a log sink whose data never appeared in process output).
- Log sinks (``family: log``) are verified from the application's own output instead: a
  canary whose kind matches a field flowing into the sink appeared in stdout or stderr
  (reason ``canary-in-process-output``). That attribution is by data kind, not call site,
  and is weaker evidence than a request; the reason string keeps the two apart.
- ``observed-unexpected``: set on new sink nodes created for requests to hosts no static sink
  represents. Their evidence is ``dynamic-only``.

Attribution is by host, not by call site: when several sinks share a host (two Sentry calls,
for example), a canary-bearing request to that host verifies all of them, and each lists the
others in ``shared_host_with``. Per-route attribution is future work.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any

from lantern_analysis.model import DataFlowGraph, Node, stable_id
from lantern_worker.dynamic.canaries import CanaryHit
from lantern_worker.dynamic.observed import ObservedRequest
from lantern_worker.dynamic.routes import kind_for_field

NETWORK_FAMILIES = frozenset({"http", "registry", "queue", "dependency"})


def host_matches(pattern: str, host: str) -> bool:
    pattern, host = pattern.lower(), host.lower()
    if pattern == host:
        return True
    if pattern.startswith("*."):
        return host.endswith(pattern[1:]) or fnmatchcase(host, pattern)
    return False


def is_network_sink(node: Node) -> bool:
    return node.kind == "sink" and (
        node.attrs.get("family") in NETWORK_FAMILIES or node.attrs.get("registry") is not None
    )


@dataclass
class SinkVerification:
    node_id: str
    status: str  # verified | inferred | observed-unexpected
    reason: str
    hosts: list[str] = field(default_factory=list)
    canaries: list[str] = field(default_factory=list)
    encodings: list[str] = field(default_factory=list)
    request_count: int = 0
    shared_host_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "hosts": self.hosts,
            "canaries": self.canaries,
            "encodings": self.encodings,
            "request_count": self.request_count,
            "shared_host_with": self.shared_host_with,
        }


@dataclass
class UnexpectedHost:
    host: str
    node_id: str
    methods: list[str]
    paths: list[str]
    canaries: list[str]
    encodings: list[str]
    request_count: int
    body_field_names: list[str]
    candidate_sinks: list[str]  # network sinks whose destination static analysis could not name

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "node_id": self.node_id,
            "methods": self.methods,
            "paths": self.paths,
            "canaries": self.canaries,
            "encodings": self.encodings,
            "request_count": self.request_count,
            "body_field_names": self.body_field_names,
            "candidate_sinks": self.candidate_sinks,
        }


@dataclass
class Reconciliation:
    sinks: dict[str, SinkVerification]
    unexpected: list[UnexpectedHost]
    hosts_observed: list[str]
    requests: int

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for v in self.sinks.values():
            counts[v.status] += 1
        counts["observed-unexpected"] += len(self.unexpected)
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "requests": self.requests,
            "hosts_observed": self.hosts_observed,
            "sinks": {k: v.to_dict() for k, v in sorted(self.sinks.items())},
            "unexpected": [u.to_dict() for u in self.unexpected],
        }


def _sink_hosts(node: Node) -> list[str]:
    endpoints = node.attrs.get("endpoints") or {}
    return [str(h) for h in endpoints.get("hosts", [])]


def _upstream_kinds(graph: DataFlowGraph) -> dict[str, set[str]]:
    """sink node id -> canary kinds of the source fields that flow into it."""
    kinds: dict[str, set[str]] = defaultdict(set)
    for flow in graph.flows:
        source, sink = graph.nodes[flow.node_ids[0]], flow.node_ids[-1]
        kind = kind_for_field(str(source.attrs.get("field") or ""))
        if kind:
            kinds[sink].add(kind)
            if kind == "email":
                kinds[sink].add("email_2")
    return kinds


def reconcile(
    graph: DataFlowGraph,
    observed: list[ObservedRequest],
    output_canaries: Iterable[CanaryHit] = (),
) -> Reconciliation:
    sinks = sorted((n for n in graph.nodes.values() if n.kind == "sink"), key=lambda n: n.id)
    network = [n for n in sinks if is_network_sink(n) and n.file != "<dynamic>"]
    by_host: dict[str, list[ObservedRequest]] = defaultdict(list)
    for request in observed:
        by_host[request.host].append(request)

    matched_hosts: set[str] = set()
    host_to_sinks: dict[str, set[str]] = defaultdict(set)
    per_sink: dict[str, list[ObservedRequest]] = {}
    for node in network:
        requests: list[ObservedRequest] = []
        for host, reqs in by_host.items():
            if any(host_matches(p, host) for p in _sink_hosts(node)):
                requests.extend(reqs)
                matched_hosts.add(host)
                host_to_sinks[host].add(node.id)
        per_sink[node.id] = requests

    output = list(output_canaries)
    upstream = _upstream_kinds(graph)
    results: dict[str, SinkVerification] = {}
    for node in sinks:
        if node.file == "<dynamic>":
            continue
        if not is_network_sink(node):
            if node.attrs.get("family") != "log":
                results[node.id] = SinkVerification(node.id, "inferred", "not-network-observable")
                continue
            seen = [h for h in output if h.kind in upstream.get(node.id, set())]
            if seen:
                results[node.id] = SinkVerification(
                    node.id,
                    "verified",
                    "canary-in-process-output",
                    canaries=sorted({h.kind for h in seen}),
                    encodings=sorted({h.encoding for h in seen}),
                )
            else:
                results[node.id] = SinkVerification(node.id, "inferred", "not-observed-in-output")
            continue
        requests = per_sink.get(node.id, [])
        hosts = sorted({r.host for r in requests})
        with_canary = [r for r in requests if r.canary_hits]
        shared = sorted({s for h in hosts for s in host_to_sinks[h]} - {node.id})
        if with_canary:
            hits = [h for r in with_canary for h in r.canary_hits]
            results[node.id] = SinkVerification(
                node.id,
                "verified",
                "canary-observed",
                hosts=hosts,
                canaries=sorted({h.kind for h in hits}),
                encodings=sorted({h.encoding for h in hits}),
                request_count=len(requests),
                shared_host_with=shared,
            )
        elif requests:
            results[node.id] = SinkVerification(
                node.id,
                "inferred",
                "observed-without-canary",
                hosts=hosts,
                request_count=len(requests),
                shared_host_with=shared,
            )
        elif not node.attrs.get("reachable", True):
            results[node.id] = SinkVerification(node.id, "inferred", "unreachable")
        elif not _sink_hosts(node):
            results[node.id] = SinkVerification(node.id, "inferred", "no-known-host")
        else:
            results[node.id] = SinkVerification(node.id, "inferred", "not-exercised")

    hostless = sorted(n.id for n in network if not _sink_hosts(n))
    unexpected: list[UnexpectedHost] = []
    for host in sorted(set(by_host) - matched_hosts):
        reqs = by_host[host]
        hits = [h for r in reqs for h in r.canary_hits]
        unexpected.append(
            UnexpectedHost(
                host=host,
                node_id=stable_id("N-snk", "<dynamic>", host),
                methods=sorted({r.method for r in reqs}),
                paths=sorted({r.path for r in reqs})[:20],
                canaries=sorted({h.kind for h in hits}),
                encodings=sorted({h.encoding for h in hits}),
                request_count=len(reqs),
                body_field_names=sorted({f for r in reqs for f in r.body_field_names})[:50],
                candidate_sinks=hostless,
            )
        )
    return Reconciliation(
        sinks=results,
        unexpected=unexpected,
        hosts_observed=sorted(by_host),
        requests=len(observed),
    )


def apply(graph: DataFlowGraph, result: Reconciliation, run_info: dict[str, Any]) -> None:
    """Write verification status onto sink nodes and add dynamic-only sink nodes."""
    for node_id, verification in result.sinks.items():
        graph.nodes[node_id].attrs["dynamic"] = verification.to_dict()
    for item in result.unexpected:
        graph.nodes[item.node_id] = Node(
            id=item.node_id,
            kind="sink",
            file="<dynamic>",
            line_start=0,
            line_end=0,
            symbol=f"observed request to {item.host}",
            language="",
            snippet="",
            rule_ids=["dynamic.observed-unexpected"],
            attrs={
                "family": "http",
                "evidence": "dynamic-only",
                "reachable": True,
                "endpoints": {"hosts": [item.host], "evidence": {item.host: "observed"}},
                "dynamic": {
                    "status": "observed-unexpected",
                    "reason": "host-not-in-static-graph",
                    "hosts": [item.host],
                    "canaries": item.canaries,
                    "encodings": item.encodings,
                    "request_count": item.request_count,
                    "methods": item.methods,
                    "paths": item.paths,
                    "body_field_names": item.body_field_names,
                    "candidate_sinks": item.candidate_sinks,
                    "shared_host_with": [],
                },
            },
        )
    graph.summary["dynamic"] = {**run_info, **result.to_dict()}
