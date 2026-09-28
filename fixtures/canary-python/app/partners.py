from typing import Any

import httpx

from app import constants
from app.config import settings


def push_contact(profile: dict[str, Any]) -> int:
    contact_value = profile[constants.PARTNER_CONTACT_FIELD]
    response = httpx.post(
        settings.partner_api_url,
        json={"contact": contact_value, "source": "canary-app"},
        timeout=constants.PARTNER_TIMEOUT_SECONDS,
    )
    return response.status_code
