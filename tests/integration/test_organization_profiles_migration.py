"""Live-Postgres verification that migration 0024 is reversible, matching
this project's established migration-testing convention."""

import subprocess

import pytest

pytestmark = pytest.mark.live


def _run_alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], check=True)


def test_0024_upgrade_downgrade_upgrade_cycle() -> None:
    _run_alembic("upgrade", "0024")
    _run_alembic("downgrade", "0023")
    _run_alembic("upgrade", "0024")
