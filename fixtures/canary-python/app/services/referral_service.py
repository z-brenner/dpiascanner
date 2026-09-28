from app.helpers.envelope import build_contact_record
from app.helpers.normalize import normalize_email
from app.repositories.contact_repository import ContactRepository


class ReferralService:
    def __init__(self, contacts: ContactRepository) -> None:
        self._contacts = contacts

    def register(self, referrer_id: int, referee_email: str) -> int:
        normalized = normalize_email(referee_email)
        record = build_contact_record(normalized, referred_by=referrer_id, source="referral")
        return self._contacts.save(record)
