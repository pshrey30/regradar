# PDF Intake Wiring — Close the ING-05 Gap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the long-flagged gap where `ingestion/pdf_intake.py`'s `intake_pdf()` (built in ING-05) is never actually called by any real ingestion connector — so `Filing.raw_pdf_s3_key` has only ever been set by manual/test insertion, never by a real poll cycle. Wire it into the SEC EDGAR connector (the only source where it's genuinely applicable — see deviation below), and make the whole pipeline actually work end-to-end on a real, non-test filing for the first time.

**Architecture:** SEC EDGAR's "current filings" feed only gives a filing *index page* URL, not a direct document URL — resolving the real primary document requires a second lookup against EDGAR's per-company submissions API, which has an authoritative `primaryDocument` field (verified live against a real Apple 10-K, not guessed — see deviation). Once resolved, `intake_pdf()` is called after the `Filing` row is committed, with `PdfIntakeError` caught so a download failure degrades to "no PDF this cycle" rather than crashing the whole poll cycle, matching every other connector's existing "never raises" design. A new shared insertion helper removes the three-way duplication of the `begin_nested()`/`db.add()`/`IntegrityError` pattern across all three connectors, since PDF-intake wiring needs a hook right after that insert.

**Real deviation, discovered while investigating this gap, not assumed up front:** modern SEC EDGAR primary documents (10-K/10-Q/8-K, the vast majority of real filings) are HTML/iXBRL, not raw PDF — SEC deprecated PDF-only primary filings years ago. Verified live: Apple's most recent 10-K (accession `0000320193-25-000079`) has `primaryDocument: "aapl-20250927.htm"`. The existing `rag/pdf_extraction.py` only understands real PDF binary (`pdfplumber.open()` on non-PDF bytes raises). Wiring `intake_pdf()` in without also handling this would "close the gap" in name only — it would archive a real document to S3, then crash or silently produce empty text the moment the pipeline tried to extract it, for the overwhelming majority of real EDGAR filings. So this plan also makes `pdf_extraction.py` content-type-aware: sniff the downloaded bytes, dispatch to the existing `pdfplumber` path for genuine PDF magic bytes, and a new lightweight HTML-text path (via `beautifulsoup4`, a new dependency) otherwise. This is exactly the kind of scope expansion AGENT-05 set precedent for ("closes gaps the ticket never chartered" when reality doesn't match the original assumption) — the user's explicit instruction ("fix all the gap and make it run smoothly") is a direct mandate for this, not an assumption on my part.

**FDA and FINRA are confirmed genuinely not applicable, not just deprioritized:** FDA's RSS feed entries link to HTML news pages with no PDF anywhere in the feed data. FINRA's connector polls a JSON dataset (the Reg SHO Threshold List) — one row per symbol, no narrative document per filing at all, same static API URL for every row. Wiring PDF intake into either would have nothing real to wire to. This plan documents that explicitly in both connectors' module docstrings rather than leaving the gap ambiguous.

**Tech Stack:** Python 3.11, `httpx` (sync, matching every existing connector's synchronous HTTP pattern — `intake_pdf()` itself is async but its internal downloads are sync `httpx.get`, unchanged), `beautifulsoup4` (new dependency, HTML text extraction), `pdfplumber` (existing), pytest + `moto` (existing S3 mocking pattern from ING-05's own tests).

## Global Constraints

- No connector may ever raise out of its `poll_*()` function over a PDF/document download failure — every existing connector's docstring already states "never raises on request failure"; a `PdfIntakeError` from `intake_pdf()` must be caught at the call site and logged, not propagated.
- The primary-document URL resolution must use EDGAR's submissions API's authoritative `primaryDocument` field — verified live against a real filing, not a heuristic guess against `index.json` (whose `type` field is a MIME-icon hint like `text.gif`, not a document-type field — a real dead end investigated and ruled out during planning).
- `rag/pdf_extraction.py`'s content-type dispatch must be based on sniffing the actual downloaded bytes (real PDF magic bytes `%PDF-`), never on file extension or URL string inspection alone — a `.htm` URL could theoretically still 30x-redirect to a PDF, and vice versa.
- The new shared connector-insertion helper must be a pure refactor of the three connectors' existing `begin_nested()`/`add()`/`IntegrityError`-catch logic — no behavior change to FDA/FINRA's insertion path, verified by their existing tests continuing to pass unmodified.
- No new Alembic migration — `Filing.raw_pdf_s3_key` already exists (`str | None`, nullable `Text`), and no other schema change is needed.

---

## Task 1: Shared connector insertion helper

