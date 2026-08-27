"""Unit tests for EVAL-01's harness control flow — retrieval/synthesis/ragas
scoring and the fixture corpus seed/delete step are all mocked here; a real
end-to-end run (real Postgres, real local Ollama) is verified separately,
not by the automated unit suite (matches this project's established
pattern for every other Ollama-dependent code path)."""

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from regradar.agents.state import RetrievedChunk
from regradar.evaluation import harness as harness_module
from regradar.evaluation.dataset import EVAL_CASES
from regradar.models.enums import EvalRunType


@dataclass
class _FakeMetricResult:
    value: float


class _FakeMetric:
    """Stands in for ragas' Faithfulness/ContextRecall — same async
    .ascore(...) -> object-with-.value contract, fixed score."""

    def __init__(self, score: float):
        self._score = score

    async def ascore(self, **kwargs) -> _FakeMetricResult:
        return _FakeMetricResult(value=self._score)


def _patch_db(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(harness_module, "get_session_factory", lambda: mock_session_factory)
    return mock_db


def _patch_common(
    monkeypatch: pytest.MonkeyPatch,
    *,
    faithfulness_score: float,
    context_recall_score: float,
    chunks_per_case: list[RetrievedChunk] | None = None,
    synthesized_answer: str | None = "A synthesized answer.",
) -> None:
    monkeypatch.setattr(harness_module, "_seed_fixture_corpus", AsyncMock(return_value={}))
    monkeypatch.setattr(harness_module, "_judge_llm", lambda: object())
    monkeypatch.setattr(
        harness_module, "Faithfulness", lambda llm: _FakeMetric(faithfulness_score)
    )
    monkeypatch.setattr(
        harness_module, "ContextRecall", lambda llm: _FakeMetric(context_recall_score)
    )
    monkeypatch.setattr(harness_module, "_current_git_commit_sha", lambda: "abc1234")

    default_chunks = (
        chunks_per_case
        if chunks_per_case is not None
        else [RetrievedChunk(filing_id=uuid.uuid4(), chunk_text="Some retrieved context.", score=0.9)]
    )
    monkeypatch.setattr(
        harness_module, "retrieve_similar_filings", AsyncMock(return_value=default_chunks)
    )
    monkeypatch.setattr(
        harness_module, "synthesize_answer", MagicMock(return_value=synthesized_answer)
    )


@pytest.mark.asyncio
async def test_run_eval_writes_averaged_scores_and_passes_above_thresholds(
    monkeypatch: pytest.MonkeyPatch,
):
    mock_db = _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.95, context_recall_score=0.9)

    run = await harness_module.run_eval(EvalRunType.MANUAL)

    assert run.ragas_faithfulness == pytest.approx(0.95)
    assert run.ragas_context_recall == pytest.approx(0.9)
    assert run.passed is True
    assert run.run_type == EvalRunType.MANUAL
    assert run.git_commit_sha == "abc1234"
    assert run.prompt_version == harness_module.PROMPT_VERSION

    inserted = mock_db.add.call_args[0][0]
    assert inserted.ragas_faithfulness == pytest.approx(0.95)

    # Regression check for a real bug found in live verification: the
    # seed/score transaction must be rolled back (never committed) so the
    # fixture rows never persist and set_rls_context's transaction-local
    # GUCs never revert mid-run — see _seed_fixture_corpus's docstring.
    mock_db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_eval_fails_when_scores_are_below_documented_thresholds(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.5, context_recall_score=0.5)

    run = await harness_module.run_eval(EvalRunType.SCHEDULED)

    assert run.passed is False


@pytest.mark.asyncio
async def test_run_eval_raises_when_every_case_fails_to_retrieve(monkeypatch: pytest.MonkeyPatch):
    _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.95, context_recall_score=0.9, chunks_per_case=[])

    with pytest.raises(RuntimeError, match="Every eval case failed"):
        await harness_module.run_eval(EvalRunType.MANUAL)


@pytest.mark.asyncio
async def test_run_eval_raises_when_every_case_fails_answer_synthesis(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.95, context_recall_score=0.9, synthesized_answer=None)

    with pytest.raises(RuntimeError, match="Every eval case failed"):
        await harness_module.run_eval(EvalRunType.MANUAL)


@pytest.mark.asyncio
async def test_run_eval_scores_every_fixture_case(monkeypatch: pytest.MonkeyPatch):
    """Each case gets its own real retrieve_similar_filings + synthesize_answer
    call — not just the first one."""
    _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.95, context_recall_score=0.9)

    await harness_module.run_eval(EvalRunType.MANUAL)

    assert harness_module.retrieve_similar_filings.await_count == len(EVAL_CASES)
    assert harness_module.synthesize_answer.call_count == len(EVAL_CASES)


def test_current_git_commit_sha_returns_none_on_subprocess_failure(monkeypatch: pytest.MonkeyPatch):
    import subprocess

    def _raise(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr(harness_module.subprocess, "run", _raise)

    assert harness_module._current_git_commit_sha() is None
