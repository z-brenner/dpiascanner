from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.db import Base, engine
from app.errors import DuplicateAccountError
from app.observability import init_error_reporting
from app.routes import health, identity, location, partners, referrals, users

init_error_reporting()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="canary-python", lifespan=lifespan)
app.include_router(users.router)
app.include_router(identity.router)
app.include_router(location.router)
app.include_router(health.router)
app.include_router(referrals.router)
app.include_router(partners.router)


@app.exception_handler(DuplicateAccountError)
async def duplicate_account(request: Request, exc: DuplicateAccountError) -> JSONResponse:
    sentry_sdk.capture_exception(exc)
    return JSONResponse(status_code=409, content={"detail": "account already exists"})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