**Files:**
- Create: `src/regradar/ingestion/sources/_common.py`
- Modify: `src/regradar/ingestion/sources/sec_edgar.py`
- Modify: `src/regradar/ingestion/sources/fda_rss.py`
- Modify: `src/regradar/ingestion/sources/finra_feed.py`
- Modify: `tests/unit/ingestion/sources/test_sec_edgar.py`, `tests/unit/ingestion/sources/test_fda_rss.py`, `tests/unit/ingestion/sources/test_finra_feed.py` (only if the refactor changes an assertion's exact mock-call shape — most existing tests should pass unmodified since this is a pure refactor)

**Interfaces:**
- Produces: `async def insert_new_filing(db: AsyncSession, source: FilingSource, candidate: NewFiling) -> Filing | None` from `regradar.ingestion.sources._common` — returns the inserted `Filing` ORM row (with a real `.id` from the DB) on success, or `None` if the insert lost an `IntegrityError` race. Consumed by Task 4's `poll_edgar()` (needs a real `Filing.id` to call `intake_pdf()` with) and, for consistency, by `fda_rss.py`/`finra_feed.py` too (they discard the return value, matching their current behavior of accumulating into an `inserted: list[NewFiling]` from `candidate`, not the returned `Filing`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/ingestion/sources/test_common.py`:

```python
"""Unit tests for the shared connector Filing-insertion helper."""

import os

os.environ.setdefault("APP_SECRET_KEY", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("S3_BUCKET_NAME", "test-bucket")
os.environ.setdefault("S3_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("HUGGINGFACE_API_TOKEN", "test")
os.environ.setdefault("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from regradar.ingestion.sources._common import insert_new_filing
from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource


def _make_candidate() -> NewFiling:
    return NewFiling(
        source_document_id="0000320193-25-000079",
        entity_name="Apple Inc.",
        filing_type="10-K",
        filing_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/0000320193-25-000079-index.htm",
        published_at=datetime.now(UTC),
    )


def _noop_nested_transaction():
    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    return _Ctx()


@pytest.mark.asyncio
async def test_insert_new_filing_returns_filing_on_success() -> None:
    db = AsyncMock()
    db.begin_nested = MagicMock(side_effect=_noop_nested_transaction)
    db.add = MagicMock()
    db.commit = AsyncMock()

    result = await insert_new_filing(db, FilingSource.SEC, _make_candidate())

    assert result is not None
    assert result.source == FilingSource.SEC
    assert result.source_document_id == "0000320193-25-000079"
    assert result.entity_name == "Apple Inc."
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_insert_new_filing_returns_none_on_integrity_error() -> None:
    db = AsyncMock()

    def _raise_nested():
        class _Ctx:
            async def __aenter__(self):
                raise IntegrityError("stmt", {}, Exception("dup"))

            async def __aexit__(self, *args):
                return False

        return _Ctx()

    db.begin_nested = MagicMock(side_effect=_raise_nested)
    db.add = MagicMock()

    result = await insert_new_filing(db, FilingSource.SEC, _make_candidate())

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/ingestion/sources/test_common.py -v`
Expected: `ModuleNotFoundError` for `regradar.ingestion.sources._common`.

- [ ] **Step 3: Write the implementation**

Create `src/regradar/ingestion/sources/_common.py`:

```python
"""Shared insertion logic for every ingestion connector.

All three connectors (sec_edgar.py, fda_rss.py, finra_feed.py) independently
duplicated the same begin_nested()/add()/IntegrityError-catch pattern. This
is that logic, extracted once — and the hook point sec_edgar.py's PDF-intake
wiring needs, since intake_pdf() requires a real, committed Filing.id.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.ingestion.types import NewFiling
from regradar.models.enums import FilingSource, FilingStatus
from regradar.models.filing import Filing


async def insert_new_filing(
    db: AsyncSession, source: FilingSource, candidate: NewFiling
) -> Filing | None:
    """Insert one new Filing row. Returns the row (with a real id) on
    success, or None if another poller already inserted this
    source_document_id first (IntegrityError race) — the unique
    constraint is the real guarantee; this is just how a connector finds
    out it lost that race."""
    filing = Filing(
        source=source,
        source_document_id=candidate.source_document_id,
        entity_name=candidate.entity_name,
        filing_type=candidate.filing_type,
        filing_url=candidate.filing_url,
        published_at=candidate.published_at,
        ingested_at=datetime.now(UTC),
        status=FilingStatus.INGESTED,
    )
    try:
        async with db.begin_nested():
            db.add(filing)
    except IntegrityError:
        return None
    return filing
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/ingestion/sources/test_common.py -v`
Expected: both tests PASS.

- [ ] **Step 5: Refactor the three connectors to use it**

In `src/regradar/ingestion/sources/sec_edgar.py`: replace the `for candidate in candidates:` loop body (the `try: async with db.begin_nested(): db.add(Filing(...)) except IntegrityError: continue` block) with:

```python
    inserted: list[NewFiling] = []
    for candidate in candidates:
        if candidate.source_document_id in existing_ids:
            continue
        filing = await insert_new_filing(db, FilingSource.SEC, candidate)
        if filing is None:
            continue
        inserted.append(candidate)
```

Add `from regradar.ingestion.sources._common import insert_new_filing` to the imports. Remove the now-unused `IntegrityError` import if nothing else in the file uses it (check first), and remove the now-unused `Filing`/`FilingStatus` imports if `poll_edgar` no longer constructs `Filing` directly (check — `FilingSource` is still needed for the `insert_new_filing` call; `FilingStatus` may become unused, check before removing).

Apply the identical transformation to `src/regradar/ingestion/sources/fda_rss.py` and `src/regradar/ingestion/sources/finra_feed.py` — same pattern, same import changes, adjusted only for each file's existing variable names (check each file's current loop structure first; don't assume it's byte-identical to EDGAR's, since FINRA's has an extra `report_date` parameter in scope but the insertion loop body itself should be structurally the same shape).

