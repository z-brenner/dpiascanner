"""Settings for the API and worker, read from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_EGRESS = (
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "api.anthropic.com",
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _private_key() -> str:
    """GITHUB_APP_PRIVATE_KEY holds the PEM itself; literal \\n sequences are accepted."""
    key = _env("GITHUB_APP_PRIVATE_KEY")
    if not key and _env("GITHUB_APP_PRIVATE_KEY_PATH"):
        with open(_env("GITHUB_APP_PRIVATE_KEY_PATH"), encoding="utf-8") as fh:
            key = fh.read()
    return key.replace("\\n", "\n")


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///lantern.db"
    redis_url: str = ""
    github_app_id: str = ""
    github_app_private_key: str = ""
    github_webhook_secret: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_app_slug: str = "lantern-dpia"
    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    token_key: str = ""  # Fernet key for tokens at rest
    web_url: str = "http://localhost:5173"
    runs_per_hour: int = 10
    run_timeout_s: int = 1800
    clone_max_mb: int = 500
    job_isolation: str = "process"  # process | container
    worker_image: str = "lantern-worker:latest"
    egress_allowlist: tuple[str, ...] = field(default=DEFAULT_EGRESS)
    session_ttl_hours: int = 24 * 7
    cookie_secure: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        extra = tuple(h.strip() for h in _env("LANTERN_EGRESS_ALLOWLIST").split(",") if h.strip())
        return cls(
            database_url=_env(
                "DATABASE_URL", "postgresql+psycopg://lantern:lantern@localhost:5432/lantern"
            ),
            redis_url=_env("REDIS_URL", "redis://localhost:6379/0"),
            github_app_id=_env("GITHUB_APP_ID"),
            github_app_private_key=_private_key(),
            github_webhook_secret=_env("GITHUB_WEBHOOK_SECRET"),
            github_client_id=_env("GITHUB_CLIENT_ID"),
            github_client_secret=_env("GITHUB_CLIENT_SECRET"),
            github_app_slug=_env("GITHUB_APP_SLUG", "lantern-dpia"),
            github_api_url=_env("GITHUB_API_URL", "https://api.github.com"),
            github_web_url=_env("GITHUB_WEB_URL", "https://github.com"),
            token_key=_env("LANTERN_TOKEN_KEY"),
            web_url=_env("LANTERN_WEB_URL", "http://localhost:5173"),
            runs_per_hour=int(_env("LANTERN_RUNS_PER_HOUR", "10")),
            run_timeout_s=int(_env("LANTERN_RUN_TIMEOUT_S", "1800")),
            clone_max_mb=int(_env("LANTERN_CLONE_MAX_MB", "500")),
            job_isolation=_env("LANTERN_JOB_ISOLATION", "process"),
            worker_image=_env("LANTERN_WORKER_IMAGE", "lantern-worker:latest"),
            egress_allowlist=DEFAULT_EGRESS + extra,
            cookie_secure=_env("LANTERN_COOKIE_SECURE", "1") != "0",
        )
