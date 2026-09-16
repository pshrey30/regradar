"""Live-Postgres verification that migration 0026 is reversible."""

import subprocess

import pytest

pytestmark = pytest.mark.live


def _run_alembic(*args: str) -> None:
    subprocess.run(["alembic", *args], check=True)


def test_0026_upgrade_downgrade_upgrade_cycle() -> None:
    _run_alembic("upgrade", "0026")
    _run_alembic("downgrade", "0025")
    _run_alembic("upgrade", "0026")
