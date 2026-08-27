"""Unit tests for the EVAL-01/EVAL-02/EVAL-03 harness control flow —
retrieval/synthesis/ragas scoring, summarize_node/ROUGE-L scoring,
triage_node/alert scoring, and the fixture corpus seed/rollback step are
all mocked here; a real end-to-end run (real Postgres, real local Ollama,
real HuggingFace) is verified separately, not by the automated unit suite
(matches this project's established pattern for every other
Ollama/HF-dependent code path)."""

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from regradar.agents.state import RetrievedChunk
from regradar.evaluation import harness as harness_module
from regradar.evaluation.dataset import (
    ALERT_EVAL_CASES,
    SEARCH_EVAL_CASES,
    SUMMARIZATION_EVAL_CASES,
)
from regradar.evaluation.harness import _AlertCaseResult
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
    rouge_l_score: float | None = 0.9,
    alert_all_fail: bool = False,
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
    monkeypatch.setattr(
        harness_module, "_score_summarization_case", MagicMock(return_value=rouge_l_score)
    )

    # Default: every alert case is correctly predicted (precision=recall=1.0).
    alert_result = (
        None if alert_all_fail else _AlertCaseResult(expected_alert=True, predicted_alert=True)
    )
    monkeypatch.setattr(
        harness_module, "_score_alert_case", MagicMock(return_value=alert_result)
    )


@pytest.mark.asyncio
async def test_run_eval_writes_averaged_scores_and_passes_above_thresholds(
    monkeypatch: pytest.MonkeyPatch,
):
    mock_db = _patch_db(monkeypatch)
    _patch_common(
        monkeypatch, faithfulness_score=0.95, context_recall_score=0.9, rouge_l_score=0.9
    )

    run = await harness_module.run_eval(EvalRunType.MANUAL)

    assert run.ragas_faithfulness == pytest.approx(0.95)
    assert run.ragas_context_recall == pytest.approx(0.9)
    assert run.rouge_l == pytest.approx(0.9)
    assert run.alert_precision == pytest.approx(1.0)
    assert run.alert_recall == pytest.approx(1.0)
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
async def test_run_eval_fails_when_ragas_scores_are_below_documented_thresholds(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.5, context_recall_score=0.5)

    run = await harness_module.run_eval(EvalRunType.SCHEDULED)

    assert run.passed is False


@pytest.mark.asyncio
async def test_run_eval_fails_when_rouge_l_is_below_documented_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(
        monkeypatch, faithfulness_score=0.95, context_recall_score=0.9, rouge_l_score=0.1
    )

    run = await harness_module.run_eval(EvalRunType.MANUAL)

    assert run.passed is False


@pytest.mark.asyncio
async def test_run_eval_leaves_ragas_fields_null_when_every_search_case_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """One metric family failing entirely doesn't stop the other family's
    column(s) from being measured and written."""
    _patch_db(monkeypatch)
    _patch_common(
        monkeypatch,
        faithfulness_score=0.95,
        context_recall_score=0.9,
        chunks_per_case=[],
        rouge_l_score=0.9,
    )

    run = await harness_module.run_eval(EvalRunType.MANUAL)

    assert run.ragas_faithfulness is None
    assert run.ragas_context_recall is None
    assert run.rouge_l == pytest.approx(0.9)
    assert run.passed is True  # only the measured metric (rouge_l) needs to clear its threshold


@pytest.mark.asyncio
async def test_run_eval_leaves_rouge_l_null_when_every_summarization_case_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(
        monkeypatch, faithfulness_score=0.95, context_recall_score=0.9, rouge_l_score=None
    )

    run = await harness_module.run_eval(EvalRunType.MANUAL)

    assert run.ragas_faithfulness == pytest.approx(0.95)
    assert run.rouge_l is None
    assert run.passed is True


