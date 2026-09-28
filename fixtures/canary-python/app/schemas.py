from datetime import date

from pydantic import BaseModel


class SignupRequest(BaseModel):
    email: str
    full_name: str
    date_of_birth: date
    plan: str = "free"
    payment_method_id: str | None = None


class IdentityVerificationRequest(BaseModel):
    ssn: str
    document_type: str = "ssn"


class ForecastRequest(BaseModel):
    latitude: float
    longitude: float
    units: str = "metric"


class ReferralRequest(BaseModel):
    referrer_id: int
    referee_email: str


class PartnerSyncRequest(BaseModel):
    phone: str
    marketing_opt_in: bool = False
