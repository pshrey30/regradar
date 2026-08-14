# AGENT-05 — PDF Extraction, Chunk Persistence & Local Embedding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three related gaps in one working pipeline: extract text+tables from a filing's
stored S3 PDF, persist AGENT-04's `Chunk` output as real `filing_chunks` rows, and embed them via
a local Ollama model — then wire all of it into `process_filing` so triage classifies against
real text and future filings' retrieval has something to search.

**Architecture:** Two new pure-ish modules — `rag/pdf_extraction.py` (PDF → text/tables, one S3
call) and `rag/embeddings.py` (chunks → embedded DB rows, one DB transaction) — plus a schema
migration (`filing_chunks.embedding` moves from `VECTOR(1536)` to `VECTOR(768)` to match the local
embedding model) and an extension to `workers/pipeline_tasks.py`'s existing `_run_pipeline_for_filing`.

**Tech Stack:** `pdfplumber` (new dependency, PDF parsing), `openai` SDK pointed at local Ollama
(`nomic-embed-text`, already pulled and verified to produce 768-dim vectors), `reportlab`
(dev-only, used once to generate a test fixture — not a runtime dependency).

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-08-14-agent-05-pdf-extraction-embedding-design.md`.
- `filing_chunks.embedding` changes from `VECTOR(1536)` to `VECTOR(768)` — the table is currently
  empty (nothing has ever written to it), so this is a clean type change, no data conversion.
- `nomic-embed-text` via Ollama produces exactly 768-dim vectors — live-verified during design.
- New config: `local_embedding_model` (default `"nomic-embed-text"`), `use_local_embeddings`
  (default `False` — code default matches every other ADR-05 toggle; `.env` already has
  `USE_LOCAL_EMBEDDINGS=true` set locally, done during design).
- `embed_chunks(filing_id: UUID, chunks: list[Chunk], db: AsyncSession) -> None` — deviates from
  the ticket's literal `list[Chunk] -> None` signature (which presupposes `filing_chunks` rows
  already exist). Embeds ALL chunks first (retrying each batch up to twice with exponential
  backoff), and only inserts DB rows — with embeddings already populated — in a single
  `db.add_all()` + `db.commit()` at the end. If embedding fails, nothing has touched the database
  at all yet, so there's no partial state to roll back — stronger than "retry then explicit error
  state."
- `extract_text_and_tables(pdf_bytes: bytes) -> tuple[str, list[TableBlock]]` never guesses a
  table's location — skips it if `str.find()` can't locate the table's own rendered text inside
  the page's rendered text.
- PDF extraction happens in `_run_pipeline_for_filing` *before* `PipelineState` is constructed, so
  triage classifies against real text. On extraction failure, log a warning and fall back to
  `raw_text=""` — never crash the pipeline over one bad/missing PDF.
- Chunking + embedding run *after* the graph invocation (this filing's own triage doesn't depend
  on its own chunks; they exist for future filings' retrieval).
- Automated tests never call real Ollama, real S3, or real Postgres by default. Live verification
  happens explicitly, briefly, with services started only for the check and stopped immediately
  after — per this project's established policy.

---

### Task 1: Config — `local_embedding_model`, `use_local_embeddings`

**Files:**
- Modify: `src/regradar/core/config.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.local_embedding_model: str` (default `"nomic-embed-text"`),
  `Settings.use_local_embeddings: bool` (default `False`), for Task 4.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`, inside `test_all_required_fields_present_loads_with_defaults`
(after the existing `assert settings.chunk_overlap_tokens == 50` line):

```python
    assert settings.use_local_embeddings is False
    assert settings.local_embedding_model == "nomic-embed-text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py -v -k all_required_fields`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'use_local_embeddings'`

- [ ] **Step 3: Write the implementation**

In `src/regradar/core/config.py`, add to the `# ── Local inference — portfolio/demo cost control
(ADR-05) ──` section, after the existing `use_local_hf_inference` field:

```python
    use_local_embeddings: bool = Field(default=False, alias="USE_LOCAL_EMBEDDINGS")
    local_embedding_model: str = Field(default="nomic-embed-text", alias="LOCAL_EMBEDDING_MODEL")
