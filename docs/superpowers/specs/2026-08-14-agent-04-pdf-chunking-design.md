# AGENT-04 — PDF Chunking Pipeline

## Context

AGENT-01→03 (merged to `master`) built the LangGraph pipeline and the real Triage Agent. The
`rag/` package exists but is empty. The `filing_chunks` DB table (FOUND-02) has columns
`chunk_index`, `chunk_text`, `section_reference`, `token_count`, `embedding` (nullable, populated
by a future AGENT-05), `is_table`. `tiktoken` is an existing, unused dependency.

No PDF-to-text extraction or table-detection utility exists yet — `workers/pipeline_tasks.py`
still runs the pipeline against `raw_text=""` with a comment noting AGENT-04 "extracts filing
text from the stored S3 PDF." However, the ticket's own AI Coding Prompt specifies
`chunk_filing(text: str, tables: list[TableBlock]) -> list[Chunk]` — it takes **already-extracted**
text and **already-detected** table regions as parameters. This means PDF parsing and table
detection are not part of this ticket's function; some future ticket supplies those inputs.
`workers/pipeline_tasks.py`'s `raw_text=""` placeholder is unaffected by this ticket — wiring
`chunk_filing` into the pipeline itself is out of scope here too (no chunking node exists in
`agents/graph.py` yet; that's AGENT-05/06's concern, which write chunks to the DB and embed them).

## `src/regradar/rag/chunking.py` (new module)

```python
class TableBlock(BaseModel):
    start_char: int
    end_char: int  # half-open [start_char, end_char) into the same `text` chunk_filing receives

class Chunk(BaseModel):
    chunk_index: int
    chunk_text: str
    section_reference: str | None
    token_count: int
    is_table: bool

def chunk_filing(text: str, tables: list[TableBlock]) -> list[Chunk]:
    ...
```

- **Chunk size/overlap**: token-based, using `tiktoken.get_encoding("cl100k_base")` (the encoding
  OpenAI's `text-embedding-3-small` — the model AGENT-05 will use — is built on). Defaults come
  from new config: `chunk_size_tokens: int = Field(default=512, alias="CHUNK_SIZE_TOKENS")`,
  `chunk_overlap_tokens: int = Field(default=50, alias="CHUNK_OVERLAP_TOKENS")`.
- **Heading detection**: a single pass over `text` locates every heading occurrence as
  `(char_position, heading_text)`, using
  `^(Item\s+\d+[A-Z]?\.?|Section\s+\d+(\.\d+)*:?|Article\s+[IVXLCDM]+\.?|\d+(\.\d+)*\s+[A-Z])`
  (multiline). This single heading list serves two purposes:
  - **`section_reference`**: for each chunk, the most recent heading at or before the chunk's
    starting character offset (`None` if none precedes it).
  - **Section-boundary preference**: when a chunk would otherwise hit the hard
    `chunk_size_tokens` cutoff, look back up to ~15% of `chunk_size_tokens` (≈75 tokens at the
    512 default) for a heading. If one falls in that window, end the chunk right before it
    instead of at the exact token count, so the next chunk starts cleanly at the heading.
- **Overlap**: always `chunk_overlap_tokens` tokens of look-back for the start of the next chunk,
  regardless of whether the previous chunk ended at the hard cutoff or early at a section
  boundary — one consistent rule, no special-casing.
- **`is_table`**: a chunk is flagged `True` if its character span overlaps any `TableBlock`'s
  `[start_char, end_char)` range (any overlap, not full containment — a chunk straddling a table's
  edge still gets flagged, since its content is table-adjacent).
- **Token↔char bridging**: tokens are the primary unit for size/overlap decisions; character
  offsets (needed only to compare chunk spans against `TableBlock`s and to locate headings) are
  derived by decoding token-index prefixes at chunk boundaries — a handful of decode calls per
  chunk, not per token. This is a soft heuristic ("chunks respect section boundaries **where
  detectable**" per the ticket's own wording), not a byte-exact guarantee.

## Tests

- `tests/fixtures/sample_filings/sample_filing_normal.txt`: synthetic 10-K-style text with several
  `Item X.` headings, long enough to force multiple chunks at the default 512-token size.
- `tests/fixtures/sample_filings/sample_filing_table_heavy.txt`: includes an obviously tabular
  text region (e.g., a pipe-or-whitespace-aligned data table). The test constructs `TableBlock`s
  at known character offsets within this fixture (nothing in this ticket auto-detects them).
- `tests/unit/rag/test_chunking.py`:
  - Chunks from the normal fixture don't split mid-sentence at the hard cutoff when a heading
    falls within the look-back window; `section_reference` matches the expected heading per
    chunk; consecutive chunks' text overlaps by the configured `chunk_overlap_tokens`.
  - Chunks overlapping the table-heavy fixture's `TableBlock` ranges have `is_table=True`; chunks
    entirely outside those ranges have `is_table=False`.
  - Config values (`chunk_size_tokens`, `chunk_overlap_tokens`) are respected when overridden
    (e.g., a small `chunk_size_tokens` in a test produces proportionally more/smaller chunks).
