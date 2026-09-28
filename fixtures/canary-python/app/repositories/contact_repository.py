from sqlalchemy.orm import Session

from app.helpers.envelope import ContactRecord
from app.models import Contact


class ContactRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, record: ContactRecord) -> int:
        row = Contact(email=record.email, referred_by=record.referred_by, source=record.source)
        self._session.add(row)
        self._session.commit()
        return row.id