```

Add to `.env.example`, after the existing `USE_LOCAL_HF_INFERENCE=false` line:

```
USE_LOCAL_EMBEDDINGS=false
LOCAL_EMBEDDING_MODEL=nomic-embed-text
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/core/config.py .env.example tests/unit/test_config.py
git commit -m "Add local_embedding_model/use_local_embeddings config (AGENT-05)"
```

---

### Task 2: `filing_chunks.embedding` dimension migration

**Files:**
- Modify: `src/regradar/models/chunk.py`
- Create: `migrations/versions/0005_change_filing_chunks_embedding_dimension.py`

**Interfaces:**
- Produces: `FilingChunk.embedding` typed as `Vector(768)`, for Task 4's `embed_chunks`.

- [ ] **Step 1: Update the ORM model**

In `src/regradar/models/chunk.py`, change:

```python
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
```

to:

```python
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)
```

- [ ] **Step 2: Write the migration**

Create `migrations/versions/0005_change_filing_chunks_embedding_dimension.py`:

```python
"""Change filing_chunks.embedding from vector(1536) to vector(768).

AGENT-05 uses a local Ollama embedding model (nomic-embed-text, 768
dimensions) instead of OpenAI's text-embedding-3-small (1536), per the
cost-conscious local-inference policy established in AGENT-03. The table
is currently empty — nothing has ever written to filing_chunks.embedding
— so this is a clean type change with no data to convert.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-14
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector(768) USING NULL::vector(768)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector(1536) USING NULL::vector(1536)"
    )
```

- [ ] **Step 3: Run the migration against a real Postgres to verify it works**

Start Postgres briefly: `docker compose -f infra/docker-compose.yml up -d postgres`, wait for
health (`docker exec infra-postgres-1 pg_isready -U regradar`).

Run (with `DATABASE_URL` exported from `.env`, matching the pattern used in AGENT-02's migration
verification):

```bash
export DATABASE_URL=$(grep '^DATABASE_URL=' .env | cut -d= -f2-)
.venv/bin/alembic upgrade head
```

Expected: migration `0005` applies cleanly on top of `0004`.

Run: `.venv/bin/alembic downgrade -1`
Expected: no errors; `filing_chunks.embedding` reverts to `vector(1536)`.

Run: `.venv/bin/alembic upgrade head` again to leave the DB at head, then stop Postgres:
`docker compose -f infra/docker-compose.yml stop postgres`. Do not leave it running.

- [ ] **Step 4: Commit**

```bash
git add src/regradar/models/chunk.py migrations/versions/0005_change_filing_chunks_embedding_dimension.py
git commit -m "Change filing_chunks.embedding to vector(768) for local embeddings (AGENT-05)"
```

---

### Task 3: `rag/pdf_extraction.py` — `fetch_pdf_bytes` + `extract_text_and_tables`

**Files:**
- Create: `src/regradar/rag/pdf_extraction.py`
- Create: `tests/fixtures/sample_filings/synthetic_table_filing.pdf` (via the generation script
  below, run once)
- Create: `tests/unit/rag/test_pdf_extraction.py`
- Modify: `pyproject.toml` (add `pdfplumber` dependency)

**Interfaces:**
- Consumes: `regradar.rag.chunking.TableBlock` (AGENT-04), `regradar.core.s3_client.get_s3_client`
  (ING-05).
- Produces (for Task 5):
  - `def fetch_pdf_bytes(s3_key: str) -> bytes`
  - `def extract_text_and_tables(pdf_bytes: bytes) -> tuple[str, list[TableBlock]]`

- [ ] **Step 1: Add the `pdfplumber` dependency**

In `pyproject.toml`'s `dependencies` list, add `"pdfplumber>=0.11",` (alongside the existing
`"tiktoken>=0.7",` line — any position in the list is fine). Run:
`.venv/bin/pip install -e .` to install it.

- [ ] **Step 2: Generate the test fixture PDF**

Run this script once (it writes a real PDF file checked into the repo — not regenerated by
tests):

```python
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

doc = SimpleDocTemplate(
    "tests/fixtures/sample_filings/synthetic_table_filing.pdf", pagesize=letter
)
styles = getSampleStyleSheet()
elements = []

elements.append(Paragraph("Item 7. Management's Discussion and Analysis", styles["Heading1"]))
elements.append(
    Paragraph(
        "The Company reported the following segment results for the fiscal year, reflecting "
        "changes in revenue mix across its primary operating regions and product lines.",
        styles["Normal"],
    )
)
elements.append(Spacer(1, 12))

data = [
    ["Region", "Revenue($M)", "Growth(%)", "Headcount"],
    ["North America", "482.3", "6.1", "1204"],
    ["Europe", "311.7", "3.4", "876"],
    ["Asia Pacific", "198.5", "9.8", "542"],
]
table = Table(data)
table.setStyle(
    TableStyle(
        [
            ("GRID", (0, 0), (-1, -1), 1, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]
    )
)
elements.append(table)
elements.append(Spacer(1, 12))
elements.append(
    Paragraph(
        "Management believes that current liquidity and capital resources are sufficient "
        "to meet operating requirements for at least the next twelve months.",
        styles["Normal"],
    )
)

doc.build(elements)
```

This requires `reportlab` installed locally to run (`.venv/bin/pip install reportlab` — dev-only,
not added to `pyproject.toml`, since it's only needed to generate this one fixture file, not at
runtime or in CI).

Verified output of this exact script (confirmed during design):

```
=== FULL TEXT ===
Item 7. Management's Discussion and Analysis
The Company reported the following segment results for the fiscal year, reflecting changes in revenue
mix across its primary operating regions and product lines.
Region Revenue($M) Growth(%) Headcount
North America 482.3 6.1 1204
Europe 311.7 3.4 876
Asia Pacific 198.5 9.8 542
Management believes that current liquidity and capital resources are sufficient to meet operating
requirements for at least the next twelve months.
=== TABLES ===
TableBlock(start_char=207, end_char=322)
```

- [ ] **Step 3: Write the failing tests**

Create `tests/unit/rag/test_pdf_extraction.py`:

```python
"""Unit tests for PDF extraction (fetch_pdf_bytes, extract_text_and_tables).

