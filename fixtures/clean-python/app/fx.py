from functools import lru_cache

import httpx

from app.config import settings


@lru_cache(maxsize=32)
def rate(base: str, target: str) -> float:
    response = httpx.get(
        settings.fx_api_url, params={"base": base, "symbols": target}, timeout=5.0
    )
    response.raise_for_status()
    return float(response.json().get("rates", {}).get(target, 1.0))
