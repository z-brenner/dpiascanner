"""Docker sandbox backend: the only place a scanned repository's code runs.

Layout of one run:

- A per-run CA (``generate_ca``). Its certificate is baked into the application image; its
  private key goes only into the proxy container and is deleted with the run directory.
- A proxy container (image built from ``proxy/``) that owns the network namespace. It has
  no network at all by default (``--network none`` plus a dummy default route) or, where the
  kernel lacks dummy interfaces, an ``--internal`` Docker network with no gateway. Inside
  it, dnsmasq resolves every name to 198.51.100.1, an iptables NAT rule sends all TCP to
  mitmproxy in transparent mode, and every other packet that leaves loopback is dropped.
  mitmproxy records each request and answers it itself; nothing is forwarded upstream.
- The application container joins that namespace (``--network container:<proxy>``) with
  every capability dropped, ``no-new-privileges``, a read-only root filesystem, a size-capped
  tmpfs for ``/tmp``, and CPU, memory, swap, process-count and file-size limits.
- For route exercise, a helper container from the proxy image joins the same namespace and
  runs ``routes.py`` against the application on loopback.

Dependency installation happens in ``docker build``, which needs registry access. Install
scripts therefore run with network access, inside the build container, with nothing in the
build context but the repository and Lantern's CA files. The application itself never runs
with network access.

Environment for building behind a TLS-intercepting proxy (development and CI only):
``LANTERN_BUILD_NETWORK`` (``host``), ``LANTERN_BUILD_CA_CERT`` (path to a PEM bundle), and
``LANTERN_BUILD_HTTPS_PROXY``.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from importlib import resources
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from lantern_worker.dynamic.base import (
    Limits,
    SetupError,
    StepOutcome,
    count_lines,
    output_hits,
    tail,
)
from lantern_worker.dynamic.canaries import CanarySet
from lantern_worker.dynamic.plan import PORT, DynamicSettings, Step, detect_language

PROXY_FILES = ("Dockerfile", "entrypoint.sh", "recorder.py")
APP_UID = 10001
PROXY_UID = 10002
READY = "LANTERN_PROXY_READY"
SETTLE_S = 3.0
_DNS_QUERY = re.compile(r"query\[(?:A|AAAA|HTTPS|SVCB)\] (\S+) from")
_CONTEXT_EXCLUDE = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache"}
)

PYTHON_DOCKERFILE = """\
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_ROOT_USER_ACTION=ignore
COPY .lantern/build-ca.crt /tmp/lantern-build-ca.crt
WORKDIR /app
COPY . /app
RUN --mount=type=cache,target=/root/.cache/pip \\
    cat /etc/ssl/certs/ca-certificates.crt /tmp/lantern-build-ca.crt > /tmp/build-bundle.crt \\
 && export PIP_CERT=/tmp/build-bundle.crt \\
 && {install} \\
 && (python -c "import pytest" 2>/dev/null || pip install -q pytest) \\
 && rm -f /tmp/build-bundle.crt /tmp/lantern-build-ca.crt
RUN mkdir -p /lantern && cp .lantern/run-ca.crt .lantern/inject_ca.py /lantern/ \\
 && python /lantern/inject_ca.py /lantern/run-ca.crt \\
 && rm -rf /app/.lantern \\
 && useradd --uid {uid} --no-create-home --home-dir /tmp lantern
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \\
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \\
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \\
    AWS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \\
    NODE_EXTRA_CA_CERTS=/lantern/run-ca.crt \\
    HOME=/tmp TMPDIR=/tmp
USER {uid}
"""

NODE_DOCKERFILE = """\
FROM node:22-slim
COPY .lantern/build-ca.crt /tmp/lantern-build-ca.crt
WORKDIR /app
COPY . /app
RUN --mount=type=cache,target=/root/.npm \\
    if [ -s /tmp/lantern-build-ca.crt ]; then \\
        export NODE_EXTRA_CA_CERTS=/tmp/lantern-build-ca.crt; \\
    fi \\
 && if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; \\
    else npm install --no-audit --no-fund; fi \\
 && if [ -f prisma/schema.prisma ]; then (npx --no-install prisma generate || true); fi \\
 && rm -f /tmp/lantern-build-ca.crt
