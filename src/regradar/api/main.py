"""FastAPI application entrypoint: app factory, health check, and core middleware."""

import logging
from importlib.metadata import version

import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from sqlalchemy import text

from regradar.api.errors import register_error_handlers
from regradar.api.middleware.request_id import RequestIdFilter, RequestIdMiddleware
from regradar.api.routers.whoami import router as whoami_router
from regradar.core.config import get_settings
from regradar.core.db import get_engine

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
    client = aioredis.from_url(get_settings().redis_url.get_secret_value())
    try:
        return bool(await client.ping())
    except Exception:  # noqa: BLE001 — health check must degrade, not raise
        return False
    finally:
        await client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="RegRadar", version=version("regradar"), docs_url="/docs")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(whoami_router)

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