- [ ] **Step 6: Run the full ingestion test suite to check for regressions**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/ingestion/ -v`
Expected: all existing tests for all three connectors still PASS unmodified (this is a pure refactor — if any existing test's mock setup breaks because it asserted on `db.add`'s exact call args in a way now indirected through `insert_new_filing`, fix the test's assertion to match the new call shape without changing what behavior it's actually verifying).

- [ ] **Step 7: Run the full unit suite, ruff, and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`, `... -m ruff check .`, `... -m mypy src/`
Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add src/regradar/ingestion/sources/_common.py src/regradar/ingestion/sources/sec_edgar.py src/regradar/ingestion/sources/fda_rss.py src/regradar/ingestion/sources/finra_feed.py tests/unit/ingestion/sources/test_common.py
git commit -m "Extract shared Filing-insertion helper from the three ingestion connectors"
```

(If Step 5's refactor required test-assertion updates in the existing connector test files, `git add` those too and note it in the commit message.)

---

## Task 2: Resolve SEC EDGAR's real primary-document URL

**Files:**
- Modify: `src/regradar/ingestion/sources/sec_edgar.py`
- Modify: `tests/unit/ingestion/sources/test_sec_edgar.py`

**Interfaces:**
- Produces: `async def _resolve_primary_document_url(cik: str, accession_number: str, user_agent: str) -> str | None` — returns the full downloadable URL of a filing's primary document, or `None` if it can't be resolved (network error, CIK not found, accession not in the company's recent submissions — logged, never raised). Consumed by Task 4's `poll_edgar()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/ingestion/sources/test_sec_edgar.py` (after the existing tests, using this file's established `monkeypatch` + `sec_edgar.httpx.get` patching pattern):

```python
import json


SAMPLE_SUBMISSIONS_JSON = {
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-25-000079", "0001140361-26-033928"],
            "form": ["10-K", "4"],
            "primaryDocument": ["aapl-20250927.htm", "xslF345X06/form4.xml"],
        }
    }
}


def test_resolve_primary_document_url_returns_real_url(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_SUBMISSIONS_JSON
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=mock_response))

    result = asyncio.get_event_loop().run_until_complete(
        sec_edgar._resolve_primary_document_url(
            cik="320193", accession_number="0000320193-25-000079", user_agent="test-agent"
        )
    )

    assert result == (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
    )


def test_resolve_primary_document_url_returns_none_when_accession_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = SAMPLE_SUBMISSIONS_JSON
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=mock_response))

    result = asyncio.get_event_loop().run_until_complete(
        sec_edgar._resolve_primary_document_url(
            cik="320193", accession_number="9999999999-99-999999", user_agent="test-agent"
        )
    )

    assert result is None


def test_resolve_primary_document_url_returns_none_on_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sec_edgar.httpx, "get", MagicMock(side_effect=sec_edgar.httpx.RequestError("timeout"))
    )

    result = asyncio.get_event_loop().run_until_complete(
        sec_edgar._resolve_primary_document_url(
            cik="320193", accession_number="0000320193-25-000079", user_agent="test-agent"
        )
    )

    assert result is None


def test_resolve_primary_document_url_returns_none_on_non_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=mock_response))

    result = asyncio.get_event_loop().run_until_complete(
        sec_edgar._resolve_primary_document_url(
            cik="320193", accession_number="0000320193-25-000079", user_agent="test-agent"
        )
    )

    assert result is None
