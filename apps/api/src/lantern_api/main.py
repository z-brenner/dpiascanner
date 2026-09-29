"""ASGI entry point: ``uvicorn lantern_api.main:app``."""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from lantern_api import auth, meta, repos, runs, webhooks
from lantern_api.services import Services
from lantern_platform.config import Settings
from lantern_platform.retention import SWEEP_INTERVAL, purge_expired

log = logging.getLogger("lantern.api")
PROXY_HEADER = "X-Katz-Proxy-Secret"
OPEN_PATHS = frozenset({"/healthz"})


def sweep_once(services: Services) -> None:
    try:
        result = purge_expired(
            services.db,
            services.settings.retention_hours,
            run_timeout=dt.timedelta(seconds=services.settings.run_timeout_s),
        )
        log.info("retention sweep: %s", result.to_dict())
    except Exception:
        log.exception("retention sweep failed")


def _sweeper(services: Services, stop: threading.Event) -> None:
    while not stop.wait(SWEEP_INTERVAL.total_seconds()):
        sweep_once(services)


def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # With retention on, sweep before serving the first request (an instance that slept,
        # as free hosting tiers do, wakes up without expired data) and then every hour.
        stop = threading.Event()
        if services.settings.retention_hours > 0:
            sweep_once(services)
            threading.Thread(target=_sweeper, args=(services, stop), daemon=True).start()
        yield
        stop.set()

    app = FastAPI(title="Katz API", version="0.1.0", lifespan=lifespan)
    app.state.services = services
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[services.settings.web_url],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )
    secret = services.settings.proxy_secret
    if secret:

        @app.middleware("http")
        async def require_proxy(
            request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            # The API's own hostname may be public (Render); only the web app's proxy, which
            # adds this header, may use it. Health checks come from the host itself.
            given = request.headers.get(PROXY_HEADER, "").encode()
            if request.url.path not in OPEN_PATHS and not hmac.compare_digest(
                given, secret.encode()
            ):
                return JSONResponse({"detail": "not found"}, status_code=404)
            return await call_next(request)

    for router in (auth.router, meta.router, repos.router, runs.router, webhooks.router):
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
