"""Drive every reachable route once with synthetic canary values.

Third-party endpoints are configured through environment variables (see lantern.yml).
Point them at a recording mock server before running this script.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CANARY = {
    "email": os.environ.get("LANTERN_CANARY_EMAIL", "lantern.canary+7f3a@example.com"),
    "name": os.environ.get("LANTERN_CANARY_NAME", "Lantern Canary"),
    "phone": os.environ.get("LANTERN_CANARY_PHONE", "+1-202-555-0147"),
    "ssn": os.environ.get("LANTERN_CANARY_SSN", "987-65-4321"),
    "dob": os.environ.get("LANTERN_CANARY_DOB", "1987-06-05"),
    "lat": float(os.environ.get("LANTERN_CANARY_LAT", "47.620422")),
    "lng": float(os.environ.get("LANTERN_CANARY_LNG", "-122.349358")),
    "condition": os.environ.get("LANTERN_CANARY_CONDITION", "canary-condition-asthma"),
    "payment_method": os.environ.get("LANTERN_CANARY_PAYMENT_METHOD", "pm_lantern_canary"),
    "referee_email": os.environ.get("LANTERN_CANARY_EMAIL_2", "lantern.referee+7f3a@example.com"),
}


def main() -> int:
    import sentry_sdk
    from fastapi.testclient import TestClient

    from app.main import app

    failures = []
    with TestClient(app) as client:
        signup = {
            "email": CANARY["email"],
            "full_name": CANARY["name"],
            "date_of_birth": CANARY["dob"],
            "plan": "pro",
            "payment_method_id": CANARY["payment_method"],
        }
        calls = [
            ("POST", "/users", {"json": signup}, 201),
            ("POST", "/users", {"json": signup}, 409),
            ("POST", "/identity/verify", {"json": {"ssn": CANARY["ssn"]}}, 202),
            (
                "POST",
                "/location/forecast",
                {"json": {"latitude": CANARY["lat"], "longitude": CANARY["lng"]}},
                200,
            ),
            (
                "POST",
                "/health/intake",
                {"data": {"user_id": "1", "condition": CANARY["condition"]}},
                201,
            ),
            (
                "POST",
                "/referrals",
                {"json": {"referrer_id": 1, "referee_email": CANARY["referee_email"]}},
                201,
            ),
            ("POST", "/partners/sync", {"json": {"phone": CANARY["phone"]}}, 202),
            ("POST", "/users/1/export", {}, 202),
        ]
        for method, path, kwargs, expected in calls:
            response = client.request(method, path, **kwargs)
            status = "ok" if response.status_code == expected else "UNEXPECTED"
            print(f"{status} {method} {path} -> {response.status_code}")
            if response.status_code != expected:
                failures.append((path, response.status_code, response.text[:200]))
    sentry_sdk.flush(timeout=5)
    for failure in failures:
        print("failure:", failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
