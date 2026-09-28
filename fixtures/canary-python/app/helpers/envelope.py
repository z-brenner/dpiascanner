from dataclasses import dataclass


@dataclass(frozen=True)
class ContactRecord:
    email: str
    referred_by: int
    source: str


def build_contact_record(email: str, *, referred_by: int, source: str) -> ContactRecord:
    return ContactRecord(email=email, referred_by=referred_by, source=source)
