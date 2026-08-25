"""SQLAlchemy declarative base and async engine/session factory."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all RegRadar ORM models."""


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        # Imported lazily, not at module load: regradar.core.config imports
        # regradar.models.enums, and importing regradar.models eagerly loads
        # every ORM model module (including this one) — a top-level import
        # here would deadlock that cycle the moment config.py is the first
        # thing anything imports (e.g. `uvicorn regradar.api.main:app`).
        from regradar.core.config import get_settings

        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped database session."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        yield session
