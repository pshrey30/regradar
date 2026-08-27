"""Live end-to-end test for EVAL-01's Ragas harness.

Marked `live` and excluded from default pytest runs (pyproject.toml's
addopts) — run it explicitly with `pytest -m live` when you actually want
to verify the real integration. Requires a real Postgres connection (via
.env's APP_DATABASE_URL) AND Ollama running locally with llama3.1 and
nomic-embed-text pulled, USE_LOCAL_LLM=true / USE_LOCAL_EMBEDDINGS=true in
.env (matches the same manual-start convention as
tests/unit/agents/test_triage_live_smoke.py's spot-check test). Never wire
this into CI: this project's cost/supervision policy is that even $0 local
LLM calls only run when a human explicitly asks for one, and this test's
Postgres writes are real (self-cleaning, but real).
"""

from pathlib import Path

import pytest
from dotenv import dotenv_values
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from regradar.core.config import get_settings
from regradar.core.db import set_rls_context
from regradar.evaluation.harness import _FIXTURE_SOURCE_DOCUMENT_PREFIX, run_eval
from regradar.models.enums import EvalRunType
from regradar.models.eval_run import EvalRun
from regradar.models.filing import Filing

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _load_real_env_for_settings(monkeypatch: pytest.MonkeyPatch):
    """Same pattern as tests/integration/test_row_level_security.py — see
    that file's fixture docstring for why this is needed."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    for key, value in dotenv_values(env_path).items():
        if value is not None:
            monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_run_eval_writes_a_real_eval_runs_row_scored_by_a_real_llm():
    run = await run_eval(EvalRunType.MANUAL)

    assert run.id is not None
    assert 0.0 <= run.ragas_faithfulness <= 1.0
    assert 0.0 <= run.ragas_context_recall <= 1.0
    assert run.git_commit_sha is not None

    settings = get_settings()
    engine = create_async_engine(settings.effective_app_database_url.get_secret_value())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            row = (
                await db.execute(select(EvalRun).where(EvalRun.id == run.id))
            ).scalar_one()
            assert row.ragas_faithfulness == pytest.approx(run.ragas_faithfulness)

            # No fixture data should survive the run.
            leftover_filings = (
                await db.execute(
                    select(Filing.id).where(
                        Filing.source_document_id.like(f"{_FIXTURE_SOURCE_DOCUMENT_PREFIX}%")
                    )
                )
            ).scalars().all()
            assert leftover_filings == []
    finally:
        async with session_factory() as db:
            await set_rls_context(db, role="service")
            await db.execute(delete(EvalRun).where(EvalRun.id == run.id))
            await db.commit()
        await engine.dispose()
