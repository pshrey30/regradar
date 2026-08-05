"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _reset_db_engine_singleton():
    """Reset core.db's cached engine/session factory before and after each test.

    pytest-asyncio gives each test function its own event loop by default.
    core.db.get_engine() caches the SQLAlchemy async engine as a module-level
    singleton (correct for a real long-running process, which only ever has
    one event loop for its whole lifetime) — but reused across test functions
    it causes "Future attached to a different loop" errors, since the
    connection pool created in one test's loop can't be used from another
    test's loop. Resetting to None forces a fresh engine bound to the
    current test's loop on next use.
    """
    import regradar.core.db as db_module

    db_module._engine = None
    db_module._session_factory = None
    yield
    db_module._engine = None
    db_module._session_factory = None
