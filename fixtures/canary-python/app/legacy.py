"""Contact sync for the pre-2023 CRM integration. Superseded by app.partners."""

import httpx
from sqlalchemy.orm import Session

from app.models import User

LEGACY_CRM_URL = "https://legacy-crm.partner.example/api/import"


def sync_all_contacts_to_legacy_crm(session: Session) -> int:
    sent = 0
    for user in session.query(User).all():
        httpx.post(LEGACY_CRM_URL, json={"email": user.email, "name": user.full_name}, timeout=10)
        sent += 1
    return sent
