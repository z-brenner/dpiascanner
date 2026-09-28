import requests

API_BASE = "https://api.acme-geo.example/v1"


class AcmeGeoClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def locate(self, lat: float, lng: float) -> dict:
        return self._post("/locate", {"lat": lat, "lng": lng})

    def ping(self) -> str:
        return "pong"

    def _post(self, path: str, body: dict) -> dict:
        response = requests.post(
            API_BASE + path,
            json=body,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=5,
        )
        return response.json()
