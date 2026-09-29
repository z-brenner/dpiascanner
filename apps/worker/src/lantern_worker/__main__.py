"""Lantern worker.

    python -m lantern_worker serve            consume the run queue
    python -m lantern_worker run-job RUN_ID   process one run (what serve spawns per job)
    python -m lantern_worker purge            delete data past LANTERN_RETENTION_HOURS once

``serve`` runs each job in a separate process (``LANTERN_JOB_ISOLATION=process``) or a fresh
container from ``LANTERN_WORKER_IMAGE`` (``container``), kills it at the run timeout, and
records the timeout against the stage that was running. With ``LANTERN_RETENTION_HOURS`` set,
``serve`` also deletes expired data every hour (``lantern_platform.retention``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

from lantern_decisions.factory import make_provider
from lantern_platform.config import Settings
from lantern_platform.crypto import TokenCipher
from lantern_platform.db import Database
from lantern_platform.egress import EgressPolicy
from lantern_platform.github import GitHub
from lantern_platform.queue import JobQueue, RedisQueue
from lantern_platform.retention import SWEEP_INTERVAL, purge_expired
from lantern_platform.tokens import DatabaseTokenStore
from lantern_worker.dynamic.base import Backend
from lantern_worker.dynamic.sandbox import DockerSandbox
from lantern_worker.fetch import GitFetcher
from lantern_worker.jobs import WorkerDeps, mark_failed, process_run

log = logging.getLogger("lantern.worker")
PASSTHROUGH_ENV = (
    "DATABASE_URL",
    "REDIS_URL",
    "GITHUB_APP_ID",
    "GITHUB_APP_PRIVATE_KEY",
    "GITHUB_API_URL",
    "GITHUB_WEB_URL",
    "LANTERN_TOKEN_KEY",
    "LANTERN_DECISION_PROVIDER",
    "TYPESAFE_API_KEY",
    "TYPESAFE_BASE_URL",
    "LANTERN_WEB_URL",
    "LANTERN_EGRESS_ALLOWLIST",
    "LANTERN_CLONE_MAX_MB",
)


def build_deps(settings: Settings) -> WorkerDeps:
    db = Database(settings.database_url)
    policy = EgressPolicy(settings.egress_allowlist)
    if os.environ.get("TYPESAFE_BASE_URL"):
        policy.check_url(os.environ["TYPESAFE_BASE_URL"])
    github = None
    if settings.github_app_id and settings.github_app_private_key:
        store = DatabaseTokenStore(db, TokenCipher(settings.token_key))
        github = GitHub(settings, policy, token_store=store)
    sandbox = DockerSandbox.from_env()

    def backend() -> Backend:
        return sandbox

    return WorkerDeps(
        db=db,
        settings=settings,
        fetcher=GitFetcher(policy, github, settings.github_web_url, settings.clone_max_mb),
        provider_factory=lambda: make_provider(),
        github=github,
        dynamic_backend=backend if sandbox.available() else None,
        report_base_url=settings.web_url,
    )


def job_command(run_id: str, settings: Settings) -> list[str]:
    if settings.job_isolation == "container":
        env: list[str] = []
        for name in PASSTHROUGH_ENV:
            if name in os.environ:
                env += ["--env", name]  # value taken from this process, never printed in argv
        return [
            "docker",
            "run",
            "--rm",
            "--name",
            f"lantern-job-{run_id}",
            "--memory",
            "4g",
            "--memory-swap",
            "4g",
            "--cpus",
            "2",
            "--pids-limit",
            "512",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,size={settings.clone_max_mb * 3}m",  # noqa: S108
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            *env,
            settings.worker_image,
            "python",
            "-m",
            "lantern_worker",
            "run-job",
            run_id,
        ]
    return [sys.executable, "-m", "lantern_worker", "run-job", run_id]


def supervise(run_id: str, settings: Settings, db: Database) -> int:
    proc = subprocess.Popen(job_command(run_id, settings), start_new_session=True)  # noqa: S603
    try:
        code = proc.wait(timeout=settings.run_timeout_s)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        if settings.job_isolation == "container":
            subprocess.run(  # noqa: S603
                ["docker", "rm", "-f", f"lantern-job-{run_id}"],  # noqa: S607
                capture_output=True,
                check=False,
            )
        mark_failed(db, run_id, None, f"timed out after {settings.run_timeout_s}s")
        return -1
    if code != 0:
        mark_failed(db, run_id, None, f"job process exited with status {code}")
    return code


def sweep(settings: Settings, db: Database) -> None:
    """One retention sweep; a failure is logged, never fatal to the worker."""
    try:
        result = purge_expired(
            db,
            settings.retention_hours,
            run_timeout=dt.timedelta(seconds=settings.run_timeout_s),
        )
        log.info("retention sweep: %s", result.to_dict())
    except Exception:
        log.exception("retention sweep failed")


def serve(
    settings: Settings,
    queue: JobQueue,
    *,
    clock: Callable[[], float] = time.monotonic,
    iterations: int | None = None,
) -> None:
    db = Database(settings.database_url)
    db.create_all()
    log.info("worker consuming the run queue")
    next_sweep = clock()
    while iterations is None or iterations > 0:
        if iterations is not None:
            iterations -= 1
        if settings.retention_hours > 0 and clock() >= next_sweep:
            sweep(settings, db)
            next_sweep = clock() + SWEEP_INTERVAL.total_seconds()
        run_id = queue.dequeue(timeout=5)
        if run_id:
            log.info("run %s: start", run_id)
            code = supervise(run_id, settings, db)
            log.info("run %s: exit %s", run_id, code)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(prog="lantern_worker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    sub.add_parser("purge")
    job = sub.add_parser("run-job")
    job.add_argument("run_id")
    job.add_argument("--workdir", type=Path, default=None)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "serve":
        serve(settings, RedisQueue(settings.redis_url))
        return 0
    if args.command == "purge":
        db = Database(settings.database_url)
        db.create_all()
        sweep(settings, db)
        return 0
    deps = build_deps(settings)
    deps.workdir_root = args.workdir
    status = process_run(args.run_id, deps)
    return 0 if status in ("completed", "failed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