RUN mkdir -p /lantern && cp .lantern/run-ca.crt /lantern/ && rm -rf /app/.lantern \\
 && useradd --uid {uid} --no-create-home --home-dir /tmp lantern
ENV NODE_EXTRA_CA_CERTS=/lantern/run-ca.crt HOME=/tmp TMPDIR=/tmp NODE_ENV=test \\
    NO_UPDATE_NOTIFIER=1 npm_config_update_notifier=false npm_config_cache=/tmp/.npm
USER {uid}
"""


def generate_ca(directory: Path) -> tuple[Path, Path]:
    """A throwaway CA for one run: (key+cert PEM for mitmproxy, cert PEM for the app)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, f"Lantern sandbox CA {uuid.uuid4().hex[:8]}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Lantern dynamic verification"),
        ]
    )
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    directory.mkdir(parents=True, exist_ok=True)
    combined = directory / "mitmproxy-ca.pem"
    combined.write_bytes(key_pem + cert_pem)
    cert_path = directory / "run-ca.crt"
    cert_path.write_bytes(cert_pem)
    return combined, cert_path


def _python_install(repo: Path) -> str:
    if (repo / "requirements-dev.txt").exists():
        return "pip install -q -r requirements-dev.txt"
    if (repo / "requirements.txt").exists():
        return "pip install -q -r requirements.txt"
    if (repo / "pyproject.toml").exists() or (repo / "setup.py").exists():
        return '(pip install -q ".[test]" || pip install -q ".[dev]" || pip install -q .)'
    return "true"


def dns_only_records(dns_log: str, seen_hosts: set[str]) -> list[dict[str, Any]]:
    """Capture records for names the application resolved but never sent HTTP to."""
    out: list[dict[str, Any]] = []
    for name in sorted({m.group(1).lower().rstrip(".") for m in _DNS_QUERY.finditer(dns_log)}):
        if name not in seen_hosts and "." in name:
            out.append(
                {
                    "ts": time.time(),
                    "method": "DNS",
                    "host": name,
                    "path": "",
                    "query": "",
                    "scheme": "dns",
                    "headers": {},
                    "body_b64": "",
                    "body_sha256": hashlib.sha256(b"").hexdigest(),
                    "recorder": "dnsmasq",
                }
            )
    return out


