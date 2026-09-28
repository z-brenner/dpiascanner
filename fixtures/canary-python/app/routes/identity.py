import logging

from fastapi import APIRouter

from app.schemas import IdentityVerificationRequest
from app.security import encrypt_field

router = APIRouter(prefix="/identity", tags=["identity"])
logger = logging.getLogger("canary.identity")


@router.post("/verify", status_code=202)
def submit_identity_check(payload: IdentityVerificationRequest) -> dict[str, str]:
    token = encrypt_field(payload.ssn)
    logger.info("identity check queued document=%s token=%s", payload.document_type, token)
    return {"status": "queued"}
