from fastapi import APIRouter

from app.partners import push_contact
from app.schemas import PartnerSyncRequest

router = APIRouter(prefix="/partners", tags=["partners"])


@router.post("/sync", status_code=202)
def sync_partner(payload: PartnerSyncRequest) -> dict[str, int]:
    profile = {"phone": payload.phone, "opt_in": payload.marketing_opt_in}
    return {"status": push_contact(profile)}
