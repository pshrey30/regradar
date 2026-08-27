"""Fixture eval sets for the EVAL-* harnesses (src/regradar/evaluation/harness.py).

EVAL-01's fixture eval set: a small, hand-written RAG corpus + question set.

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

from regradar.models.enums import FilingDomain, FilingSource, RiskLevel


@dataclass(frozen=True)
class SeedFiling:
    """One synthetic filing plus its chunk text(s), seeded for one eval run."""

    entity_name: str
    filing_type: str
    source: FilingSource
    chunks: list[str]


@dataclass(frozen=True)
class SearchEvalCase:
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

SEARCH_EVAL_CASES: list[SearchEvalCase] = [
    SearchEvalCase(
        question="What sterility testing problem did the FDA find at Meridian Biotech's facility?",
        reference=(
            "The FDA found that batch records for Meridian Biotech's MeridiPen insulin "
            "product lacked required in-process sterility testing for three consecutive "
            "production lots in March 2026."
        ),
    ),
    SearchEvalCase(
        question="How much time does Meridian Biotech have to submit a corrective action plan?",
        reference="Meridian Biotech must submit a corrective action plan within 15 business days.",
    ),
    SearchEvalCase(
        question="What material weakness did Harborline Capital Partners disclose?",
        reference=(
            "Harborline Capital Partners disclosed a material weakness in internal "
            "controls related to the valuation of Level 3 illiquid securities."
        ),
    ),
    SearchEvalCase(
        question="What was Harborline Capital Partners' total assets under management?",
        reference="Harborline Capital Partners reported $4.2 billion in total assets under management.",
    ),
    SearchEvalCase(
        question="Why was Coastal Bridge Brokerage censured and fined by FINRA?",
        reference=(
            "Coastal Bridge Brokerage was censured and fined $375,000 for failing to "
            "maintain a supervisory system to detect unsuitable variable annuity "
            "recommendations to elderly customers."
        ),
    ),
    SearchEvalCase(
        question="What remedial step did Coastal Bridge Brokerage agree to as part of its FINRA settlement?",
        reference="Coastal Bridge Brokerage agreed to retain an independent compliance consultant.",
    ),
    SearchEvalCase(
        question="Why did Pinehollow Diagnostics recall its GlucoSure Rapid Test strips?",
        reference=(
            "Pinehollow Diagnostics recalled its GlucoSure Rapid Test strips because a "
            "manufacturing defect could produce falsely low blood glucose readings, "
            "risking undertreated hypoglycemia."
        ),
    ),
    SearchEvalCase(
        question="How many GlucoSure Rapid Test strip units were affected by Pinehollow Diagnostics' recall?",
        reference="Approximately 340,000 units were affected by the recall.",
    ),
]


@dataclass(frozen=True)
class SummarizationEvalCase:
    """EVAL-02's fixture eval set: a small, hand-written extraction-shaped
    input plus a reference executive_brief, run through the real
    Summarization Agent (agents.summarization_agent.summarize_node) and
    scored via ROUGE-L against the reference.
    """

    domain: FilingDomain
    risk_level: RiskLevel
    obligations: list[dict]
    deadlines: list[dict]
    risk_flags: list[str]
    affected_products: list[str]
    key_entities: list[str]
    reference_executive_brief: str


SUMMARIZATION_EVAL_CASES: list[SummarizationEvalCase] = [
    SummarizationEvalCase(
        domain=FilingDomain.CLINICAL,
        risk_level=RiskLevel.HIGH,
        obligations=[
            {"description": "Submit a corrective action plan addressing sterility testing gaps"}
        ],
        deadlines=[{"description": "Corrective action plan due", "date": "2026-04-15"}],
        risk_flags=["missing in-process sterility testing", "injectable drug product"],
        affected_products=["MeridiPen insulin"],
        key_entities=["Meridian Biotech Inc.", "FDA"],
        reference_executive_brief=(
            "The FDA issued a warning letter to Meridian Biotech after finding that its "
            "MeridiPen insulin product lacked required in-process sterility testing for "
            "three production lots. The company must submit a corrective action plan by "
            "April 15, 2026. This is a high-risk finding involving an injectable drug product."
        ),
    ),
    SummarizationEvalCase(
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.MEDIUM,
        obligations=[{"description": "Remediate the identified internal-controls weakness"}],
        deadlines=[],
        risk_flags=["material weakness in internal controls", "Level 3 illiquid securities valuation"],
        affected_products=[],
        key_entities=["Harborline Capital Partners"],
        reference_executive_brief=(
            "Harborline Capital Partners disclosed a material weakness in its internal "
            "controls over the valuation of Level 3 illiquid securities, identified during "
            "its Q4 2025 audit. The firm must remediate the weakness. This is a medium-risk "
            "finding affecting financial reporting reliability."
        ),
    ),
    SummarizationEvalCase(
        domain=FilingDomain.FINANCIAL,
        risk_level=RiskLevel.CRITICAL,
        obligations=[{"description": "Retain an independent compliance consultant"}],
        deadlines=[],
        risk_flags=["unsuitable variable annuity recommendations", "elderly customers", "supervisory failure"],
        affected_products=["variable annuities"],
        key_entities=["Coastal Bridge Brokerage LLC", "FINRA"],
        reference_executive_brief=(
            "FINRA censured and fined Coastal Bridge Brokerage $375,000 for failing to "
            "supervise unsuitable variable annuity recommendations made to elderly "
            "customers. The firm must retain an independent compliance consultant. This is "
            "a critical-risk finding involving vulnerable customers."
        ),
    ),
    SummarizationEvalCase(
        domain=FilingDomain.CLINICAL,
        risk_level=RiskLevel.CRITICAL,
        obligations=[{"description": "Notify affected distributors and customers of the recall"}],
        deadlines=[],
        risk_flags=["Class I recall", "false low glucose readings", "risk of undertreated hypoglycemia"],
        affected_products=["GlucoSure Rapid Test strips"],
        key_entities=["Pinehollow Diagnostics Corp."],
        reference_executive_brief=(
            "Pinehollow Diagnostics initiated a Class I recall of approximately 340,000 "
            "GlucoSure Rapid Test strip units due to a defect that can produce falsely low "
            "blood glucose readings. This poses a risk of undertreated hypoglycemia. This "
            "is a critical-risk finding requiring immediate customer notification."
        ),
    ),
    SummarizationEvalCase(
        domain=FilingDomain.ENVIRONMENTAL,
        risk_level=RiskLevel.LOW,
        obligations=[{"description": "File an updated emissions monitoring report"}],
        deadlines=[{"description": "Updated report due", "date": "2026-06-01"}],
        risk_flags=["minor reporting delay"],
        affected_products=[],
        key_entities=["Alderbrook Chemical Works"],
        reference_executive_brief=(
            "Alderbrook Chemical Works must file an updated emissions monitoring report by "
            "June 1, 2026, following a minor reporting delay. This is a low-risk finding "
            "with no indication of an actual emissions violation."
        ),
    ),
]
