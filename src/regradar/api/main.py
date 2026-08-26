"""FastAPI application entrypoint: app factory, health check, and core middleware."""

import logging
from importlib.metadata import version

from fastapi import FastAPI, Response
from sqlalchemy import text

from regradar.api.errors import register_error_handlers
from regradar.api.middleware.request_id import RequestIdFilter, RequestIdMiddleware
from regradar.api.routers.filings import router as filings_router
from regradar.api.routers.metrics import router as metrics_router
from regradar.api.routers.webhooks import router as webhooks_router
from regradar.api.routers.whoami import router as whoami_router
from regradar.core.db import get_engine
from regradar.core.redis_client import get_redis_client

logging.getLogger().addFilter(RequestIdFilter())


async def _check_database() -> bool:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False


async def _check_redis() -> bool:
    try:
        return bool(await get_redis_client().ping())
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False


def create_app() -> FastAPI:
    app = FastAPI(title="RegRadar", version=version("regradar"), docs_url="/docs")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(whoami_router)
    app.include_router(filings_router)
    app.include_router(webhooks_router)
    app.include_router(metrics_router)

    @app.get("/health")
    async def health(response: Response) -> dict[str, str]:
        db_ok, redis_ok = await _check_database(), await _check_redis()
        if not (db_ok and redis_ok):
            response.status_code = 503
        return {
            "status": "ok" if db_ok and redis_ok else "error",
            "database": "ok" if db_ok else "unreachable",
            "redis": "ok" if redis_ok else "unreachable",
        }

    return app


app = create_app()