class DockerSandbox:
    name = "docker"

    def __init__(
        self,
        docker: str = "docker",
        build_network: str | None = None,
        build_ca: Path | None = None,
        build_https_proxy: str | None = None,
    ) -> None:
        self.docker = docker
        self.build_network = build_network
        self.build_ca = build_ca
        self.build_https_proxy = build_https_proxy

    @classmethod
    def from_env(cls) -> DockerSandbox:
        ca = os.environ.get("LANTERN_BUILD_CA_CERT")
        return cls(
            build_network=os.environ.get("LANTERN_BUILD_NETWORK") or None,
            build_ca=Path(ca) if ca else None,
            build_https_proxy=os.environ.get("LANTERN_BUILD_HTTPS_PROXY") or None,
        )

    # --- docker plumbing ------------------------------------------------------------------

    def _run(
        self, *args: str, timeout: float = 120, check: bool = True, input: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run(  # noqa: S603
            [self.docker, *args],
            capture_output=True,
            timeout=timeout,
            check=False,
            input=input,
        )
        if check and proc.returncode != 0:
            raise SetupError(f"docker {args[0]}", tail(proc.stderr, 800))
        return proc

    def available(self) -> bool:
        if shutil.which(self.docker) is None:
            return False
        try:
            return (
                self._run(
                    "info", "--format", "{{.ServerVersion}}", timeout=20, check=False
                ).returncode
                == 0
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _build(self, context: Path, tag: str, timeout: float) -> None:
        args = ["build", "--quiet", "--tag", tag]
        if self.build_network:
            args += ["--network", self.build_network]
        if self.build_https_proxy:
            args += [
                "--build-arg",
                f"HTTPS_PROXY={self.build_https_proxy}",
                "--build-arg",
                f"https_proxy={self.build_https_proxy}",
            ]
        self._run(*args, str(context), timeout=timeout)

    def _build_ca_bytes(self) -> bytes:
        return self.build_ca.read_bytes() if self.build_ca else b""

    # --- images ---------------------------------------------------------------------------

    def proxy_image(self, workdir: Path, timeout: float) -> str:
        package = resources.files("lantern_worker.dynamic")
        files = {name: (package / "proxy" / name).read_bytes() for name in PROXY_FILES}
        files["routes.py"] = (package / "routes.py").read_bytes()
        digest = hashlib.sha256(b"".join(files[k] for k in sorted(files))).hexdigest()[:16]
        tag = f"lantern-proxy:{digest}"
        if self._run("image", "inspect", tag, check=False).returncode == 0:
            return tag
        context = workdir / "proxy-context"
        context.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (context / name).write_bytes(data)
        (context / "build-ca.crt").write_bytes(self._build_ca_bytes())
        self._build(context, tag, timeout)
        shutil.rmtree(context, ignore_errors=True)
        return tag

    def app_image(self, repo: Path, run_ca: Path, workdir: Path, tag: str, timeout: float) -> None:
        context = workdir / "app-context"
        shutil.copytree(
            repo,
            context,
            ignore=shutil.ignore_patterns(*_CONTEXT_EXCLUDE),
            symlinks=True,
        )
        lantern = context / ".lantern"
        lantern.mkdir(exist_ok=True)
        (lantern / "build-ca.crt").write_bytes(self._build_ca_bytes())
        shutil.copy(run_ca, lantern / "run-ca.crt")
        package = resources.files("lantern_worker.dynamic")
        (lantern / "inject_ca.py").write_bytes((package / "images" / "inject_ca.py").read_bytes())
        if detect_language(repo) == "python":
            dockerfile = PYTHON_DOCKERFILE.format(install=_python_install(repo), uid=APP_UID)
        else:
            dockerfile = NODE_DOCKERFILE.format(uid=APP_UID)
        (lantern / "Dockerfile").write_text(dockerfile)
        (context / ".dockerignore").write_text("\n".join(sorted(_CONTEXT_EXCLUDE)) + "\n")
        try:
            args = ["build", "--quiet", "--tag", tag, "--file", str(lantern / "Dockerfile")]
            if self.build_network:
                args += ["--network", self.build_network]
            if self.build_https_proxy:
                args += ["--build-arg", f"HTTPS_PROXY={self.build_https_proxy}"]
            self._run(*args, str(context), timeout=timeout)
        finally:
            shutil.rmtree(context, ignore_errors=True)

    # --- session --------------------------------------------------------------------------

    def _start_proxy(self, image: str, name: str, ca_pem: Path, network: list[str]) -> None:
        self._run(
            "create",
            "--name",
            name,
            *network,
            "--cap-drop",
            "ALL",
            "--cap-add",
            "NET_ADMIN",
            "--cap-add",
            "NET_BIND_SERVICE",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--cap-add",
            "CHOWN",
            "--memory",
            "384m",
            "--pids-limit",
            "128",
            image,
        )
        ca_pem.chmod(0o644)
        self._run("cp", str(ca_pem), f"{name}:/lantern/ca/mitmproxy-ca.pem")
        self._run("start", name)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            logs = self._run("logs", name, check=False)
            if READY.encode() in logs.stdout + logs.stderr:
                return
            state = self._run("inspect", "--format", "{{.State.Running}}", name, check=False)
            if state.stdout.strip() == b"false":
                raise SetupError("proxy", tail(logs.stdout + logs.stderr, 800))
            time.sleep(0.3)
        raise SetupError("proxy", "proxy did not become ready")

    @contextlib.contextmanager
    def open(
        self,
        repo: Path,
        settings: DynamicSettings,
        canaries: CanarySet,
        workdir: Path,
        limits: Limits,
    ) -> Iterator[DockerSession]:
        run_id = uuid.uuid4().hex[:12]
        proxy_name = f"lantern-proxy-{run_id}"
        app_tag = f"lantern-app:{run_id}"
        network_name = f"lantern-net-{run_id}"
        created_network = False
        ca_dir = workdir / "ca"
        try:
            ca_pem, ca_cert = generate_ca(ca_dir)
            proxy_image = self.proxy_image(workdir, limits.install_timeout_s)
            self.app_image(repo, ca_cert, workdir, app_tag, limits.install_timeout_s)
            try:
                self._start_proxy(proxy_image, proxy_name, ca_pem, ["--network", "none"])
                network_mode = "none"
            except SetupError:
                self._run("rm", "-f", proxy_name, check=False)
                self._run("network", "create", "--internal", network_name)
                created_network = True
                self._start_proxy(proxy_image, proxy_name, ca_pem, ["--network", network_name])
                network_mode = "internal"
            env = {
                **settings.env,
                **canaries.env(),
                **{e.env: e.url for e in settings.endpoints},
            }
            yield DockerSession(
                self,
                proxy_name,
                proxy_image,
                app_tag,
                run_id,
                env,
                workdir,
                limits,
                settings,
                canaries,
                network_mode,
            )
        finally:
            self._cleanup(run_id, app_tag, network_name if created_network else None)
            shutil.rmtree(ca_dir, ignore_errors=True)

    def _cleanup(self, run_id: str, app_tag: str, network: str | None) -> None:
        listing = self._run("ps", "-aq", "--filter", f"name={run_id}", check=False)
        ids = listing.stdout.decode().split()
        if ids:
            self._run("rm", "-f", *ids, check=False, timeout=60)
        self._run("rmi", "-f", app_tag, check=False, timeout=60)
        if network:
            self._run("network", "rm", network, check=False)


class DockerSession:
    def __init__(
        self,
        sandbox: DockerSandbox,
        proxy: str,
        proxy_image: str,
        app_image: str,
        run_id: str,
        env: dict[str, str],
        workdir: Path,
        limits: Limits,
        settings: DynamicSettings,
        canaries: CanarySet,
        network_mode: str,
    ) -> None:
        self.sandbox = sandbox
        self.proxy = proxy
        self.proxy_image = proxy_image
        self.app_image = app_image
        self.run_id = run_id
        self.env = env
        self.capture = workdir / "capture.jsonl"
        self.limits = limits
        self.settings = settings
        self.canaries = canaries
        self.network_mode = network_mode
        self._steps = 0

    def _limit_args(self) -> list[str]:
        lim = self.limits
        return [
            "--network",
            f"container:{self.proxy}",
            "--user",
            f"{APP_UID}:{APP_UID}",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,exec,nosuid,size={lim.disk_mb}m",  # noqa: S108
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--cpus",
            str(lim.cpus),
            "--memory",
            f"{lim.memory_mb}m",
            "--memory-swap",
            f"{lim.memory_mb}m",
            "--pids-limit",
            str(lim.pids),
            "--ulimit",
            f"fsize={lim.max_file_mb * 1024 * 1024}",
            "--ulimit",
            f"cpu={lim.cpu_seconds}",
        ]

    def _env_args(self, port: int) -> list[str]:
        out: list[str] = []
        for key, value in {**self.env, "PORT": str(port)}.items():
            out += ["--env", f"{key}={value}"]
        return out

    def _sync_capture(self) -> None:
        """Copy the proxy's capture out, plus DNS-only lookups, replacing the local copy."""
        tmp = self.capture.with_suffix(".tmp")
        got = self.sandbox._run("cp", f"{self.proxy}:/capture/capture.jsonl", str(tmp), check=False)
        lines = tmp.read_text("utf-8").splitlines() if got.returncode == 0 and tmp.exists() else []
        tmp.unlink(missing_ok=True)
        hosts = {json.loads(line)["host"].lower().split(":")[0] for line in lines if line.strip()}
        dns = self.sandbox._run("exec", self.proxy, "cat", "/capture/dns.log", check=False)
        extra = dns_only_records(dns.stdout.decode("utf-8", "replace"), hosts)
        with self.capture.open("w", encoding="utf-8") as fh:
            for line in lines:
                if line.strip():
                    fh.write(line + "\n")
            for record in extra:
                fh.write(json.dumps(record, sort_keys=True) + "\n")

    def run(self, step: Step, static_routes: list[dict[str, Any]], budget_s: float) -> StepOutcome:
        self._steps += 1
        before = count_lines(self.capture)
        timeout = max(1.0, min(self.limits.step_timeout_s, budget_s))
        if step.mode == "routes":
            outcome = self._routes(step, static_routes, timeout)
        else:
            outcome = self._command(step, timeout)
        self._sync_capture()
        outcome.requests = count_lines(self.capture) - before
        return outcome

    def _command(self, step: Step, timeout: float) -> StepOutcome:
        name = f"lantern-app{self._steps}-{self.run_id}"
        port = self.settings.port
        argv = [a.replace(PORT, str(port)) for a in step.argv]
        started = time.monotonic()
        timed_out = False
        try:
            proc = self.sandbox._run(
                "run",
                "--name",
                name,
                *self._limit_args(),
                *self._env_args(port),
                self.app_image,
                *argv,
                timeout=timeout,
                check=False,
            )
            exit_code: int | None = proc.returncode
            output = proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            self.sandbox._run("kill", name, check=False)
            timed_out, exit_code = True, None
            logs = self.sandbox._run("logs", name, check=False)
            output = logs.stdout + logs.stderr
        self.sandbox._run("rm", "-f", name, check=False)
        return StepOutcome(
            step.mode,
            list(step.argv),
            exit_code,
            time.monotonic() - started,
            timed_out=timed_out,
            output_tail=tail(output),
            output_canaries=output_hits(self.canaries, output),
        )

    def _routes(
        self, step: Step, static_routes: list[dict[str, Any]], timeout: float
    ) -> StepOutcome:
        server = f"lantern-srv{self._steps}-{self.run_id}"
        driver = f"lantern-drv{self._steps}-{self.run_id}"
        port = self.settings.port
        argv = [a.replace(PORT, str(port)) for a in step.argv]
        started = time.monotonic()
        self.sandbox._run(
            "run",
            "--detach",
            "--name",
            server,
            *self._limit_args(),
            *self._env_args(port),
            self.app_image,
            *argv,
        )
        plan = {
            "base_url": f"http://127.0.0.1:{port}",
            "canaries": self.canaries.values,
            "static_routes": static_routes,
            "startup_timeout": min(60.0, timeout / 2),
            "budget_s": max(1.0, timeout - 20),
        }
        report: dict[str, Any] | None = None
        error = ""
        try:
            proc = self.sandbox._run(
                "run",
                "--rm",
                "--interactive",
                "--name",
                driver,
                "--network",
                f"container:{self.proxy}",
                "--user",
                f"{PROXY_UID}:{PROXY_UID}",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--memory",
                "256m",
                "--entrypoint",
                "python",
                self.proxy_image,
                "/lantern/routes.py",
                "-",
                input=json.dumps(plan).encode(),
                timeout=timeout,
                check=False,
            )
            report = json.loads(proc.stdout) if proc.returncode == 0 else None
            if report is None:
                error = tail(proc.stderr, 500)
        except (subprocess.TimeoutExpired, ValueError) as exc:
            error = type(exc).__name__
            self.sandbox._run("rm", "-f", driver, check=False)
        time.sleep(SETTLE_S)
        self.sandbox._run("stop", "--time", "10", server, check=False, timeout=30)
        logs = self.sandbox._run("logs", server, check=False)
        state = self.sandbox._run("inspect", "--format", "{{.State.ExitCode}}", server, check=False)
        self.sandbox._run("rm", "-f", server, check=False)
        output = logs.stdout + logs.stderr
        code = state.stdout.strip()
        return StepOutcome(
            step.mode,
            list(step.argv),
            int(code) if code.lstrip(b"-").isdigit() else None,
            time.monotonic() - started,
            output_tail=tail(output),
            routes=report,
            error=error,
            output_canaries=output_hits(self.canaries, output),
        )
