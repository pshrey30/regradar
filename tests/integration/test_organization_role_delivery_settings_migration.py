"""Live-Postgres verification that migration 0025 is reversible."""

import subprocess

import pytest

pytestmark = pytest.mark.live


def _run_alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], check=True)


def test_0025_upgrade_downgrade_upgrade_cycle() -> None:
    _run_alembic("upgrade", "0025")
    _run_alembic("downgrade", "0024")
    _run_alembic("upgrade", "0025")
