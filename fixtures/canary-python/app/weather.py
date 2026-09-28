from typing import Any

import httpx

from app.config import settings


def fetch_forecast(lat: float, lng: float, units: str) -> dict[str, Any]:
    response = httpx.get(
        settings.weather_api_url,
        params={"lat": lat, "lon": lng, "units": units},
        timeout=5.0,
    )
    response.raise_for_status()
    return dict(response.json())