fetch_pdf_bytes is tested with moto (no real AWS), following the same
settings-env + cache_clear pattern as tests/unit/ingestion/test_pdf_intake.py.
extract_text_and_tables is NOT mocked — it runs the real pdfplumber parser
against a real checked-in PDF fixture
(tests/fixtures/sample_filings/synthetic_table_filing.pdf), generated once
via the reportlab script in this plan's Task 3.
"""

from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from regradar.rag import pdf_extraction
from regradar.rag.chunking import TableBlock
from regradar.rag.pdf_extraction import extract_text_and_tables, fetch_pdf_bytes

FIXTURE_PDF = Path(__file__).parent.parent.parent / "fixtures" / "sample_filings" / "synthetic_table_filing.pdf"

BUCKET_NAME = "test-regradar-bucket"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("S3_BUCKET_NAME", BUCKET_NAME)
    monkeypatch.setenv("S3_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("HUGGINGFACE_API_TOKEN", "test")
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", "RegRadar/1.0 (test@example.com)")
    pdf_extraction.get_settings.cache_clear()
    yield
    pdf_extraction.get_settings.cache_clear()


@mock_aws
def test_fetch_pdf_bytes_downloads_from_s3() -> None:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=BUCKET_NAME)
    client.put_object(Bucket=BUCKET_NAME, Key="filings/test.pdf", Body=b"fake pdf bytes")

    result = fetch_pdf_bytes("filings/test.pdf")

    assert result == b"fake pdf bytes"


def test_extract_text_and_tables_returns_expected_text_and_table_span() -> None:
    pdf_bytes = FIXTURE_PDF.read_bytes()

    text, tables = extract_text_and_tables(pdf_bytes)

    assert text.startswith("Item 7. Management's Discussion and Analysis")
    assert "North America 482.3 6.1 1204" in text
    assert len(tables) == 1
    assert tables[0] == TableBlock(start_char=207, end_char=322)


def test_extract_text_and_tables_table_span_contains_table_content() -> None:
    pdf_bytes = FIXTURE_PDF.read_bytes()

    text, tables = extract_text_and_tables(pdf_bytes)

    table_slice = text[tables[0].start_char : tables[0].end_char]
    assert "Region Revenue($M) Growth(%) Headcount" in table_slice
    assert "Asia Pacific 198.5 9.8 542" in table_slice
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/rag/test_pdf_extraction.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.rag.pdf_extraction'`

- [ ] **Step 5: Write the implementation**

Create `src/regradar/rag/pdf_extraction.py`:

```python
"""PDF text and table extraction — reads a filing's stored S3 PDF and
turns it into plain text plus detected table regions, ready for
rag.chunking.chunk_filing().
"""

import io

import pdfplumber

from regradar.core.s3_client import get_s3_client
from regradar.core.config import get_settings
from regradar.rag.chunking import TableBlock


def fetch_pdf_bytes(s3_key: str) -> bytes:
    """Download a filing's PDF from S3."""
    settings = get_settings()
    client = get_s3_client()
    response = client.get_object(Bucket=settings.s3_bucket_name, Key=s3_key)
    return response["Body"].read()


