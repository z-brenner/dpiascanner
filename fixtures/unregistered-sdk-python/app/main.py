import os

from acme_geo import AcmeGeoClient
from fastapi import FastAPI
from pydantic import BaseModel
from tiny_utils import slugify

app = FastAPI(title="unregistered-sdk-python")
geo = AcmeGeoClient(os.environ.get("ACME_GEO_KEY", ""))


class NearbyRequest(BaseModel):
    latitude: float
    longitude: float
    venue_name: str


@app.post("/nearby")
def nearby(payload: NearbyRequest) -> dict[str, object]:
    geo.ping()
    result = geo.locate(payload.latitude, payload.longitude)
    return {"slug": slugify(payload.venue_name), "area": result.get("area")}
