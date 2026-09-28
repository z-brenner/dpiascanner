"""ASGI entry point: ``uvicorn lantern_api.main:app``."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from lantern_api import auth, repos, runs, webhooks
from lantern_api.services import Services
from lantern_platform.config import Settings


def create_app(services: Services) -> FastAPI:
    app = FastAPI(title="Lantern API", version="0.1.0")
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[services.settings.web_url],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    for router in (auth.router, repos.router, runs.router, webhooks.router):
        app.include_router(router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _app_from_env() -> FastAPI:
    return create_app(Services.build(Settings.from_env()))


def __getattr__(name: str) -> FastAPI:
    # Build the app lazily so importing this module (tests, tooling) needs no configuration.
    if name == "app":
        return _app_from_env()
    raise AttributeError(name)
