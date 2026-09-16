"""Test fixtures for agents tests."""

import pytest


@pytest.fixture(autouse=True)
def _enable_env_file_for_live_tests(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """Allow env_file reading for live tests only, disabled for unit tests.

    The global conftest.py disables env_file to prevent .env leakage
    into unit tests. But live tests need real configuration to connect
    to actual services (database, redis, LLM server), so we re-enable it
    for tests marked with @pytest.mark.live.
    """
    from regradar.core.config import Settings

    if "live" in request.keywords:
        # Re-enable env_file for this live test
        monkeypatch.setitem(Settings.model_config, "env_file", ".env")
