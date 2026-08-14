# AGENT-05 — PDF Extraction, Chunk Persistence & Local Embedding

## Context

AGENT-01→04 built the pipeline and pure `chunk_filing(text, tables) -> list[Chunk]`. Three real
gaps existed before RAG chunking/embedding could function end-to-end, none chartered as their own
ticket in the Feature Ticket List:

1. **No PDF-to-text extraction.** `chunk_filing()` assumes text already exists; nothing reads the
   stored S3 PDF (`Filing.raw_pdf_s3_key`, set by ING-05's `pdf_intake.intake_pdf()`) and turns it
   into text. `workers/pipeline_tasks.py` still runs the pipeline with `raw_text=""`.
2. **No table detection.** `chunk_filing()` always receives `tables=[]` — nothing produces real
   `TableBlock`s.
3. **No chunk persistence.** The literal AGENT-05 ticket's `embed_chunks(chunks: list[Chunk]) ->
   None` signature does an UPDATE into `filing_chunks.embedding`, presupposing rows already exist
   — but nothing INSERTs `chunk_filing()`'s output into the DB.

Per explicit user instruction, this ticket's scope is expanded to close all three, plus the
embedding call itself — one working pipeline, not four disconnected pieces.

**Explicitly out of scope:** `pdf_intake.intake_pdf()` still isn't called by any real ingestion
connector (EDGAR/FDA/FINRA) — this ticket handles the case where `raw_pdf_s3_key` *is* set
(falling back gracefully to `raw_text=""` when it isn't), but doesn't wire PDF archiving into the
connectors themselves. That's a further, separate gap.

## Embedding provider: local Ollama, real schema change

`nomic-embed-text` (pulled via Ollama, live-verified) produces **768-dimension** vectors — not
OpenAI's 1536. Unlike AGENT-03's model swap (which didn't touch the schema), this requires a real
migration: `filing_chunks.embedding` changes from `VECTOR(1536)` to `VECTOR(768)`. The table is
currently empty (nothing has ever written to it), so this is a clean type change with no data
migration.

New config: `local_embedding_model: str = Field(default="nomic-embed-text", alias=
"LOCAL_EMBEDDING_MODEL")`, `use_local_embeddings: bool = Field(default=False, alias=
"USE_LOCAL_EMBEDDINGS")` (code default `False`, matching every other ADR-05 toggle; `.env` has
`USE_LOCAL_EMBEDDINGS=true` set locally). If the real-OpenAI path is ever enabled later, it must
request `dimensions=768` from `text-embedding-3-small` (OpenAI's embeddings API supports
truncated output dimensions) to stay schema-compatible with the migrated column — noted in code
as a requirement, not exercised or tested now.

## `src/regradar/rag/pdf_extraction.py` (new)

```python
def fetch_pdf_bytes(s3_key: str) -> bytes:
    """Download a PDF from S3 via the existing get_s3_client()."""


def extract_text_and_tables(pdf_bytes: bytes) -> tuple[str, list[TableBlock]]:
    """Per page: page.extract_text() builds the page's text (joined across
    pages with "\\n\\n"). For each page.find_tables() result,
    page.within_bbox(table.bbox).extract_text() gives that table's own
    rendered text, located within the page's full text via str.find() —
    both sides use the same extract_text() rendering, so this locates
    reliably (verified against a real synthetic PDF containing an actual
    table, generated via reportlab specifically to validate this design).
    A table whose text can't be located in the page text is skipped, never
    guessed at — same "soft heuristic, no silent wrong answers" precedent
    as chunk_filing's own section-boundary detection.
    """
```

New dependency: `pdfplumber`.

## `src/regradar/rag/embeddings.py` (new)

```python
def embed_chunks(filing_id: UUID, chunks: list[Chunk], db: AsyncSession) -> None:
    """Signature deviates from the ticket's literal list[Chunk] -> None —
    filing_id and db are added because the ticket's version presupposes
    filing_chunks rows already exist, which nothing produces. This
    function owns both steps:
      1. INSERT a filing_chunks row per Chunk (embedding=None initially).
      2. Batch the chunk texts (up to 100 per batch), call the embedding
         client (local Ollama or real OpenAI, chosen the same way
         triage_agent._get_llm_client() chooses between HF/local paths),
         retrying a failed batch up to twice with exponential backoff.
      3. UPDATE each row's embedding with its resulting vector.
    All three steps run inside one DB transaction. A failure at any point
    rolls back to zero persisted rows for this filing — stronger than the
    ticket's "retry twice then explicit error state": no partial
    embedding state can ever exist in the table. Logs token count per
    batch call (feeds later EVAL-06 cost tracking, same pattern as
    classify_filing()).
    """
```

## Pipeline wiring (`workers/pipeline_tasks.py`)

`_run_pipeline_for_filing` extended:

```python
raw_text = ""
tables: list[TableBlock] = []
if filing.raw_pdf_s3_key:
    try:
        pdf_bytes = fetch_pdf_bytes(filing.raw_pdf_s3_key)
        raw_text, tables = extract_text_and_tables(pdf_bytes)
    except Exception as exc:
        logger.warning("PDF extraction failed for filing %s: %s", filing_id, exc)
        # raw_text stays "" — graceful degradation, matches this
        # project's existing failure philosophy (never crash the
        # pipeline over one filing's bad/missing PDF)

state = PipelineState(filing_id=filing.id, raw_text=raw_text)
result = build_graph().invoke(state)  # triage now classifies against real text

if raw_text:
    chunks = chunk_filing(raw_text, tables)
    await embed_chunks(filing.id, chunks, db)

# ...existing triage/classification persistence, unchanged...
```

Extraction happens *before* `PipelineState` construction specifically so triage classifies
against real text — this also resolves AGENT-01→04's `raw_text=""` placeholder, not just
AGENT-05's own chunking need. Chunking+embedding run after the graph (this filing's own triage
doesn't depend on its own chunks existing; they exist to make this filing searchable for *future*
filings' retrieval in AGENT-06).

## Tests

- Unit tests mock: S3 fetch (`fetch_pdf_bytes`), the embedding client, and the DB session — no
  real Postgres, Ollama, or S3 call in default test runs.
- A synthetic PDF fixture with a real table (generated via `reportlab`, matching the one used to
  validate this design) checked into `tests/fixtures/sample_filings/`, used to test
  `extract_text_and_tables()` against real PDF bytes (parsing itself isn't mocked — only the S3
  fetch step is).
- Live verification (briefly, per this project's policy — started only for the check, stopped
  immediately after): real Postgres for the migration's upgrade/downgrade/upgrade cycle and the
  `embed_chunks()` transaction; real Ollama for the actual embedding call.
