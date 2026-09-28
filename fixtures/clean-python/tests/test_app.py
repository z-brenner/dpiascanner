from typing import Any

LAMP = {
    "sku": "LAMP-001",
    "name": "Brass desk lamp",
    "category": "lighting",
    "country_of_origin": "PT",
    "price_cents": 4900,
}


def test_create_and_get(client: Any) -> None:
    assert client.post("/products", json=LAMP).status_code == 201
    assert client.get("/products/LAMP-001").json()["name"] == "Brass desk lamp"


def test_quote_converts_currency(client: Any) -> None:
    client.post("/products", json=LAMP)
    assert client.get("/products/LAMP-001/quote", params={"currency": "eur"}).json()["amount"] == 24.5


def test_missing_product(client: Any) -> None:
    assert client.get("/products/NOPE").status_code == 404
