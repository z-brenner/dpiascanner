from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.repositories.contact_repository import ContactRepository
from app.services.referral_service import ReferralService


def get_contact_repository(session: Session = Depends(get_session)) -> ContactRepository:
    return ContactRepository(session)


def get_referral_service(
    repository: ContactRepository = Depends(get_contact_repository),
) -> ReferralService:
    return ReferralService(repository)
