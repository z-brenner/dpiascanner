"""Docker sandbox integration tests.

They build the proxy image and one application image per fixture, so they are opt-in:
``LANTERN_DOCKER_TESTS=1 make test-docker``. Behind a TLS-intercepting proxy, also set
LANTERN_BUILD_NETWORK=host, LANTERN_BUILD_CA_CERT, and LANTERN_BUILD_HTTPS_PROXY so the image
builds can reach the package registries (the application itself never gets a network).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lantern_analysis.analyze import analyze_repo
from lantern_analysis.model import DataFlowGraph
from lantern_worker.dynamic import DynamicSettings, DynamicVerifier, Limits, Step, plan_steps
from lantern_worker.dynamic.reconcile import is_network_sink
from lantern_worker.dynamic.sandbox import DockerSandbox, dns_only_records, generate_ca

pytestmark = pytest.mark.docker

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
LIMITS = Limits(install_timeout_s=1200, step_timeout_s=180, wall_clock_s=420)

PROBE = r"""
import json, socket, ssl, urllib.request
out = {"resolved": socket.gethostbyname("example.com")}
try:
    out["http"] = urllib.request.urlopen("http://example.com/", timeout=10).read().decode()[:40]
except Exception as exc:
    out["http"] = "error " + type(exc).__name__
query = bytes.fromhex("123401000001000000000000076578616d706c6503636f6d0000010001")
for name, addr in (("udp_dns", "8.8.8.8"), ("docker_dns", "127.0.0.11")):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(query, (addr, 53)); s.recvfrom(512); out[name] = "answered"
    except OSError as exc:
        out[name] = "blocked " + type(exc).__name__
with socket.create_connection(("example.com", 443), timeout=10) as sock:
    with ssl.create_default_context().wrap_socket(sock, server_hostname="example.com") as tls:
        issuer = dict(item[0] for item in tls.getpeercert()["issuer"])
out["tls_issuer"] = issuer.get("organizationName", "")
print("PROBE" + json.dumps(out))
"""


@pytest.fixture(scope="module")
def sandbox() -> DockerSandbox:
    if not os.environ.get("LANTERN_DOCKER_TESTS"):
        pytest.skip("set LANTERN_DOCKER_TESTS=1 to run the Docker sandbox tests")
    box = DockerSandbox.from_env()
    if not box.available():
        pytest.fail("LANTERN_DOCKER_TESTS=1 but no Docker daemon is reachable")
    return box


def test_ca_is_a_constrained_throwaway(tmp_path):
    from cryptography import x509

    combined, cert_path = generate_ca(tmp_path)
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    constraints = cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    assert constraints.ca and constraints.path_length == 0
    assert (cert.not_valid_after_utc - cert.not_valid_before_utc).days <= 3
    assert b"PRIVATE KEY" in combined.read_bytes() and b"PRIVATE KEY" not in cert_path.read_bytes()


def test_dns_only_records():
    log = (
        "Sep 28 dnsmasq[9]: query[A] api.stripe.com from 127.0.0.1\n"
        "Sep 28 dnsmasq[9]: query[AAAA] smtp.sendgrid.net from 127.0.0.1\n"
        "Sep 28 dnsmasq[9]: config api.stripe.com is 198.51.100.1\n"
    )
    records = dns_only_records(log, {"api.stripe.com"})
    assert [(r["method"], r["host"]) for r in records] == [("DNS", "smtp.sendgrid.net")]


@pytest.mark.parametrize("fixture", ["canary-python", "canary-typescript"])
def test_sandbox_verifies_the_canary_fixtures(sandbox, fixture):
    repo = FIXTURES / fixture
    graph = analyze_repo(repo, "fixture-sha")
    steps = [s for s in plan_steps(repo, DynamicSettings.load(repo)) if s.mode == "script"]
    report = DynamicVerifier(sandbox, LIMITS).verify(repo, graph, steps=steps)
    assert report.status == "completed", report.notes
    assert all(r.scheme == "https" for r in report.observed if r.method != "DNS")
    for node in graph.nodes.values():
        if is_network_sink(node) and node.file != "<dynamic>":
            expected = "verified" if node.attrs.get("reachable", True) else "inferred"
            assert node.attrs["dynamic"]["status"] == expected, (node.file, node.line_start)


def test_sandbox_blocks_egress(sandbox):
    repo = FIXTURES / "canary-python"
    graph = DataFlowGraph(repo=str(repo), commit="x", languages=["python"])
    step = Step("script", ("python", "-c", PROBE))
    report = DynamicVerifier(sandbox, LIMITS).verify(repo, graph, steps=[step])
    [outcome] = report.steps
    line = next(ln for ln in outcome.output_tail.splitlines() if ln.startswith("PROBE"))
    probe = json.loads(line[len("PROBE") :])
    assert probe["resolved"] == "198.51.100.1"  # every name resolves to the sink address
    assert probe["http"].startswith('{"ok":true')  # answered by the proxy, not example.com
    assert probe["udp_dns"].startswith("blocked")
    assert probe["docker_dns"].startswith("blocked")
    # The TLS peer for any host is the sandbox proxy, with a certificate from the run's CA.
    assert probe["tls_issuer"] == "Katz dynamic verification"
    assert {r.host for r in report.observed} >= {"example.com"}
