"""FastAPI application entrypoint.

Minimal placeholder — the real app factory, DB/Redis connectivity checks,
request-ID middleware, and OpenAPI configuration are implemented in API-01.
This stub exists only so the `api` service in Docker Compose (FOUND-03) has
something real to run and health-check against.
"""

from fastapi import FastAPI

app = FastAPI(title="RegRadar")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