def extract_text_and_tables(pdf_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Extract plain text and detected table regions from a PDF.

    Per page, page.extract_text() builds that page's text (pages joined
    with "\\n\\n"). For each page.find_tables() result,
    page.within_bbox(table.bbox).extract_text() gives that table's own
    rendered text, located within the page's full text via str.find() —
    both use the same extract_text() rendering, so this locates reliably.
    A table whose text can't be found in the page text is skipped, never
    guessed at.
    """
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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/rag/test_pdf_extraction.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml tests/fixtures/sample_filings/synthetic_table_filing.pdf src/regradar/rag/pdf_extraction.py tests/unit/rag/test_pdf_extraction.py
git commit -m "Add PDF text and table extraction (AGENT-05)"
```

---

### Task 4: `rag/embeddings.py` — `embed_chunks`

**Files:**
- Create: `src/regradar/rag/embeddings.py`
- Create: `tests/unit/rag/test_embeddings.py`

**Interfaces:**
- Consumes: `regradar.rag.chunking.Chunk` (AGENT-04), `regradar.models.chunk.FilingChunk`
  (Task 2), `Settings.use_local_embeddings`/`local_embedding_model`/`local_llm_base_url`
  (Task 1 + existing).
- Produces (for Task 5): `def embed_chunks(filing_id: UUID, chunks: list[Chunk], db:
  AsyncSession) -> None`, `class EmbeddingError(Exception)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/rag/test_embeddings.py`:

```python
"""Unit tests for embed_chunks. The OpenAI-compatible client is always
mocked — no real Ollama or OpenAI call in these tests.
"""

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from regradar.models.chunk import FilingChunk
from regradar.rag.chunking import Chunk
from regradar.rag.embeddings import EmbeddingError, embed_chunks


def _make_chunks(count: int) -> list[Chunk]:
    return [
        Chunk(
            chunk_index=i,
            chunk_text=f"chunk text {i}",
            section_reference=None,
            token_count=3,
            is_table=False,
        )
        for i in range(count)
    ]


def _mock_embedding_response(count: int) -> MagicMock:
    response = MagicMock()
    response.data = [MagicMock(embedding=[0.1] * 768) for _ in range(count)]
    response.usage = MagicMock(total_tokens=30)
    return response


async def test_embed_chunks_inserts_rows_with_embeddings() -> None:
    filing_id = uuid.uuid4()
    chunks = _make_chunks(2)
    mock_client = MagicMock()
    mock_client.embeddings.create.return_value = _mock_embedding_response(2)
    mock_db = AsyncMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        await embed_chunks(filing_id, chunks, mock_db)

    mock_db.add_all.assert_called_once()
    added_rows = mock_db.add_all.call_args[0][0]
    assert len(added_rows) == 2
    assert all(isinstance(row, FilingChunk) for row in added_rows)
    assert all(row.filing_id == filing_id for row in added_rows)
    assert all(row.embedding == [0.1] * 768 for row in added_rows)
    assert [row.chunk_index for row in added_rows] == [0, 1]
    mock_db.commit.assert_awaited_once()


async def test_embed_chunks_batches_at_100() -> None:
    filing_id = uuid.uuid4()
    chunks = _make_chunks(150)
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = [
        _mock_embedding_response(100),
        _mock_embedding_response(50),
    ]
    mock_db = AsyncMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        await embed_chunks(filing_id, chunks, mock_db)

    assert mock_client.embeddings.create.call_count == 2
    first_call_texts = mock_client.embeddings.create.call_args_list[0].kwargs["input"]
    second_call_texts = mock_client.embeddings.create.call_args_list[1].kwargs["input"]
    assert len(first_call_texts) == 100
    assert len(second_call_texts) == 50


async def test_embed_chunks_retries_failed_batch_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    filing_id = uuid.uuid4()
    chunks = _make_chunks(1)
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = [
        RuntimeError("connection failed"),
        _mock_embedding_response(1),
    ]
    mock_db = AsyncMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        await embed_chunks(filing_id, chunks, mock_db)

    assert mock_client.embeddings.create.call_count == 2
    mock_db.commit.assert_awaited_once()


async def test_embed_chunks_raises_after_all_retries_fail_and_never_touches_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    filing_id = uuid.uuid4()
    chunks = _make_chunks(1)
    mock_client = MagicMock()
    mock_client.embeddings.create.side_effect = RuntimeError("connection failed")
    mock_db = AsyncMock()

    with patch(
        "regradar.rag.embeddings._get_embedding_client",
        return_value=(mock_client, "nomic-embed-text"),
    ):
        with pytest.raises(EmbeddingError):
            await embed_chunks(filing_id, chunks, mock_db)

    assert mock_client.embeddings.create.call_count == 3
    mock_db.add_all.assert_not_called()
    mock_db.commit.assert_not_awaited()


async def test_embed_chunks_does_nothing_for_empty_chunk_list() -> None:
    mock_db = AsyncMock()

    await embed_chunks(uuid.uuid4(), [], mock_db)

    mock_db.add_all.assert_not_called()
    mock_db.commit.assert_not_awaited()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/rag/test_embeddings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.rag.embeddings'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/rag/embeddings.py`:

```python
"""Embed AGENT-04's Chunk output via a local Ollama model (or real OpenAI,
untested/unexercised for now) and persist as filing_chunks rows.

