from typing import Any

import sentry_sdk

from app.config import settings


def _scrub_user_pii(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any]:
    user = event.get("user")
    if user:
        user.pop("email", None)
        user.pop("ip_address", None)
    return event


def init_error_reporting() -> None:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        before_send=_scrub_user_pii,
        send_default_pii=False,
        traces_sample_rate=0.0,
    )


def identify_user(user_id: int, email: str) -> None:
    sentry_sdk.set_user({"id": str(user_id), "email": email})
