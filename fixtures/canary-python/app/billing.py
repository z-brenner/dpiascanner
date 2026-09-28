import stripe

from app.config import settings
from app.models import User

stripe.api_key = settings.stripe_api_key
stripe.api_base = settings.stripe_api_base


def create_billing_customer(user: User, payment_method_id: str) -> str:
    customer = stripe.Customer.create(
        name=user.full_name,
        email=user.email,
        payment_method=payment_method_id,
        invoice_settings={"default_payment_method": payment_method_id},
    )
    return str(customer.id)
