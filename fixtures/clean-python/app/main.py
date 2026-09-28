from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import Base, engine
from app.observability import init_error_reporting
from app.routes import products

init_error_reporting()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="clean-python", lifespan=lifespan)
app.include_router(products.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
