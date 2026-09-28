from fastapi import APIRouter, Depends

from app.deps import get_referral_service
from app.schemas import ReferralRequest
from app.services.referral_service import ReferralService

router = APIRouter(prefix="/referrals", tags=["referrals"])


@router.post("", status_code=201)
def create_referral(
    payload: ReferralRequest, service: ReferralService = Depends(get_referral_service)
) -> dict[str, int]:
    contact_id = service.register(payload.referrer_id, payload.referee_email)
    return {"id": contact_id}
