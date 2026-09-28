"""Drive every route once. Third-party endpoints come from environment variables."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    import sentry_sdk
    from fastapi.testclient import TestClient

    from app.main import app

    failures = []
    with TestClient(app) as client:
        product = {
            "sku": "LAMP-001",
            "name": "Brass desk lamp",
            "category": "lighting",
            "condition": "refurbished",
            "country_of_origin": "PT",
            "price_cents": 4900,
        }
        calls = [
            ("POST", "/products", {"json": product, "headers": {"x-request-id": "r-1"}}, 201),
            ("GET", "/products", {"params": {"category": "lighting"}}, 200),
            ("GET", "/products/LAMP-001", {}, 200),
            ("GET", "/products/LAMP-001/quote", {"params": {"currency": "eur"}}, 200),
            ("GET", "/products/MISSING", {}, 404),
            ("GET", "/healthz", {}, 200),
        ]
        for method, path, kwargs, expected in calls:
            response = client.request(method, path, **kwargs)
            ok = response.status_code == expected
            print(f"{'ok' if ok else 'UNEXPECTED'} {method} {path} -> {response.status_code}")
            if not ok:
                failures.append((path, response.status_code))
    sentry_sdk.flush(timeout=5)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
