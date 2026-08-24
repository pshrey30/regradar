"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_dotenv_fallback(monkeypatch: pytest.MonkeyPatch):
    """Never let a real local .env file leak into test outcomes.

    Settings.model_config sets env_file=".env" so the real app can run with
    just a .env file and no exported env vars. But that means any test that
    simulates "this setting isn't configured" via monkeypatch.delenv(...)
    still silently sees the developer's real .env value as a fallback —
    delenv only clears os.environ, it doesn't disable pydantic-settings'
    dotenv read. On a machine with a populated .env (e.g. real Slack/
    SendGrid credentials for AGENT-10), that turns "not configured" tests
    into false failures/passes depending on what's in that file. Force
    env_file=None for the whole test session so only explicit
    monkeypatch.setenv/os.environ values (and field defaults) apply,
    matching the isolation tests/unit/test_config.py already gets for free
    via its own explicit Settings(_env_file=None) construction.
    """
    from regradar.core.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)


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
