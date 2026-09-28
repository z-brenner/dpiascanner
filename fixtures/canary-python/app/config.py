"""Runtime configuration. Environment variables win over config/settings.yaml."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_CONFIG_FILE = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


def _file_config() -> dict[str, Any]:
    if _CONFIG_FILE.exists():
        return yaml.safe_load(_CONFIG_FILE.read_text()) or {}
    return {}


@dataclass(frozen=True)
class Settings:
    database_url: str
    mixpanel_token: str
    mixpanel_events_url: str
    sentry_dsn: str
    stripe_api_key: str
    stripe_api_base: str
    weather_api_url: str
    partner_api_url: str
    s3_bucket: str
    s3_region: str
    s3_endpoint_url: str | None


def load_settings() -> Settings:
    file_cfg = _file_config()
    s3_cfg = file_cfg.get("s3", {})
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://canary:canary@localhost:5432/canary"
        ),
        mixpanel_token=os.environ.get("MIXPANEL_TOKEN", "canary-project-token"),
        mixpanel_events_url=os.environ.get("MIXPANEL_EVENTS_URL", "https://api.mixpanel.com/track"),
        sentry_dsn=os.environ.get("SENTRY_DSN", "https://publickey@o0.ingest.sentry.io/0"),
        stripe_api_key=os.environ.get("STRIPE_API_KEY", ""),
        stripe_api_base=os.environ.get("STRIPE_API_BASE", "https://api.stripe.com"),
        weather_api_url=os.environ.get(
            "WEATHER_API_URL", "https://api.open-weather-data.example/v1/forecast"
        ),
        partner_api_url=os.environ.get(
            "PARTNER_API_URL", "https://hooks.partner-crm.example/v2/contacts"
        ),
        s3_bucket=os.environ.get("S3_BUCKET", s3_cfg.get("bucket", "canary-user-exports")),
        s3_region=os.environ.get("S3_REGION", s3_cfg.get("region", "eu-west-1")),
        s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
    )


settings = load_settings()