```

Add `import asyncio` and `import json` to this test file's imports if not already present (check first — `json` may end up unused if the test only ever sets `.json.return_value` directly on a mock rather than parsing real JSON text; keep only if actually used by the final test bodies as written above — inspect and drop the `import json` if it's genuinely unused after Step 1's tests are in place, to avoid an F401 ruff failure).

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/ingestion/sources/test_sec_edgar.py -v -k resolve_primary_document`
Expected: `AttributeError: module 'regradar.ingestion.sources.sec_edgar' has no attribute '_resolve_primary_document_url'` on all 4.

- [ ] **Step 3: Write the implementation**

In `src/regradar/ingestion/sources/sec_edgar.py`, add after `_fetch_current_filings_feed`:

```python
SUBMISSIONS_API_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"


def _extract_cik_from_filing_url(filing_url: str) -> str | None:
    """The current-filings feed's index-page link always looks like
    .../Archives/edgar/data/{cik}/{accession-no-dashes}/{accession}-index.htm
    — the CIK segment is unpadded (no leading zeros)."""
    marker = "/data/"
    idx = filing_url.find(marker)
    if idx == -1:
        return None
    remainder = filing_url[idx + len(marker) :]
    cik = remainder.split("/", 1)[0]
    return cik if cik.isdigit() else None


async def _resolve_primary_document_url(
    cik: str, accession_number: str, user_agent: str
) -> str | None:
    """Resolve a filing's actual downloadable primary document URL.

    The current-filings feed only gives an index-page URL, not a direct
    document link. EDGAR's per-company submissions API has an
    authoritative primaryDocument field per accession — verified live
    against a real filing during planning (Apple's most recent 10-K
    resolves to "aapl-20250927.htm", matching manual inspection of the
    filing's real document list). This is NOT parsed from index.json's
    directory listing — that file's "type" field is a MIME-icon hint
    ("text.gif") for the web UI, not a document-type field, and isn't a
    reliable way to identify the primary document.

    Returns None (never raises) on any failure — a filing whose primary
    document can't be resolved this cycle just doesn't get its PDF
    archived yet; it isn't a reason to fail the whole poll.
    """
    _get_rate_limiter().wait()
    url = SUBMISSIONS_API_URL.format(cik=cik)
    try:
        response = httpx.get(url, headers={"User-Agent": user_agent}, timeout=10.0)
    except httpx.RequestError as exc:
        logger.warning("Failed to fetch submissions for CIK %s: %s", cik, exc)
        return None

    if response.status_code != 200:
        return None

    try:
        data = response.json()
        recent = data["filings"]["recent"]
        accession_numbers = recent["accessionNumber"]
        primary_documents = recent["primaryDocument"]
    except (KeyError, ValueError) as exc:
        logger.warning("Malformed submissions response for CIK %s: %s", cik, exc)
        return None

    if accession_number not in accession_numbers:
        return None
    index = accession_numbers.index(accession_number)
    primary_document = primary_documents[index]
    accession_no_dashes = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{primary_document}"
```

Add `import logging` and `logger = logging.getLogger(__name__)` near the top of the file if not already present (check first — this file currently has no logger, per the earlier full-file read).

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/ingestion/sources/test_sec_edgar.py -v`
Expected: all tests (existing + 4 new) PASS.

- [ ] **Step 5: Run the full unit suite, ruff, and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`, `... -m ruff check .`, `... -m mypy src/`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add src/regradar/ingestion/sources/sec_edgar.py tests/unit/ingestion/sources/test_sec_edgar.py
git commit -m "Resolve SEC EDGAR's real primary-document URL via the submissions API"
```

---

## Task 3: Content-type-aware document extraction (PDF or HTML)

**Files:**
- Modify: `pyproject.toml` (add `beautifulsoup4` dependency)
- Modify: `src/regradar/rag/pdf_extraction.py`
- Modify: `src/regradar/workers/pipeline_tasks.py` (only the import names, if renamed — see Step 3)
- Modify: `tests/unit/rag/test_pdf_extraction.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `extract_text_and_tables(document_bytes: bytes) -> tuple[str, list[TableBlock]]` (same name/signature as today — `document_bytes` param renamed from `pdf_bytes` for accuracy, but this is a positional-compatible rename, not a breaking change for the one call site in `pipeline_tasks.py`) now handles both real PDF and HTML content transparently. `fetch_pdf_bytes` is renamed to `fetch_document_bytes` (same signature) since it now may return either format — update its one call site in `pipeline_tasks.py`.

- [ ] **Step 1: Add the new dependency**

