from typing import Any

from fastapi import APIRouter

from app.schemas import ForecastRequest
from app.weather import fetch_forecast

router = APIRouter(prefix="/location", tags=["location"])


@router.post("/forecast")
def forecast_for_device(payload: ForecastRequest) -> dict[str, Any]:
    return fetch_forecast(payload.latitude, payload.longitude, payload.units)
