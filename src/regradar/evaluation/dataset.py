"""EVAL-01's fixture eval set: a small, hand-written RAG corpus + question set.

Ragas' faithfulness/context-recall metrics score real (question, retrieved
context, generated answer, reference answer) tuples — they need something
real to run the actual API-06 retrieval + synthesis pipeline against, not a
mocked response. This is a self-contained fixture (not real filings): the
harness seeds it into `filings`/`filing_chunks` under the default
organization at the start of a run and deletes it again at the end, so a
run never leaves synthetic data behind for real API callers to see.

Every question's `reference` answer is only supported by facts present in
`seed_chunks` — that's what makes faithfulness/context-recall meaningful to
score at all here.
"""

from dataclasses import dataclass

from regradar.models.enums import FilingSource


@dataclass(frozen=True)
class SeedFiling:
    """One synthetic filing plus its chunk text(s), seeded for one eval run."""

    entity_name: str
    filing_type: str
    source: FilingSource
    chunks: list[str]


@dataclass(frozen=True)
class EvalCase:
    """One question this eval run asks against the seeded corpus above."""

    question: str
    reference: str


SEED_FILINGS: list[SeedFiling] = [
    SeedFiling(
        entity_name="Meridian Biotech Inc.",
        filing_type="Warning Letter",
        source=FilingSource.FDA,
        chunks=[
            (
                "The FDA has identified significant violations at Meridian Biotech's "
                "Raleigh, NC manufacturing facility. Investigators found that batch "
                "records for the company's injectable insulin product, MeridiPen, "
                "lacked required in-process sterility testing for three consecutive "
                "production lots in March 2026. The firm must submit a corrective "
                "action plan within 15 business days of receipt of this letter."
            ),
        ],
    ),
    SeedFiling(
        entity_name="Harborline Capital Partners",
        filing_type="10-K",
        source=FilingSource.SEC,
        chunks=[
            (
                "Harborline Capital Partners reported total assets under management "
                "of $4.2 billion as of fiscal year end 2025, a 12% increase from the "
                "prior year. The company disclosed a material weakness in internal "
                "controls related to the valuation of Level 3 illiquid securities, "
                "identified during the Q4 2025 audit."
            ),
        ],
    ),
    SeedFiling(
        entity_name="Coastal Bridge Brokerage LLC",
        filing_type="Disciplinary Action",
        source=FilingSource.FINRA,
        chunks=[
            (
                "FINRA found that Coastal Bridge Brokerage failed to establish and "
                "maintain a supervisory system reasonably designed to detect "
                "unsuitable variable annuity recommendations to elderly customers "
                "between January 2024 and June 2025. The firm was censured and fined "
                "$375,000, and agreed to retain an independent compliance consultant."
            ),
        ],
    ),
    SeedFiling(
        entity_name="Pinehollow Diagnostics Corp.",
        filing_type="Recall Notice",
        source=FilingSource.FDA,
        chunks=[
            (
                "Pinehollow Diagnostics initiated a Class I recall of its GlucoSure "
                "Rapid Test strips due to a manufacturing defect that can produce "
                "falsely low blood glucose readings, posing a risk of undertreated "
                "hypoglycemia. Approximately 340,000 units distributed between "
                "September 2025 and January 2026 are affected."
            ),
        ],
    ),
]

EVAL_CASES: list[EvalCase] = [
    EvalCase(
        question="What sterility testing problem did the FDA find at Meridian Biotech's facility?",
        reference=(
            "The FDA found that batch records for Meridian Biotech's MeridiPen insulin "
            "product lacked required in-process sterility testing for three consecutive "
            "production lots in March 2026."
        ),
    ),
    EvalCase(
        question="How much time does Meridian Biotech have to submit a corrective action plan?",
        reference="Meridian Biotech must submit a corrective action plan within 15 business days.",
    ),
    EvalCase(
        question="What material weakness did Harborline Capital Partners disclose?",
        reference=(
            "Harborline Capital Partners disclosed a material weakness in internal "
            "controls related to the valuation of Level 3 illiquid securities."
        ),
    ),
    EvalCase(
        question="What was Harborline Capital Partners' total assets under management?",
        reference="Harborline Capital Partners reported $4.2 billion in total assets under management.",
    ),
    EvalCase(
        question="Why was Coastal Bridge Brokerage censured and fined by FINRA?",
        reference=(
            "Coastal Bridge Brokerage was censured and fined $375,000 for failing to "
            "maintain a supervisory system to detect unsuitable variable annuity "
            "recommendations to elderly customers."
        ),
    ),
    EvalCase(
        question="What remedial step did Coastal Bridge Brokerage agree to as part of its FINRA settlement?",
        reference="Coastal Bridge Brokerage agreed to retain an independent compliance consultant.",
    ),
    EvalCase(
        question="Why did Pinehollow Diagnostics recall its GlucoSure Rapid Test strips?",
        reference=(
            "Pinehollow Diagnostics recalled its GlucoSure Rapid Test strips because a "
            "manufacturing defect could produce falsely low blood glucose readings, "
            "risking undertreated hypoglycemia."
        ),
    ),
    EvalCase(
        question="How many GlucoSure Rapid Test strip units were affected by Pinehollow Diagnostics' recall?",
        reference="Approximately 340,000 units were affected by the recall.",
    ),
]
