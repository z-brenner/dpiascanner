import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    fx_api_url: str
    sentry_dsn: str
    default_currency: str


def load_settings() -> Settings:
    return Settings(
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://catalog:catalog@localhost:5432/catalog"
        ),
        fx_api_url=os.environ.get("FX_API_URL", "https://api.fx-rates.example/v1/latest"),
        sentry_dsn=os.environ.get("SENTRY_DSN", "https://publickey@o0.ingest.sentry.io/0"),
        default_currency=os.environ.get("DEFAULT_CURRENCY", "USD"),
    )


settings = load_settings()
