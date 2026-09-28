from fastapi import FastAPI

from app.routes import router

app = FastAPI(title="gaps-python")
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
