"""Live end-to-end test for the EVAL-01/EVAL-02/EVAL-03/EVAL-04/EVAL-06 harness.

Marked `live` and excluded from default pytest runs (pyproject.toml's
addopts) — run it explicitly with `pytest -m live` when you actually want
to verify the real integration. Requires a real Postgres connection (via
.env's APP_DATABASE_URL), Ollama running locally with llama3.1 and
nomic-embed-text pulled, USE_LOCAL_LLM=true / USE_LOCAL_EMBEDDINGS=true in
.env, a real HUGGINGFACE_API_TOKEN configured (EVAL-03's alert scoring calls
the real Triage Agent, which classifies via HF's zero-shot endpoint), AND a
real LANGFUSE_PUBLIC_KEY/SECRET_KEY (EVAL-06 traces every real LLM call
through Langfuse's drop-in OpenAI client) — matches the same
manual-start/real-credential convention as
tests/unit/agents/test_triage_live_smoke.py. Never wire this into CI: this
project's cost/supervision policy is that even $0 local/free-tier calls
only run when a human explicitly asks for one, and this test's Postgres
writes are real (self-cleaning, but real).
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
    assert 0.0 <= run.rouge_l <= 1.0
    assert 0.0 <= run.extraction_f1 <= 1.0
    assert run.p99_latency_ms is not None
    assert run.p99_latency_ms >= 0
    # Precision/recall can legitimately be null if the confusion matrix's
    # denominator was zero (e.g. no predicted positives) — only assert the
    # range when the harness actually measured one.
    if run.alert_precision is not None:
        assert 0.0 <= run.alert_precision <= 1.0
    if run.alert_recall is not None:
        assert 0.0 <= run.alert_recall <= 1.0
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
            assert row.rouge_l == pytest.approx(run.rouge_l)
            assert row.extraction_f1 == pytest.approx(run.extraction_f1)
            assert row.p99_latency_ms == run.p99_latency_ms
            if run.alert_precision is None:
                assert row.alert_precision is None
            else:
                assert row.alert_precision == pytest.approx(run.alert_precision)
            if run.alert_recall is None:
                assert row.alert_recall is None
            else:
                assert row.alert_recall == pytest.approx(run.alert_recall)

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