In `pyproject.toml`, find the main dependencies list (where `pdfplumber` is already declared) and add `"beautifulsoup4>=4.12"` alongside it, in the same style/section.

- [ ] **Step 2: Write the failing tests**

Add to `tests/unit/rag/test_pdf_extraction.py` (check the existing file's imports/fixtures first and match its style — likely already has a real or synthetic PDF fixture from ING-05/AGENT-05, per `tests/fixtures/sample_filings/synthetic_table_filing.pdf`):

```python
def test_extract_text_and_tables_handles_real_pdf_bytes() -> None:
    with open("tests/fixtures/sample_filings/synthetic_table_filing.pdf", "rb") as f:
        pdf_bytes = f.read()

    text, tables = extract_text_and_tables(pdf_bytes)

    assert text  # non-empty — exact content already covered by this file's existing PDF tests


def test_extract_text_and_tables_handles_html_bytes() -> None:
    html = b"""
    <html><body>
    <h1>Item 1A. Risk Factors</h1>
    <p>Acme Corp faces a material weakness in internal controls.</p>
    <table><tr><td>Deadline</td><td>2027-01-15</td></tr></table>
    </body></html>
    """

    text, tables = extract_text_and_tables(html)

    assert "Item 1A. Risk Factors" in text
    assert "material weakness" in text
    assert "<html>" not in text  # tags stripped, not leaked into extracted text
    assert len(tables) == 1
    table_text = text[tables[0].start_char : tables[0].end_char]
    assert "Deadline" in table_text
    assert "2027-01-15" in table_text


def test_extract_text_and_tables_strips_script_and_style_from_html() -> None:
    html = b"""
    <html><body>
    <script>alert('should not appear');</script>
    <style>.hidden { display: none; }</style>
    <p>Real filing content here.</p>
    </body></html>
    """

    text, _tables = extract_text_and_tables(html)

    assert "alert" not in text
    assert "display: none" not in text
    assert "Real filing content here." in text
```

- [ ] **Step 3: Run tests to verify they fail as expected**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/rag/test_pdf_extraction.py -v -k "html"`
Expected: the two HTML tests fail (either `pdfplumber` raises trying to parse HTML as PDF, or the assertions about tags/tables fail) — confirming the current implementation has no HTML path. The real-PDF test should already pass (it exercises existing behavior, just via the renamed function/param — write it now so its later pass confirms the rename didn't break anything).

- [ ] **Step 4: Write the implementation**

Replace the full contents of `src/regradar/rag/pdf_extraction.py`:

```python
"""Document text and table extraction — reads a filing's stored S3 document
(real PDF binary, or HTML for modern SEC EDGAR primary documents — most
2020s+ EDGAR filings are HTML/iXBRL, not PDF; SEC deprecated PDF-only
primary filings years ago) and turns it into plain text plus detected
table regions, ready for rag.chunking.chunk_filing().

Dispatches on the actual downloaded bytes' real content (PDF magic bytes),
never on file extension or URL — a redirect or misconfigured content-type
header could make either assumption wrong.
"""

import io

import pdfplumber
from bs4 import BeautifulSoup

from regradar.core.config import get_settings
from regradar.core.s3_client import get_s3_client
from regradar.rag.chunking import TableBlock

PDF_MAGIC_BYTES = b"%PDF-"


def fetch_document_bytes(s3_key: str) -> bytes:
    """Download a filing's stored document (PDF or HTML) from S3."""
    settings = get_settings()
    client = get_s3_client()
    response = client.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    return response["Body"].read()


def _extract_from_pdf(pdf_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Per page, page.extract_text() builds that page's text (pages joined
    with "\\n\\n"). For each page.find_tables() result,
    page.within_bbox(table.bbox).extract_text() gives that table's own
    rendered text, located within the page's full text via str.find() —
    both use the same extract_text() rendering, so this locates reliably.
    A table whose text can't be found in the page text is skipped, never
    guessed at."""
    full_text_parts: list[str] = []
    tables: list[TableBlock] = []
    cumulative_offset = 0

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            for table in page.find_tables():
                table_text = page.within_bbox(table.bbox).extract_text() or ""
                if not table_text:
                    continue
                local_start = page_text.find(table_text)
                if local_start == -1:
                    continue
                local_end = local_start + len(table_text)
                tables.append(
                    TableBlock(
                        start_char=cumulative_offset + local_start,
                        end_char=cumulative_offset + local_end,
                    )
                )
            full_text_parts.append(page_text)
            cumulative_offset += len(page_text) + 2  # +2 for the "\n\n" page separator

    full_text = "\n\n".join(full_text_parts)
    return full_text, tables


def _extract_from_html(html_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Strip script/style (never real filing content), extract visible
    text, then locate each <table>'s own text within that same full text
    — same start_char/end_char contract as the PDF path, located via
    str.find() the same way, for the same reason (one extraction pass is
    the ground truth both the full text and each table's span come from)."""
    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()

    tables: list[TableBlock] = []
    table_texts = [table.get_text(separator=" ", strip=True) for table in soup.find_all("table")]

    full_text = soup.get_text(separator="\n", strip=True)

    for table_text in table_texts:
        if not table_text:
            continue
        start = full_text.find(table_text)
        if start == -1:
            continue
        tables.append(TableBlock(start_char=start, end_char=start + len(table_text)))

    return full_text, tables


def extract_text_and_tables(document_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Extract plain text and detected table regions from a filing's
    downloaded document — dispatches on the bytes' actual content, not
    file extension or URL."""
    if document_bytes.lstrip()[: len(PDF_MAGIC_BYTES)] == PDF_MAGIC_BYTES:
        return _extract_from_pdf(document_bytes)
    return _extract_from_html(document_bytes)
```

- [ ] **Step 5: Update `pipeline_tasks.py`'s import**

In `src/regradar/workers/pipeline_tasks.py`, change:

```python
from regradar.rag.pdf_extraction import extract_text_and_tables, fetch_pdf_bytes
```

to:

```python
from regradar.rag.pdf_extraction import extract_text_and_tables, fetch_document_bytes
```

and update the one call site:

```python
                pdf_bytes = fetch_pdf_bytes(filing.raw_pdf_s3_key)
                raw_text, tables = extract_text_and_tables(pdf_bytes)
```

to:

```python
                document_bytes = fetch_document_bytes(filing.raw_pdf_s3_key)
                raw_text, tables = extract_text_and_tables(document_bytes)
```

Check `tests/unit/workers/test_pipeline_tasks.py` for any `monkeypatch.setattr(pipeline_tasks_module, "fetch_pdf_bytes", ...)` call sites (there should be at least one, per the earlier established mocking pattern for this module) and rename them to `fetch_document_bytes` to match.

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/rag/test_pdf_extraction.py tests/unit/workers/test_pipeline_tasks.py -v`
Expected: all tests PASS.

- [ ] **Step 7: Run the full unit suite, ruff, and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`, `... -m ruff check .`, `... -m mypy src/`
Expected: all clean. If `mypy` complains about `bs4` lacking type stubs, add `types-beautifulsoup4` to the dev dependency group in `pyproject.toml` (check how other typed-but-stub-needing dependencies are handled in this project first, e.g. search for an existing `types-*` entry, and match that pattern).

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/regradar/rag/pdf_extraction.py src/regradar/workers/pipeline_tasks.py tests/unit/rag/test_pdf_extraction.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Make document extraction content-type-aware (real PDF or HTML)"
```

---

## Task 4: Wire `intake_pdf()` into the EDGAR connector; document FDA/FINRA's non-applicability

**Files:**
- Modify: `src/regradar/ingestion/sources/sec_edgar.py`
- Modify: `src/regradar/ingestion/sources/fda_rss.py`
- Modify: `src/regradar/ingestion/sources/finra_feed.py`
- Modify: `tests/unit/ingestion/sources/test_sec_edgar.py`

**Interfaces:**
- Consumes: `insert_new_filing` (Task 1), `_resolve_primary_document_url` (Task 2), `intake_pdf`/`PdfIntakeError` from `regradar.ingestion.pdf_intake`.
- Produces: `poll_edgar()` now archives each newly-inserted filing's real primary document to S3 and sets `Filing.raw_pdf_s3_key`, end to end, for the first time via a real (non-test) code path.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/ingestion/sources/test_sec_edgar.py`:

```python
@pytest.mark.asyncio
async def test_poll_edgar_calls_intake_pdf_for_each_new_filing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=_sample_feed_response()))
    db = _make_mock_db()

    mock_resolve = AsyncMock(return_value="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm")
    mock_intake = AsyncMock(return_value="filings/abc123.pdf")
    monkeypatch.setattr(sec_edgar, "_resolve_primary_document_url", mock_resolve)
    monkeypatch.setattr(sec_edgar, "intake_pdf", mock_intake)

    await sec_edgar.poll_edgar(_make_source_config(), db)

    mock_resolve.assert_awaited_once()
    mock_intake.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_edgar_continues_when_intake_pdf_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=_sample_feed_response()))
    db = _make_mock_db()

    monkeypatch.setattr(
        sec_edgar,
        "_resolve_primary_document_url",
        AsyncMock(return_value="https://www.sec.gov/Archives/edgar/data/320193/x/doc.htm"),
    )
    monkeypatch.setattr(
        sec_edgar, "intake_pdf", AsyncMock(side_effect=sec_edgar.PdfIntakeError("download failed"))
    )

    # Must not raise — a PDF download failure is not a reason to fail the whole poll cycle.
    result = await sec_edgar.poll_edgar(_make_source_config(), db)

    assert len(result) == 1  # the filing itself was still successfully inserted


@pytest.mark.asyncio
async def test_poll_edgar_skips_intake_pdf_when_primary_document_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sec_edgar.httpx, "get", MagicMock(return_value=_sample_feed_response()))
    db = _make_mock_db()

    monkeypatch.setattr(sec_edgar, "_resolve_primary_document_url", AsyncMock(return_value=None))
    mock_intake = AsyncMock()
    monkeypatch.setattr(sec_edgar, "intake_pdf", mock_intake)

    await sec_edgar.poll_edgar(_make_source_config(), db)

    mock_intake.assert_not_awaited()
```

Check this test file's existing helpers (`_sample_feed_response`/`_make_source_config`/`_make_mock_db`, or whatever the actual current helper names are — the earlier full-file read didn't capture every helper name, only the `_make_mock_db(existing_ids=None)` one) and adapt these three new tests' setup calls to match whatever those helpers are actually named and shaped, rather than assuming the placeholder names above are exactly right. The important content is the mocking/assertion structure (mock `_resolve_primary_document_url` and `intake_pdf` at the module level, verify they're called/not-called and that a `PdfIntakeError` doesn't propagate), not the exact fixture helper names.

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/ingestion/sources/test_sec_edgar.py -v -k intake_pdf`
Expected: fail — `poll_edgar` doesn't call `_resolve_primary_document_url`/`intake_pdf` at all yet.

- [ ] **Step 3: Wire it into `poll_edgar`**

In `src/regradar/ingestion/sources/sec_edgar.py`, add the import:

```python
from regradar.ingestion.pdf_intake import PdfIntakeError, intake_pdf
```

Update the insertion loop (from Task 1's refactored shape) to:

```python
    inserted: list[NewFiling] = []
    for candidate in candidates:
        if candidate.source_document_id in existing_ids:
            continue
        filing = await insert_new_filing(db, FilingSource.SEC, candidate)
        if filing is None:
            continue
        inserted.append(candidate)

        cik = _extract_cik_from_filing_url(candidate.filing_url)
        if cik is not None:
            document_url = await _resolve_primary_document_url(
                cik, candidate.source_document_id, settings.sec_edgar_user_agent
            )
            if document_url is not None:
                try:
                    await intake_pdf(document_url, filing.id, db)
                except PdfIntakeError as exc:
                    logger.warning(
                        "PDF intake failed for filing %s: %s", filing.id, exc
                    )
```

This runs after `insert_new_filing`'s own `db.commit()`-free nested insert — check whether `insert_new_filing` (Task 1) commits itself or only adds within `begin_nested()`; per its Task 1 implementation, it does NOT call the outer `await db.commit()` (that still happens once at the end of `poll_edgar`, same as before this refactor) — so `filing.id` is available immediately after `db.add()` inside the nested transaction because SQLAlchemy's ORM assigns the client-side-generated UUID default (`default=uuid.uuid4` on `Filing.id`) at object-construction time, not at flush/commit time. Confirm this assumption holds by checking `Filing.id`'s column definition (`mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)` per the earlier model read) — a Python-side `default=` callable populates `.id` immediately on `Filing(...)` construction, before any DB round-trip, so `filing.id` is safely available here without needing to flush/commit first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit/ingestion/sources/test_sec_edgar.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Document FDA/FINRA's non-applicability**

In `src/regradar/ingestion/sources/fda_rss.py`'s module docstring, add a paragraph (don't remove anything existing):

```
PDF intake (ingestion/pdf_intake.py, wired into sec_edgar.py) does not
apply here — this feed's entries link to HTML news pages on fda.gov, not
PDF documents; there's no document URL in the feed data to archive.
```

In `src/regradar/ingestion/sources/finra_feed.py`'s module docstring, add a paragraph:

```
PDF intake (ingestion/pdf_intake.py, wired into sec_edgar.py) does not
apply here — this connector polls the Reg SHO Threshold List, a tabular
JSON dataset with no per-item document at all (every row shares the same
static API endpoint URL); there is no PDF to archive.
```

- [ ] **Step 6: Run the full unit suite, ruff, and mypy**

Run: `PYTHONPATH=./src /Users/SHREYPATEL/Documents/RegRadar/.venv/bin/python -m pytest tests/unit -v`, `... -m ruff check .`, `... -m mypy src/`
Expected: all clean.

- [ ] **Step 7: Commit**

```bash
git add src/regradar/ingestion/sources/sec_edgar.py src/regradar/ingestion/sources/fda_rss.py src/regradar/ingestion/sources/finra_feed.py tests/unit/ingestion/sources/test_sec_edgar.py
git commit -m "Wire PDF/document intake into the SEC EDGAR connector"
```

---

## Task 5: Live verification — a real EDGAR filing, ingested and processed end-to-end for the first time

This is the actual payoff of the whole effort: prove that a genuinely, freshly-polled EDGAR filing — not a manually-inserted test row — flows all the way through ingestion, PDF/document archival, chunking, embedding, retrieval, extraction, summarization, and delivery, exactly as a real production run would.

**Files:** none (manual verification, no code changes).

- [ ] **Step 1: Real EDGAR poll**

Start real Postgres (`docker start infra-postgres-1`). Write a short throwaway script (not committed) that calls `poll_edgar()` directly against a real `SourceConfig` row and a real DB session — no mocking. Confirm: at least one new `Filing` row is inserted, and (for at least one of them, ideally a 10-K/10-Q with a real primary document) `Filing.raw_pdf_s3_key` is genuinely set — check it against real S3 (`aws s3 ls` or the boto3 client) to confirm the object actually exists there, not just that the DB column is non-null.

- [ ] **Step 2: Real end-to-end pipeline run**

Using real local Ollama (`ollama serve`, per the established pattern — `llama3.1` and `llama3.2:1b` both already pulled), real Postgres, and real S3: call `enqueue_filing_processing()` or directly invoke `_run_pipeline_for_filing()` against the filing_id from Step 1. Confirm, all genuinely produced (not manually seeded) from this one real filing:
  - `raw_text` is non-empty and looks like real filing content (not garbled — if the primary document was HTML, confirm the extracted text reads as prose, not raw markup).
  - Real `filing_chunks` rows exist with real embeddings.
  - `domain`/`risk_level`/`classification_confidence` are set on the `Filing` row.
  - A real `extractions` row exists with genuine obligations/deadlines (or an honestly-empty list, if the filing type genuinely has none — a 10-K risk-factors section should produce at least something).
  - A real `briefs` row exists with all four fields populated.
  - If `SLACK_WEBHOOK_URL`/`SENDGRID_API_KEY`/`DELIVERY_EMAIL_RECIPIENT` are configured (they are, per AGENT-10), confirm real `deliveries` rows and that the Slack/email alerts for this real filing actually arrive — visually confirm with the user, same as AGENT-10's own verification.
  - `filing.status` ends at `FilingStatus.COMPLETE`.

- [ ] **Step 3: Clean up**

Delete the test filing and all its cascaded rows (chunks, extraction, brief, deliveries). Stop Ollama and the Postgres container. Remove the throwaway script and any copied `.env`.

- [ ] **Step 4: Update project memory**

Record that the "known remaining gap" flagged since AGENT-05/06 is now closed, with what was actually verified — this happens outside the plan file, as a memory update once the ticket is complete.

---

## Self-Review Notes

- **Spec coverage:** The user's instruction ("fix all the gap and make it run smoothly") is covered by: (1) the actual wiring (Task 4), (2) the real blocker discovered during investigation — EDGAR's feed only gives an index page, not a document URL (Task 2) — fixed with a verified-live, authoritative resolution method rather than a guess, (3) the second real blocker — most real EDGAR documents are HTML, which the existing PDF-only extractor would choke on (Task 3) — fixed with content-sniffing dispatch, (4) closing the FDA/FINRA ambiguity honestly rather than pretending to wire something that doesn't apply (Task 4, Step 5), (5) genuine end-to-end proof on real, non-test data (Task 5) — the actual "smoothly" claim, not just unit tests.
- **Placeholder scan:** No TBD/TODO markers. Task 4's test helper names are flagged as "adapt to the file's actual current names" rather than a fabricated exact match — this is deliberate honesty about a detail the planning research didn't fully capture (the file's complete helper list), not a placeholder; the actual test *content* (what's mocked, what's asserted) is fully specified.
- **Type consistency:** `insert_new_filing`'s return type (`Filing | None`) is used consistently in Task 4's `poll_edgar` (checked via `if filing is None: continue`, then `filing.id` used directly). `_resolve_primary_document_url`'s `str | None` return is checked before `intake_pdf` is ever called. `extract_text_and_tables`'s renamed `document_bytes` parameter and `fetch_document_bytes`'s rename are applied consistently at their one real call site in `pipeline_tasks.py` (Task 3, Step 5).
