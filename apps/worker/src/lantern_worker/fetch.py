"""Fetch a repository at one commit into a fresh directory.

``GitFetcher`` makes a depth-1 fetch of exactly the run's SHA. The installation token goes
to git through ``GIT_CONFIG_*`` environment variables as an HTTP header, so it never appears
in a command line, a remote URL, or ``.git/config``; the ``.git`` directory is deleted after
checkout anyway. The checkout is measured against the disk cap before analysis starts.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from lantern_analysis.secrets import redact
from lantern_platform.egress import EgressPolicy
from lantern_platform.github import GitHub


class FetchError(RuntimeError):
    pass


class RepoFetcher(Protocol):
    def fetch(self, full_name: str, sha: str, installation_id: int | None, dest: Path) -> None: ...


def dir_size_mb(path: Path) -> float:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total / (1024 * 1024)


class GitFetcher:
    def __init__(
        self,
        policy: EgressPolicy,
        github: GitHub | None = None,
        web_url: str = "https://github.com",
        max_mb: int = 500,
        timeout_s: float = 600,
    ) -> None:
        self.policy = policy
        self.github = github
        self.web_url = web_url.rstrip("/")
        self.max_mb = max_mb
        self.timeout_s = timeout_s

    def clone_url(self, full_name: str) -> str:
        return f"{self.web_url}/{full_name}.git"

    def _env(self, url: str, installation_id: int | None, home: Path) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(home),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_LFS_SKIP_SMUDGE": "1",
        }
        for key in (
            "HTTPS_PROXY",
            "https_proxy",
            "NO_PROXY",
            "no_proxy",
            "SSL_CERT_FILE",
            "GIT_SSL_CAINFO",
        ):
            if key in os.environ:
                env[key] = os.environ[key]
        if installation_id is not None and self.github is not None:
            token = self.github.installation_token(installation_id)
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            parts = urlsplit(url)
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"http.{parts.scheme}://{parts.netloc}/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
                }
            )
        return env

    def _git(self, args: list[str], env: dict[str, str], cwd: Path) -> None:
        try:
            proc = subprocess.run(  # noqa: S603
                ["git", *args],  # noqa: S607
                cwd=cwd,
                env=env,
                capture_output=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FetchError(f"git {args[0]} timed out after {self.timeout_s:.0f}s") from exc
        if proc.returncode != 0:
            raise FetchError(
                f"git {args[0]} failed: {redact(proc.stderr.decode('utf-8', 'replace'))[-500:]}"
            )

    def fetch(self, full_name: str, sha: str, installation_id: int | None, dest: Path) -> None:
        url = self.clone_url(full_name)
        self.policy.check_url(url)
        dest.mkdir(parents=True, exist_ok=False)
        home = dest.parent / ".git-home"
        home.mkdir(exist_ok=True)
        env = self._env(url, installation_id, home)
        self._git(["init", "--quiet"], env, dest)
        self._git(["fetch", "--quiet", "--depth", "1", "--no-tags", url, sha], env, dest)
        self._git(
            ["-c", "advice.detachedHead=false", "checkout", "--quiet", "FETCH_HEAD"], env, dest
        )
        shutil.rmtree(dest / ".git", ignore_errors=True)
        shutil.rmtree(home, ignore_errors=True)
        size = dir_size_mb(dest)
        if size > self.max_mb:
            raise FetchError(f"checkout is {size:.0f} MB, over the {self.max_mb} MB cap")


class LocalFetcher:
    """Copies a local directory. For tests and for scanning a path you already trust."""

    def __init__(self, source: Path) -> None:
        self.source = source

    def fetch(self, full_name: str, sha: str, installation_id: int | None, dest: Path) -> None:
        shutil.copytree(
            self.source,
            dest,
            ignore=shutil.ignore_patterns(".git", "node_modules", ".venv", "__pycache__"),
        )
