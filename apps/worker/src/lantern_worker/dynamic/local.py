"""Run Lantern's own fixtures on the host, against fixtures/mock-server.

This backend exists so the verifier can be tested without Docker. It refuses any repository
outside the fixtures directory: code from a scanned repository runs only in the Docker
sandbox (``sandbox.py``). Fixture endpoints are rewritten through ``lantern.yml``
(``dynamic.endpoints``) to point at the mock server, which records every request in the
same raw capture format the sandbox proxy writes.

Isolation here is limited to what a trusted fixture needs: a minimal environment (no proxy
variables, no credentials from the worker's environment), a fresh temporary directory,
rlimits on CPU seconds and file size, and a wall-clock timeout on every process group.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import os
import resource
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

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
from lantern_worker.dynamic.routes import drive

SETTLE_S = 2.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _load_mock_server(fixtures_root: Path) -> ModuleType:
    path = fixtures_root / "mock-server" / "mock_server.py"
    spec = importlib.util.spec_from_file_location("lantern_fixture_mock_server", path)
    if spec is None or spec.loader is None:
        raise SetupError("mock-server", f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _rlimits(limits: Limits) -> Callable[[], None]:
    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        size = limits.max_file_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))

    return apply


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL)


class LocalFixtureBackend:
    name = "local-fixture"

    def __init__(self, fixtures_root: Path, env_cache: Path | None = None) -> None:
        self.fixtures_root = fixtures_root.resolve()
        self.env_cache = env_cache or Path.home() / ".cache" / "lantern" / "fixture-envs"

    # --- dependency setup -------------------------------------------------------------

    def _python_env(self, repo: Path, limits: Limits) -> Path:
        reqs = [p for p in (repo / "requirements-dev.txt", repo / "requirements.txt") if p.exists()]
        if not reqs:
            raise SetupError("install", "no requirements file")
        digest = hashlib.sha256(b"".join(p.read_bytes() for p in reqs)).hexdigest()[:16]
        venv = self.env_cache / f"py312-{digest}"
        marker = venv / ".lantern-ready"
        if marker.exists():
            return venv / "bin"
        uv = shutil.which("uv")
        if uv is None:
            raise SetupError("install", "uv not found")
        venv.parent.mkdir(parents=True, exist_ok=True)
        for argv in (
            [uv, "venv", "--quiet", "--python", "3.12", str(venv)],
            [
                uv,
                "pip",
                "install",
                "--quiet",
                "--python",
                str(venv / "bin" / "python"),
                "-r",
                str(reqs[0]),
            ],
        ):
            proc = subprocess.run(  # noqa: S603
                argv, cwd=repo, capture_output=True, timeout=limits.install_timeout_s, check=False
            )
            if proc.returncode != 0:
                raise SetupError("install", tail(proc.stderr, 800))
        marker.write_text("ok")
        return venv / "bin"

    def _node_env(self, repo: Path, limits: Limits) -> Path:
        npm = shutil.which("npm")
        if npm is None:
            raise SetupError("install", "npm not found")
        if not (repo / "node_modules").exists():
            proc = subprocess.run(  # noqa: S603
                [npm, "ci", "--no-audit", "--no-fund"],
                cwd=repo,
                capture_output=True,
                timeout=limits.install_timeout_s,
                check=False,
            )
            if proc.returncode != 0:
                raise SetupError("install", tail(proc.stderr, 800))
        return Path(npm).parent

    # --- session ------------------------------------------------------------------------

    @contextlib.contextmanager
    def open(
        self,
        repo: Path,
        settings: DynamicSettings,
        canaries: CanarySet,
        workdir: Path,
        limits: Limits,
    ) -> Iterator[LocalSession]:
        repo = repo.resolve()
        if not repo.is_relative_to(self.fixtures_root) or repo == self.fixtures_root:
            raise PermissionError(
                "the local backend runs Lantern's own fixtures only; "
                "repository code runs in the Docker sandbox"
            )
        language = detect_language(repo)
        bin_dir = (
            self._python_env(repo, limits) if language == "python" else self._node_env(repo, limits)
        )
        mock = _load_mock_server(self.fixtures_root)
        tmp = workdir / "tmp"
        tmp.mkdir(parents=True, exist_ok=True)
        capture = workdir / "capture.jsonl"
        with mock.MockServer(capture) as server:
            env = {
                "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
                "HOME": str(tmp),
                "TMPDIR": str(tmp),
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "NODE_ENV": "test",
                "NO_UPDATE_NOTIFIER": "1",
                "npm_config_update_notifier": "false",
            }
            node = shutil.which("node")
            if node:
                env["PATH"] = os.pathsep.join(
                    [str(bin_dir), str(Path(node).parent), "/usr/bin", "/bin"]
                )
            # Every sandbox run gets a fresh /tmp; emulate that for declared paths.
            env.update({k: v.replace("/tmp/", f"{tmp}/") for k, v in settings.env.items()})  # noqa: S108
            env.update(canaries.env())
            for endpoint in settings.endpoints:
                env[endpoint.env] = server.host_url(endpoint.url, endpoint.rewrite)
            yield LocalSession(repo, env, capture, limits, settings, canaries)


class LocalSession:
    def __init__(
        self,
        repo: Path,
        env: dict[str, str],
        capture: Path,
        limits: Limits,
        settings: DynamicSettings,
        canaries: CanarySet,
    ) -> None:
        self.repo = repo
        self.env = env
        self.capture = capture
        self.limits = limits
        self.settings = settings
        self.canaries = canaries

    def _resolve(self, argv: tuple[str, ...], port: int) -> list[str]:
        out = [a.replace(PORT, str(port)) for a in argv]
        found = shutil.which(out[0], path=self.env["PATH"])
        return [found or out[0], *out[1:]]

    def run(self, step: Step, static_routes: list[dict[str, Any]], budget_s: float) -> StepOutcome:
        before = count_lines(self.capture)
        timeout = max(1.0, min(self.limits.step_timeout_s, budget_s))
        if step.mode == "routes":
            outcome = self._routes(step, static_routes, timeout)
        else:
            outcome = self._command(step, timeout)
        outcome.requests = count_lines(self.capture) - before
        return outcome

    def _command(self, step: Step, timeout: float) -> StepOutcome:
        argv = self._resolve(step.argv, self.settings.port)
        started = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=self.repo,
            env={**self.env, "PORT": str(self.settings.port)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=_rlimits(self.limits),
            start_new_session=True,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            out, _ = proc.communicate()
            timed_out = True
        return StepOutcome(
            step.mode,
            list(step.argv),
            proc.returncode,
            time.monotonic() - started,
            timed_out=timed_out,
            output_tail=tail(out),
            output_canaries=output_hits(self.canaries, out),
        )

    def _routes(
        self, step: Step, static_routes: list[dict[str, Any]], timeout: float
    ) -> StepOutcome:
        port = (
            _free_port() if (PORT in step.argv or not self.settings.start) else self.settings.port
        )
        argv = self._resolve(step.argv, port)
        started = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603
            argv,
            cwd=self.repo,
            env={**self.env, "PORT": str(port)},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=_rlimits(self.limits),
            start_new_session=True,
        )
        plan = {
            "base_url": f"http://127.0.0.1:{port}",
            "canaries": self.canaries.values,
            "static_routes": static_routes,
            "startup_timeout": min(60.0, timeout / 2),
            "budget_s": max(1.0, timeout - 10),
        }
        report = drive(plan)
        time.sleep(SETTLE_S)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            out, _ = proc.communicate()
        return StepOutcome(
            step.mode,
            list(step.argv),
            proc.returncode,
            time.monotonic() - started,
            output_tail=tail(out),
            routes=report.to_dict(),
            output_canaries=output_hits(self.canaries, out),
        )