embed_chunks's signature deviates from the literal ticket text
(list[Chunk] -> None) — filing_id and db are required because nothing
else inserts filing_chunks rows for these chunks; this function owns
that step too.
"""

import logging
import time
import uuid
from uuid import UUID

from openai import OpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from regradar.core.config import get_settings
from regradar.models.chunk import FilingChunk
from regradar.rag.chunking import Chunk

logger = logging.getLogger(__name__)

BATCH_SIZE = 100
MAX_ATTEMPTS = 3


class EmbeddingError(Exception):
    """Raised when embedding a batch fails after retrying twice."""


def _get_embedding_client() -> tuple[OpenAI, str]:
    settings = get_settings()
    if settings.use_local_embeddings:
        return (
            OpenAI(base_url=settings.local_llm_base_url, api_key="ollama-local"),
            settings.local_embedding_model,
        )
    return OpenAI(api_key=settings.openai_api_key.get_secret_value()), "text-embedding-3-small"


def _embed_batch(client: OpenAI, model: str, texts: list[str]) -> list[list[float]]:
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt > 0:
            time.sleep(2**attempt)
        try:
            response = client.embeddings.create(model=model, input=texts)
            token_count = response.usage.total_tokens if response.usage else None
            logger.info(
                "Embedding batch: model=%s size=%d tokens=%s", model, len(texts), token_count
            )
            return [item.embedding for item in response.data]
        except Exception as exc:  # noqa: BLE001 — any failure retries, then raises EmbeddingError
            last_error = exc
            logger.warning("Embedding batch attempt %d failed: %s", attempt + 1, exc)

    raise EmbeddingError(f"Embedding failed after retries: {last_error}") from last_error


async def embed_chunks(filing_id: UUID, chunks: list[Chunk], db: AsyncSession) -> None:
    """Embed every chunk, then insert filing_chunks rows with embeddings
    already populated, in a single commit. If embedding fails, nothing
    is added to the session and the database is never touched — no
    partial state can exist.
    """
    if not chunks:
        return

    client, model = _get_embedding_client()

    embeddings: list[list[float]] = []
    for batch_start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[batch_start : batch_start + BATCH_SIZE]
        texts = [c.chunk_text for c in batch]
        embeddings.extend(_embed_batch(client, model, texts))

    rows = [
        FilingChunk(
            id=uuid.uuid4(),
            filing_id=filing_id,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            section_reference=chunk.section_reference,
            token_count=chunk.token_count,
            is_table=chunk.is_table,
            embedding=embedding,
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]
    db.add_all(rows)
    await db.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/rag/test_embeddings.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/rag/embeddings.py tests/unit/rag/test_embeddings.py
git commit -m "Add embed_chunks: batched local embedding + chunk persistence (AGENT-05)"
```

---

### Task 5: Wire extraction + chunking + embedding into `process_filing`

**Files:**
- Modify: `src/regradar/workers/pipeline_tasks.py`
- Modify: `tests/unit/workers/test_pipeline_tasks.py`

**Interfaces:**
- Consumes: `fetch_pdf_bytes`, `extract_text_and_tables` (Task 3), `embed_chunks` (Task 4),
  `regradar.rag.chunking.chunk_filing` (AGENT-04).
- Produces: updated `_run_pipeline_for_filing` — same overall shape, extended with the extraction
  and chunk/embed steps.

- [ ] **Step 1: Update the two existing tests that construct a `MagicMock()` filing**

`tests/unit/workers/test_pipeline_tasks.py`'s `test_process_filing_persists_classification_on_success`
and `test_process_filing_marks_needs_classification_when_triage_fails` both do `filing =
MagicMock()`. A bare `MagicMock()`'s `.raw_pdf_s3_key` attribute is truthy by default (it's itself
a `MagicMock`), which would make the new code in this task try to actually fetch/extract a PDF in
these tests. Add `filing.raw_pdf_s3_key = None` right after `filing.id = filing_id` in **both**
tests, so they continue to exercise only the triage path, unaffected by this task's changes.

- [ ] **Step 2: Write the new failing tests**

Add to `tests/unit/workers/test_pipeline_tasks.py`, after the existing
`test_process_filing_marks_needs_classification_when_triage_fails` test. First add these imports
near the top of the file, alongside the existing `from regradar.models.enums import ...` line:

```python
from regradar.rag.chunking import Chunk
```

Then the new tests:

```python
def test_process_filing_extracts_text_and_embeds_chunks_when_pdf_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            invoke=lambda state: {
                "domain": FilingDomain.FINANCIAL,
                "risk_level": RiskLevel.LOW,
                "classification_confidence": 0.9,
            }
        ),
    )
    monkeypatch.setattr(
        pipeline_tasks_module, "fetch_pdf_bytes", lambda s3_key: b"fake pdf bytes"
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "extract_text_and_tables",
        lambda pdf_bytes: ("Item 1. Real extracted filing text.", []),
    )
    fake_chunks = [
        Chunk(
            chunk_index=0,
            chunk_text="Item 1. Real extracted filing text.",
            section_reference="Item 1.",
            token_count=6,
            is_table=False,
        )
    ]
    monkeypatch.setattr(
        pipeline_tasks_module, "chunk_filing", lambda text, tables: fake_chunks
    )
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    mock_embed_chunks.assert_awaited_once_with(filing_id, fake_chunks, mock_db)


def test_process_filing_falls_back_to_empty_text_when_pdf_extraction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = "filings/abc123.pdf"

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )

    captured_state = {}

    def _fake_invoke(state):
        captured_state["raw_text"] = state.raw_text
        return {
            "domain": FilingDomain.FINANCIAL,
            "risk_level": RiskLevel.LOW,
            "classification_confidence": 0.9,
        }

    monkeypatch.setattr(
        pipeline_tasks_module, "build_graph", lambda: MagicMock(invoke=_fake_invoke)
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "fetch_pdf_bytes",
        MagicMock(side_effect=RuntimeError("S3 unavailable")),
    )
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    assert captured_state["raw_text"] == ""
    mock_embed_chunks.assert_not_awaited()


def test_process_filing_skips_extraction_when_no_pdf_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filing_id = uuid.uuid4()
    filing = MagicMock()
    filing.id = filing_id
    filing.raw_pdf_s3_key = None

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=filing)
    mock_db.commit = AsyncMock()

    mock_session_factory = MagicMock()
    mock_session_factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

    import regradar.workers.pipeline_tasks as pipeline_tasks_module

    monkeypatch.setattr(
        pipeline_tasks_module, "get_session_factory", lambda: mock_session_factory
    )
    monkeypatch.setattr(
        pipeline_tasks_module,
        "build_graph",
        lambda: MagicMock(
            invoke=lambda state: {
                "domain": FilingDomain.FINANCIAL,
                "risk_level": RiskLevel.LOW,
                "classification_confidence": 0.9,
            }
        ),
    )
    mock_fetch = MagicMock()
    monkeypatch.setattr(pipeline_tasks_module, "fetch_pdf_bytes", mock_fetch)
    mock_embed_chunks = AsyncMock()
    monkeypatch.setattr(pipeline_tasks_module, "embed_chunks", mock_embed_chunks)

    process_filing.run(str(filing_id))

    mock_fetch.assert_not_called()
    mock_embed_chunks.assert_not_awaited()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v -k extracts_text_and_embeds`
Expected: FAIL — `ImportError` or `AttributeError` since `pipeline_tasks_module.fetch_pdf_bytes`
doesn't exist yet.

- [ ] **Step 4: Write the implementation**

In `src/regradar/workers/pipeline_tasks.py`, add these imports alongside the existing ones:

```python
from regradar.rag.chunking import chunk_filing
from regradar.rag.embeddings import embed_chunks
from regradar.rag.pdf_extraction import extract_text_and_tables, fetch_pdf_bytes
```

Replace `_run_pipeline_for_filing`:

```python
async def _run_pipeline_for_filing(filing_id: str) -> None:
    session_factory = get_session_factory()
    async with session_factory() as db:
        filing = await db.get(Filing, uuid.UUID(filing_id))
        if filing is None:
            logger.warning("Filing %s not found — skipping pipeline run", filing_id)
            return

        raw_text = ""
        tables: list = []
        if filing.raw_pdf_s3_key:
            try:
                pdf_bytes = fetch_pdf_bytes(filing.raw_pdf_s3_key)
                raw_text, tables = extract_text_and_tables(pdf_bytes)
            except Exception as exc:
                logger.warning("PDF extraction failed for filing %s: %s", filing_id, exc)

        state = PipelineState(filing_id=filing.id, raw_text=raw_text)
        result = build_graph().invoke(state)

        if result["domain"] is None:
            filing.status = FilingStatus.NEEDS_CLASSIFICATION
        else:
            filing.domain = result["domain"]
            filing.risk_level = result["risk_level"]
            filing.classification_confidence = result["classification_confidence"]
            filing.status = FilingStatus.CLASSIFYING
        await db.commit()

        if raw_text:
            chunks = chunk_filing(raw_text, tables)
            await embed_chunks(filing.id, chunks, db)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/workers/test_pipeline_tasks.py -v`
Expected: PASS (all tests in the file — the two updated pre-existing tests, the three new ones,
and every other test untouched by this change)

- [ ] **Step 6: Commit**

```bash
git add src/regradar/workers/pipeline_tasks.py tests/unit/workers/test_pipeline_tasks.py
git commit -m "Wire PDF extraction, chunking, and embedding into process_filing (AGENT-05)"
```

---

### Task 6: Live verification, full-suite check, lint, mypy, push

**Files:** none (verification only)

- [ ] **Step 1: Live-verify the full extraction + embedding flow against real Postgres + Ollama**

Start both services briefly:

```bash
docker compose -f infra/docker-compose.yml up -d postgres
/opt/homebrew/opt/ollama/bin/ollama serve > /tmp/ollama-serve.log 2>&1 &
```

Wait for both to be ready (`docker exec infra-postgres-1 pg_isready -U regradar`, `curl -s
http://localhost:11434`). Confirm `nomic-embed-text` is still pulled: `/opt/homebrew/opt/ollama/bin/ollama
list` (it was pulled during design verification — re-pull with `ollama pull nomic-embed-text` if
it's missing).

Run a real end-to-end check (adjust paths/imports as needed for a quick interactive script):
insert a `Filing` row with `raw_pdf_s3_key` pointing at the real test PDF already in the S3 bucket
(`filings/3df79d34abbca99308e79cb94461c1893582604d68329a41fd4bec1885e6adb4.pdf`, left over from
ING-05's live verification — note this one is just a placeholder "Dummy PDF file" with minimal
text, so this mainly confirms the extraction+embedding *pipeline plumbing* works against real
infra, not meaningful chunk content), then call `_run_pipeline_for_filing` directly and confirm a
`filing_chunks` row was created with a non-null 768-dimension `embedding`.

Expected: no errors; a real `filing_chunks` row exists in Postgres with a real embedding vector.

- [ ] **Step 2: Stop both services**

```bash
pkill -f "ollama serve"
docker compose -f infra/docker-compose.yml stop postgres
```

Confirm both are down: `curl -s http://localhost:11434` should fail to connect; `docker ps` should
show no running containers.

- [ ] **Step 3: Run the full default test suite**

Run: `.venv/bin/pytest -v --ignore=tests/integration/test_flows.py`
Expected: PASS (all tests; `test_flows.py` needs a live Postgres this ticket's default test run
doesn't start, consistent with prior tickets).

- [ ] **Step 4: Run lint and type checks**

Run: `.venv/bin/ruff check src/regradar/rag src/regradar/core/config.py src/regradar/models/chunk.py src/regradar/workers/pipeline_tasks.py tests/unit/rag tests/unit/test_config.py tests/unit/workers/test_pipeline_tasks.py`
Run: `.venv/bin/mypy src/regradar/rag src/regradar/core/config.py src/regradar/models/chunk.py src/regradar/workers/pipeline_tasks.py`
Expected: no errors. Fix any and commit the fix as part of this task.

- [ ] **Step 5: Push the branch**

```bash
git push -u origin agent-05-embedding-pgvector
```

Do not merge to `master` — merging is a separate explicit step the user confirms. Note: this
branch is based on `agent-04-pdf-chunking`, which must merge to `master` first (or this branch
gets rebased) before this ticket can merge cleanly.
