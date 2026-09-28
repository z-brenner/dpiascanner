import sentry_sdk

from app.config import settings


def init_error_reporting() -> None:
    sentry_sdk.init(dsn=settings.sentry_dsn, send_default_pii=False, traces_sample_rate=0.0)
