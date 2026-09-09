"""A dedicated (non-pooled) asyncpg connection for Postgres LISTEN/NOTIFY.

SQLAlchemy's async engine pool isn't a fit for LISTEN: a pooled connection
can be handed back and reused for an unrelated query at any moment, which
would silently drop the subscription. Each caller of `listen` gets its own
short-lived asyncpg connection instead, closed when the listener exits.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from regradar.core.config import get_settings


def _asyncpg_dsn() -> str:
    # asyncpg.connect() doesn't understand SQLAlchemy's "+asyncpg" dialect
    # suffix on the URL scheme.
    settings = get_settings()
    return settings.effective_app_database_url.get_secret_value().replace(
        "postgresql+asyncpg://", "postgresql://"
    )


@asynccontextmanager
async def listen(channel: str) -> AsyncIterator[asyncio.Queue[str]]:
    """Yield a queue of raw NOTIFY payloads received on `channel` while the
    context manager is open."""
    queue: asyncio.Queue[str] = asyncio.Queue()
    connection = await asyncpg.connect(_asyncpg_dsn())

    def _on_notify(_connection: object, _pid: int, _channel: str, payload: str) -> None:
        queue.put_nowait(payload)

    await connection.add_listener(channel, _on_notify)
    try:
        yield queue
    finally:
        await connection.remove_listener(channel, _on_notify)
        await connection.close()
