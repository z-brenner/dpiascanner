from fastapi import FastAPI

from app.invites import router as invites_router
from app.routes import router

app = FastAPI(title="gaps-python")
app.include_router(router)
app.include_router(invites_router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
