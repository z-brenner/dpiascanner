from mixpanel import Consumer, Mixpanel

from app.config import settings

_mixpanel = Mixpanel(
    settings.mixpanel_token,
    consumer=Consumer(events_url=settings.mixpanel_events_url),
)


def track_signup(user_id: int, email: str, plan: str) -> None:
    _mixpanel.track(str(user_id), "Signed Up", {"email": email, "plan": plan})
