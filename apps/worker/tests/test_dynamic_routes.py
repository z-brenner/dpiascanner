import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lantern_worker.dynamic.canaries import CanarySet
from lantern_worker.dynamic.plan import PORT, DynamicSettings, Endpoint, plan_steps
from lantern_worker.dynamic.routes import (
    ValueFactory,
    drive,
    kind_for_field,
    requests_from_openapi,
    requests_from_static,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures"


@pytest.fixture(scope="module")
def values() -> ValueFactory:
    return ValueFactory(CanarySet.default().values)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("email", "email"),
        ("refereeEmail", "email"),
        ("phone_number", "phone"),
        ("ssn", "ssn"),
        ("dateOfBirth", "dob"),
        ("latitude", "lat"),
        ("lng", "lng"),
        ("condition", "condition"),
        ("payment_method_id", "payment_method"),
        ("full_name", "name"),
        ("firstName", "name"),
        ("product_name", None),
        ("plan", None),
    ],
)
def test_field_kinds(name, kind):
    assert kind_for_field(name) == kind


SPEC = {
    "openapi": "3.1.0",
    "paths": {
        "/users": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Signup"}}
                    }
                }
            }
        },
        "/users/{user_id}/export": {
            "post": {
                "parameters": [{"name": "user_id", "in": "path", "schema": {"type": "integer"}}]
            }
        },
        "/intake": {
            "post": {
                "requestBody": {
                    "content": {
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "condition": {"type": "string"},
                                    "user_id": {"type": "string"},
                                },
                            }
                        }
                    }
                }
            }
        },
    },
    "components": {
        "schemas": {
            "Base": {
                "type": "object",
                "properties": {"email": {"type": "string", "format": "email"}},
            },
            "Signup": {
                "allOf": [
                    {"$ref": "#/components/schemas/Base"},
                    {
                        "type": "object",
                        "properties": {
                            "plan": {"enum": ["pro", "free"]},
                            "latitude": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                            "contact": {"type": "string", "format": "email"},
                        },
                    },
                ]
            },
        }
    },
}


def test_requests_from_openapi(values):
    reqs = {(r.method, r.path): r for r in requests_from_openapi(SPEC, values)}
    signup = reqs[("POST", "/users")]
    assert signup.body_kind == "json"
    assert signup.body["email"] == "lantern.canary+7f3a@example.com"
    assert signup.body["contact"] == "lantern.canary+7f3a@example.com"  # by schema format
    assert signup.body["plan"] == "pro"
    assert signup.body["latitude"] == pytest.approx(47.620422)
    assert ("POST", "/users/1/export") in reqs
    intake = reqs[("POST", "/intake")]
    assert intake.body_kind == "form"
    assert intake.body == {"condition": "canary-condition-asthma", "user_id": "lantern"}


def test_static_routes_fall_back_to_a_file_prefix(values):
    routes = [
        {
            "method": "POST",
            "path": "/verify",
            "file": "app/routes/identity.py",
            "fields": [{"name": "ssn", "location": "body"}],
        },
        {"method": "POST", "path": "/users", "file": "src/routes/users.ts", "fields": []},
    ]
    verify, users = requests_from_static(routes, values)
    assert verify.path == "/verify"
    assert verify.fallback_paths == ["/identity/verify"]
    assert verify.body == {"ssn": "987-65-4321"}
    assert users.fallback_paths == []  # already carries its prefix


class _App(BaseHTTPRequestHandler):
    seen: list = []  # noqa: RUF012

    def _reply(self, status, body=b"{}"):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(200 if self.path == "/" else 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        type(self).seen.append((self.path, json.loads(self.rfile.read(length) or b"{}")))
        self._reply(202 if self.path == "/identity/verify" else 404)

    def log_message(self, *args):
        return


def test_drive_uses_static_routes_and_retries_with_the_prefix_guess():
    _App.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        plan = {
            "base_url": f"http://127.0.0.1:{server.server_address[1]}",
            "canaries": CanarySet.default().values,
            "static_routes": [
                {
                    "method": "POST",
                    "path": "/verify",
                    "file": "app/routes/identity.py",
                    "fields": [{"name": "ssn", "location": "body"}],
                }
            ],
            "passes": 1,
            "startup_timeout": 5,
        }
        report = drive(plan)
    finally:
        server.shutdown()
    assert report.discovery == "static"
    assert [(r.path, r.status, r.origin) for r in report.routes] == [
        ("/identity/verify", 202, "static+prefix-guess")
    ]
    assert ("/identity/verify", {"ssn": "987-65-4321"}) in _App.seen


def test_settings_and_plan_for_the_python_fixture():
    repo = FIXTURES / "canary-python"
    settings = DynamicSettings.load(repo)
    assert settings.run == "python scripts/exercise.py"
    assert Endpoint("SENTRY_DSN", "https://publickey@o0.ingest.sentry.io/0") in settings.endpoints
    steps = plan_steps(repo, settings)
    assert [s.mode for s in steps] == ["tests", "script", "routes"]
    assert steps[2].argv == (
        "python",
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        PORT,
    )


def test_settings_and_plan_for_the_typescript_fixture():
    repo = FIXTURES / "canary-typescript"
    settings = DynamicSettings.load(repo)
    assert Endpoint("STRIPE_API_BASE", "https://api.stripe.com", "port") in settings.endpoints
    steps = plan_steps(repo, settings)
    assert [(s.mode, s.argv[:2]) for s in steps] == [
        ("tests", ("npm", "test")),
        ("script", ("npx", "tsx")),
        ("routes", ("npm", "start")),
    ]


def test_plan_without_lantern_yml(tmp_path):
    (tmp_path / "service.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
    steps = plan_steps(tmp_path, DynamicSettings.load(tmp_path))
    assert [s.mode for s in steps] == ["routes"]
    assert steps[0].argv[:5] == ("python", "-m", "flask", "--app", "service:app")
