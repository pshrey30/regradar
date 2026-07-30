"""SQLAlchemy declarative base and async engine/session factory.

Reads DATABASE_URL / DATABASE_POOL_SIZE directly from the environment for now.
Once FOUND-05 (core/config.py Settings) lands, this should source both from
the shared Settings object instead of os.environ directly.
"""

import os
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
        database_url = os.environ["DATABASE_URL"]
        pool_size = int(os.environ.get("DATABASE_POOL_SIZE", "10"))
        _engine = create_async_engine(database_url, pool_size=pool_size)
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
