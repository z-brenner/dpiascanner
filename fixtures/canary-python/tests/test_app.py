from typing import Any


def test_healthz(client: Any) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_signup_then_duplicate(client: Any) -> None:
    body = {"email": "a@example.com", "full_name": "A", "date_of_birth": "1990-01-02"}
    assert client.post("/users", json=body).status_code == 201
    assert client.post("/users", json=body).status_code == 409


def test_partner_sync_calls_out(client: Any, received: list[dict[str, Any]]) -> None:
    assert client.post("/partners/sync", json={"phone": "+1-202-555-0100"}).status_code == 202
    assert any(r["path"] == "/contacts" for r in received)


def test_export_uploads_document(client: Any, received: list[dict[str, Any]]) -> None:
    body = {"email": "b@example.com", "full_name": "B", "date_of_birth": "1990-01-02"}
    user_id = client.post("/users", json=body).json()["id"]
    assert client.post(f"/users/{user_id}/export").status_code == 202
    assert any(r["method"] == "PUT" for r in received)