@pytest.mark.asyncio
async def test_run_eval_raises_when_every_case_fails_across_every_metric_family(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(
        monkeypatch,
        faithfulness_score=0.95,
        context_recall_score=0.9,
        chunks_per_case=[],
        rouge_l_score=None,
        alert_all_fail=True,
    )

    with pytest.raises(RuntimeError, match="Every eval case failed"):
        await harness_module.run_eval(EvalRunType.MANUAL)


@pytest.mark.asyncio
async def test_run_eval_raises_when_every_case_fails_answer_synthesis(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(
        monkeypatch,
        faithfulness_score=0.95,
        context_recall_score=0.9,
        synthesized_answer=None,
        rouge_l_score=None,
        alert_all_fail=True,
    )

    with pytest.raises(RuntimeError, match="Every eval case failed"):
        await harness_module.run_eval(EvalRunType.MANUAL)


@pytest.mark.asyncio
async def test_run_eval_scores_every_fixture_case(monkeypatch: pytest.MonkeyPatch):
    """Each case gets its own real retrieve_similar_filings + synthesize_answer
    / summarization scoring call — not just the first one."""
    _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.95, context_recall_score=0.9)

    await harness_module.run_eval(EvalRunType.MANUAL)

    assert harness_module.retrieve_similar_filings.await_count == len(SEARCH_EVAL_CASES)
    assert harness_module.synthesize_answer.call_count == len(SEARCH_EVAL_CASES)
    assert harness_module._score_summarization_case.call_count == len(SUMMARIZATION_EVAL_CASES)
    assert harness_module._score_alert_case.call_count == len(ALERT_EVAL_CASES)


def test_current_git_commit_sha_returns_none_on_subprocess_failure(monkeypatch: pytest.MonkeyPatch):
    import subprocess

    def _raise(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr(harness_module.subprocess, "run", _raise)

    assert harness_module._current_git_commit_sha() is None


def test_score_summarization_case_scores_a_real_rouge_l_against_the_generated_brief(
    monkeypatch: pytest.MonkeyPatch,
):
    from regradar.agents.state import BriefSet
    from regradar.evaluation.dataset import SUMMARIZATION_EVAL_CASES

    case = SUMMARIZATION_EVAL_CASES[0]
    fake_briefs = BriefSet(
        executive_brief=case.reference_executive_brief,  # identical text -> perfect score
        cco_summary="irrelevant",
        analyst_summary="irrelevant",
        engineer_summary="irrelevant",
    )
    monkeypatch.setattr(
        harness_module,
        "summarize_node",
        lambda state: state.model_copy(update={"briefs": fake_briefs}),
    )

    score = harness_module._score_summarization_case(case)

    assert score == pytest.approx(1.0)


def test_score_summarization_case_returns_none_when_summarization_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    from regradar.evaluation.dataset import SUMMARIZATION_EVAL_CASES

    case = SUMMARIZATION_EVAL_CASES[0]
    monkeypatch.setattr(harness_module, "summarize_node", lambda state: state)  # briefs stays None

    assert harness_module._score_summarization_case(case) is None


@pytest.mark.parametrize(
    ("risk_level", "expected_predicted_alert"),
    [
        ("high", True),
        ("critical", True),
        ("low", False),
        ("medium", False),
    ],
)
def test_score_alert_case_derives_predicted_alert_from_risk_level(
    monkeypatch: pytest.MonkeyPatch, risk_level: str, expected_predicted_alert: bool
):
    from regradar.models.enums import RiskLevel

    case = ALERT_EVAL_CASES[0]
    monkeypatch.setattr(
        harness_module,
        "triage_node",
        lambda state: state.model_copy(update={"risk_level": RiskLevel(risk_level)}),
    )

    result = harness_module._score_alert_case(case)

    assert result is not None
    assert result.expected_alert == case.expected_alert
    assert result.predicted_alert is expected_predicted_alert


def test_score_alert_case_returns_none_when_triage_fails_to_classify(
    monkeypatch: pytest.MonkeyPatch,
):
    case = ALERT_EVAL_CASES[0]
    monkeypatch.setattr(harness_module, "triage_node", lambda state: state)  # risk_level stays None

    assert harness_module._score_alert_case(case) is None


def test_precision_recall_computes_the_aggregate_confusion_matrix():
    results = [
        _AlertCaseResult(expected_alert=True, predicted_alert=True),  # TP
        _AlertCaseResult(expected_alert=True, predicted_alert=True),  # TP
        _AlertCaseResult(expected_alert=True, predicted_alert=False),  # FN
        _AlertCaseResult(expected_alert=False, predicted_alert=True),  # FP
        _AlertCaseResult(expected_alert=False, predicted_alert=False),  # TN
    ]

    precision, recall = harness_module._precision_recall(results)

    assert precision == pytest.approx(2 / 3)  # TP=2, FP=1
    assert recall == pytest.approx(2 / 3)  # TP=2, FN=1


def test_precision_recall_returns_none_for_zero_denominators():
    # No predicted positives at all -> precision undefined.
    all_predicted_negative = [_AlertCaseResult(expected_alert=True, predicted_alert=False)]
    precision, recall = harness_module._precision_recall(all_predicted_negative)
    assert precision is None
    assert recall == pytest.approx(0.0)

    # No actual positives at all -> recall undefined.
    all_actual_negative = [_AlertCaseResult(expected_alert=False, predicted_alert=False)]
    precision, recall = harness_module._precision_recall(all_actual_negative)
    assert precision is None
    assert recall is None


@pytest.mark.asyncio
async def test_run_eval_fails_when_alert_precision_is_below_documented_threshold(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(monkeypatch, faithfulness_score=0.95, context_recall_score=0.9)
    # Half the alert cases are false positives -> precision well below 0.95.
    monkeypatch.setattr(
        harness_module,
        "_score_alert_case",
        MagicMock(
            side_effect=[
                _AlertCaseResult(expected_alert=False, predicted_alert=True)
                if i % 2 == 0
                else _AlertCaseResult(expected_alert=True, predicted_alert=True)
                for i in range(len(ALERT_EVAL_CASES))
            ]
        ),
    )

    run = await harness_module.run_eval(EvalRunType.MANUAL)

    assert run.alert_precision is not None
    assert run.alert_precision < 0.95
    assert run.passed is False


@pytest.mark.asyncio
async def test_run_eval_leaves_alert_fields_null_when_every_alert_case_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_db(monkeypatch)
    _patch_common(
        monkeypatch, faithfulness_score=0.95, context_recall_score=0.9, alert_all_fail=True
    )

    run = await harness_module.run_eval(EvalRunType.MANUAL)

    assert run.alert_precision is None
    assert run.alert_recall is None
    assert run.ragas_faithfulness == pytest.approx(0.95)  # other families still measured
    assert run.passed is True
