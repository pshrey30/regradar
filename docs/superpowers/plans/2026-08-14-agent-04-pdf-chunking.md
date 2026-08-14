# AGENT-04 — PDF Chunking Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `rag/chunking.py`'s `chunk_filing()` — splits already-extracted filing text into
~512-token chunks with 50-token overlap, preferring to cut at a detected section heading over the
hard token cutoff when one is nearby, and flagging chunks that overlap a caller-supplied table
region.

**Architecture:** A single new module, `src/regradar/rag/chunking.py`, with no dependency on any
other agent/pipeline code — it's a pure function operating on `text: str` and
`tables: list[TableBlock]`, returning `list[Chunk]`. Two new `Settings` fields
(`chunk_size_tokens`, `chunk_overlap_tokens`) control chunk size. The algorithm below was
prototyped and verified against real fixture text before being written into this plan — the token
counts, character offsets, and chunk boundaries stated in each task's tests are actual verified
output, not estimates.

**Tech Stack:** `tiktoken` (`cl100k_base` encoding — already a dependency, unused until now),
`pydantic` for `TableBlock`/`Chunk`, stdlib `re` for heading detection.

## Global Constraints

- Design source of truth: `docs/superpowers/specs/2026-08-14-agent-04-pdf-chunking-design.md`.
- `chunk_filing(text: str, tables: list[TableBlock]) -> list[Chunk]` — exact signature per the
  ticket. No PDF parsing, no table detection inside this function — both are pre-supplied inputs.
- Encoding: `tiktoken.get_encoding("cl100k_base")`.
- Heading regex (module-level constant, not configurable):
  `r'^(Item\s+\d+[A-Z]?\.?|Section\s+\d+(?:\.\d+)*:?|Article\s+[IVXLCDM]+\.?|\d+(?:\.\d+)*\s+[A-Z])'`
  with `re.MULTILINE`.
- Look-back window for the section-boundary preference: `int(chunk_size_tokens * 0.15)` tokens
  (≈75 at the 512 default), minimum 1.
- Overlap is always applied the same way (`chunk_overlap_tokens` tokens of look-back for the next
  chunk's start), regardless of whether the previous chunk ended at the hard cutoff or early at a
  section boundary.
- `is_table=True` if a chunk's character span `[start_char, end_char)` overlaps any `TableBlock`'s
  `[start_char, end_char)` (any overlap, not full containment):
  `table.start_char < chunk_end_char and table.end_char > chunk_start_char`.
- `section_reference` for a chunk is the most recent heading at or before the chunk's own starting
  character offset, or `None` if no heading precedes it.

---

### Task 1: `chunk_size_tokens` / `chunk_overlap_tokens` config

**Files:**
- Modify: `src/regradar/core/config.py`
- Modify: `.env.example`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `Settings.chunk_size_tokens: int` (default `512`), `Settings.chunk_overlap_tokens: int`
  (default `50`), for Task 3.

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py`, inside `test_all_required_fields_present_loads_with_defaults`
(after the existing `assert settings.classification_confidence_threshold == 0.75` line):

```python
    assert settings.chunk_size_tokens == 512
    assert settings.chunk_overlap_tokens == 50
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_config.py -v -k all_required_fields`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'chunk_size_tokens'`

- [ ] **Step 3: Write the implementation**

In `src/regradar/core/config.py`, add a new section after the
`# ── Model routing tier thresholds ──` block (before `# ── Local inference ──`):

```python
    # ── RAG chunking ─────────────────────────────────────
    chunk_size_tokens: int = Field(default=512, alias="CHUNK_SIZE_TOKENS")
    chunk_overlap_tokens: int = Field(default=50, alias="CHUNK_OVERLAP_TOKENS")
```

Add to `.env.example`, after the `CLASSIFICATION_CONFIDENCE_THRESHOLD` line:

```
CHUNK_SIZE_TOKENS=512
CHUNK_OVERLAP_TOKENS=50
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_config.py -v`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/core/config.py .env.example tests/unit/test_config.py
git commit -m "Add chunk_size_tokens/chunk_overlap_tokens config (AGENT-04)"
```

---

### Task 2: Fixture files

**Files:**
- Create: `tests/fixtures/sample_filings/sample_filing_normal.txt`
- Create: `tests/fixtures/sample_filings/sample_filing_table_heavy.txt`

**Interfaces:**
- Produces: two fixture files consumed by Task 4's tests. Their exact contents (and the
  chunk-boundary numbers derived from them) were verified by running the actual chunking algorithm
  against them before this plan was written.

- [ ] **Step 1: Generate `sample_filing_normal.txt`**

Run this script (adjust the output path if your working directory differs) to produce the exact
verified fixture:

```python
filler = (
    "The Company continues to monitor macroeconomic conditions that may affect its operations, "
    "including inflation, interest rate volatility, and supply chain disruptions across its primary "
    "markets. Management believes that current liquidity and capital resources are sufficient to "
    "meet operating requirements for at least the next twelve months. "
)

parts = []
parts.append("Item 1. Business\n")
parts.append(filler * 8)
parts.append("\nItem 1A. Risk Factors\n")
parts.append(filler * 10)
parts.append("\nItem 2. Properties\n")
parts.append(filler * 6)
parts.append("\nItem 7. Management's Discussion and Analysis\n")
parts.append(filler * 12)

text = "".join(parts)
with open("tests/fixtures/sample_filings/sample_filing_normal.txt", "w") as f:
    f.write(text)
```

Verified properties of the resulting file (12,418 characters):
- Headings at exact character offsets: `(0, "Item 1. Business")`, `(2754, "Item 1A. Risk
  Factors")`, `(6197, "Item 2. Properties")`, `(8269, "Item 7. Management's Discussion and
  Analysis")`.
- At the default `chunk_size_tokens=512`/`chunk_overlap_tokens=50`, `chunk_filing()` on this text
  (with `tables=[]`) produces exactly 5 chunks:
  - Chunk 0: tokens=512, `start_char=0`, `end_char=3310`, `section_reference="Item 1. Business"`.
  - Chunk 1: tokens=490, `start_char=2980`, `end_char=6197` (ends exactly at the "Item 2." heading
    — a clean cut, not mid-sentence), `section_reference="Item 1A. Risk Factors"`.
  - Chunk 2: tokens=512, `start_char=5875`, `end_char=9190`,
    `section_reference="Item 1A. Risk Factors"`.
  - Chunk 3: tokens=512, `start_char=8860`, `end_char=12223`,
    `section_reference="Item 7. Management's Discussion and Analysis"`.
  - Chunk 4: tokens=80, `start_char=11899`, `end_char=12418` (end of text),
    `section_reference="Item 7. Management's Discussion and Analysis"`.

- [ ] **Step 2: Generate `sample_filing_table_heavy.txt`**

Run this script:

```python
filler = (
    "The Company reported the following segment results for the fiscal year, reflecting "
    "changes in revenue mix across its primary operating regions and product lines. "
)

table_text = (
    "Region      Revenue($M)   Growth(%)   Headcount\n"
    "North America   482.3        6.1          1204\n"
    "Europe          311.7        3.4           876\n"
    "Asia Pacific    198.5        9.8           542\n"
    "Latin America    64.2        2.1           187\n"
)

parts = []
parts.append("Item 7. Management's Discussion and Analysis\n")
parts.append(filler * 6)
parts.append("\nTable 1: Segment Revenue Summary\n")
parts.append(table_text)
parts.append("\n")
parts.append(filler * 6)

text = "".join(parts)
with open("tests/fixtures/sample_filings/sample_filing_table_heavy.txt", "w") as f:
    f.write(text)
```

Verified properties of the resulting file (2,260 characters, 410 tokens total):
- The table region (the `table_text` block above) occupies character offsets
  `[1051, 1287)` — i.e. `TableBlock(start_char=1051, end_char=1287)` exactly covers
  `"Region      Revenue($M)...    2.1           187\n"`.
- At `chunk_size_tokens=200`/`chunk_overlap_tokens=20` (a smaller size than default, used
  specifically in this test to force multiple chunks from this shorter fixture), `chunk_filing()`
  with `tables=[TableBlock(start_char=1051, end_char=1287)]` produces exactly 3 chunks:
  - Chunk 0: tokens=200, `is_table=True` (overlaps the table region).
  - Chunk 1: tokens=200, `is_table=True` (overlaps the table region).
  - Chunk 2: tokens=50, `is_table=False` (entirely past the table region).
- At the default `chunk_size_tokens=512`, the whole 410-token file fits in a single chunk:
  1 chunk, tokens=410, `is_table=True`.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/sample_filings/sample_filing_normal.txt tests/fixtures/sample_filings/sample_filing_table_heavy.txt
git commit -m "Add PDF chunking test fixtures (AGENT-04)"
```

---

### Task 3: `TableBlock`, `Chunk`, and `chunk_filing()`

**Files:**
- Create: `src/regradar/rag/chunking.py`
- Create: `tests/unit/rag/test_chunking.py`
- Create: `tests/unit/rag/__init__.py` (empty — matches the existing `tests/unit/agents/__init__.py`
  pattern for making the directory a proper test package)

**Interfaces:**
- Consumes: `Settings.chunk_size_tokens`, `Settings.chunk_overlap_tokens` (Task 1); the two
  fixture files (Task 2).
- Produces:
  - `class TableBlock(BaseModel)`: fields `start_char: int`, `end_char: int`.
  - `class Chunk(BaseModel)`: fields `chunk_index: int`, `chunk_text: str`,
    `section_reference: str | None`, `token_count: int`, `is_table: bool`.
  - `def chunk_filing(text: str, tables: list[TableBlock]) -> list[Chunk]`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/rag/__init__.py` (empty file).

Create `tests/unit/rag/test_chunking.py`:

```python
"""Unit tests for the PDF chunking pipeline (chunk_filing).

Fixture files and their exact expected chunk boundaries were verified by
running the actual algorithm against them — see the fixture-generation
comments in docs/superpowers/plans/2026-08-14-agent-04-pdf-chunking.md
for how tests/fixtures/sample_filings/sample_filing_normal.txt and
sample_filing_table_heavy.txt were produced.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from regradar.rag.chunking import Chunk, TableBlock, chunk_filing

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures" / "sample_filings"


def _fake_settings(chunk_size_tokens: int, chunk_overlap_tokens: int) -> MagicMock:
    settings = MagicMock()
    settings.chunk_size_tokens = chunk_size_tokens
    settings.chunk_overlap_tokens = chunk_overlap_tokens
    return settings


def test_chunk_filing_normal_fixture_produces_expected_chunk_boundaries() -> None:
    text = (FIXTURES_DIR / "sample_filing_normal.txt").read_text()

    with patch(
        "regradar.rag.chunking.get_settings",
        return_value=_fake_settings(chunk_size_tokens=512, chunk_overlap_tokens=50),
    ):
        chunks = chunk_filing(text, tables=[])

    assert len(chunks) == 5
    assert [c.token_count for c in chunks] == [512, 490, 512, 512, 80]
    assert [c.chunk_index for c in chunks] == [0, 1, 2, 3, 4]
    assert chunks[0].section_reference == "Item 1. Business"
    assert chunks[1].section_reference == "Item 1A. Risk Factors"
    assert chunks[2].section_reference == "Item 1A. Risk Factors"
    assert chunks[3].section_reference == "Item 7. Management's Discussion and Analysis"
    assert chunks[4].section_reference == "Item 7. Management's Discussion and Analysis"


def test_chunk_filing_cuts_cleanly_before_heading_not_mid_sentence() -> None:
    text = (FIXTURES_DIR / "sample_filing_normal.txt").read_text()

    with patch(
        "regradar.rag.chunking.get_settings",
        return_value=_fake_settings(chunk_size_tokens=512, chunk_overlap_tokens=50),
    ):
        chunks = chunk_filing(text, tables=[])

    # Chunk 1 must end right before "Item 2." (a clean cut), not contain it,
    # and not end mid-word.
    assert "Item 2." not in chunks[1].chunk_text
    assert chunks[1].chunk_text.rstrip().endswith(".")
    # Chunk 2 (which starts before "Item 2." due to overlap) does contain it.
    assert "Item 2." in chunks[2].chunk_text


def test_chunk_filing_consecutive_chunks_overlap() -> None:
    from regradar.rag.chunking import ENCODING

    text = (FIXTURES_DIR / "sample_filing_normal.txt").read_text()

    with patch(
        "regradar.rag.chunking.get_settings",
        return_value=_fake_settings(chunk_size_tokens=512, chunk_overlap_tokens=50),
    ):
        chunks = chunk_filing(text, tables=[])

    # Chunk 1 starts 50 tokens back into chunk 0's span (tokens 462:512 of
    # the full encoding) — decode that exact token range and confirm it's
    # both chunk 0's tail and chunk 1's head, verbatim.
    overlap_text = ENCODING.decode(ENCODING.encode(text)[462:512])
    assert chunks[0].chunk_text.endswith(overlap_text)
    assert chunks[1].chunk_text.startswith(overlap_text)


def test_chunk_filing_table_heavy_fixture_flags_overlapping_chunks() -> None:
    text = (FIXTURES_DIR / "sample_filing_table_heavy.txt").read_text()
    table = TableBlock(start_char=1051, end_char=1287)

    with patch(
        "regradar.rag.chunking.get_settings",
        return_value=_fake_settings(chunk_size_tokens=200, chunk_overlap_tokens=20),
    ):
        chunks = chunk_filing(text, tables=[table])

    assert len(chunks) == 3
    assert [c.token_count for c in chunks] == [200, 200, 50]
    assert chunks[0].is_table is True
    assert chunks[1].is_table is True
    assert chunks[2].is_table is False


def test_chunk_filing_table_heavy_fixture_single_chunk_at_default_size() -> None:
    text = (FIXTURES_DIR / "sample_filing_table_heavy.txt").read_text()
    table = TableBlock(start_char=1051, end_char=1287)

    with patch(
        "regradar.rag.chunking.get_settings",
        return_value=_fake_settings(chunk_size_tokens=512, chunk_overlap_tokens=50),
    ):
        chunks = chunk_filing(text, tables=[table])

    assert len(chunks) == 1
    assert chunks[0].token_count == 410
    assert chunks[0].is_table is True


def test_chunk_filing_empty_tables_list_never_flags_is_table() -> None:
    text = (FIXTURES_DIR / "sample_filing_normal.txt").read_text()

    with patch(
        "regradar.rag.chunking.get_settings",
        return_value=_fake_settings(chunk_size_tokens=512, chunk_overlap_tokens=50),
    ):
        chunks = chunk_filing(text, tables=[])

    assert all(c.is_table is False for c in chunks)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/rag/test_chunking.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'regradar.rag.chunking'`

- [ ] **Step 3: Write the implementation**

Create `src/regradar/rag/chunking.py`:

```python
"""PDF chunking pipeline — splits already-extracted filing text into
token-sized chunks, preferring section-heading boundaries over a hard
token cutoff, and flagging chunks that overlap caller-supplied table
regions.

This module does NOT parse PDFs or detect tables itself — chunk_filing
takes already-extracted text and an already-detected list of table
regions as input. PDF-to-text extraction and table detection are future
work; this module is a pure text-chunking utility.
"""

import re

import tiktoken
from pydantic import BaseModel

from regradar.core.config import get_settings

ENCODING = tiktoken.get_encoding("cl100k_base")

HEADING_PATTERN = re.compile(
    r"^(Item\s+\d+[A-Z]?\.?|Section\s+\d+(?:\.\d+)*:?|Article\s+[IVXLCDM]+\.?|\d+(?:\.\d+)*\s+[A-Z])",
    re.MULTILINE,
)

LOOKBACK_FRACTION = 0.15


class TableBlock(BaseModel):
    """A caller-detected table region, as a half-open character span into
    the same `text` passed to chunk_filing."""

    start_char: int
    end_char: int


class Chunk(BaseModel):
    chunk_index: int
    chunk_text: str
    section_reference: str | None
    token_count: int
    is_table: bool


def _find_headings(text: str) -> list[tuple[int, str]]:
    """Every heading occurrence as (char_position, heading_line_text)."""
    headings = []
    for match in HEADING_PATTERN.finditer(text):
        line_end = text.find("\n", match.start())
        if line_end == -1:
            line_end = len(text)
        heading_text = text[match.start() : line_end].strip()
        headings.append((match.start(), heading_text))
    return headings


def _section_reference_at(headings: list[tuple[int, str]], char_pos: int) -> str | None:
    """The most recent heading at or before char_pos, or None."""
    ref: str | None = None
    for pos, heading_text in headings:
        if pos <= char_pos:
            ref = heading_text
        else:
            break
    return ref


def _overlaps_table(tables: list[TableBlock], start_char: int, end_char: int) -> bool:
    return any(t.start_char < end_char and t.end_char > start_char for t in tables)


def chunk_filing(text: str, tables: list[TableBlock]) -> list[Chunk]:
    """Split text into chunk_size_tokens-sized chunks with
    chunk_overlap_tokens of overlap between consecutive chunks.

    When a chunk would otherwise hit the hard token cutoff, looks back up
    to LOOKBACK_FRACTION of chunk_size_tokens for a detected section
    heading; if found, ends the chunk right before it instead. Chunks
    whose character span overlaps any TableBlock are flagged is_table.
    """
    settings = get_settings()
    chunk_size = settings.chunk_size_tokens
    overlap = settings.chunk_overlap_tokens
    lookback_tokens = max(1, int(chunk_size * LOOKBACK_FRACTION))

    tokens = ENCODING.encode(text)
    headings = _find_headings(text)

    chunks: list[Chunk] = []
    start_tok = 0
    chunk_index = 0

    while start_tok < len(tokens):
        hard_end_tok = min(start_tok + chunk_size, len(tokens))
        end_tok = hard_end_tok

        if hard_end_tok < len(tokens):
            window_start_tok = max(start_tok, hard_end_tok - lookback_tokens)
            window_start_char = len(ENCODING.decode(tokens[:window_start_tok]))
            window_end_char = len(ENCODING.decode(tokens[:hard_end_tok]))
            candidates = [
                pos for pos, _ in headings if window_start_char < pos <= window_end_char
            ]
            if candidates:
                cut_char = min(candidates)
                end_tok = len(ENCODING.encode(text[:cut_char]))
                end_tok = max(start_tok + 1, min(end_tok, hard_end_tok))

        chunk_tokens = tokens[start_tok:end_tok]
        chunk_text = ENCODING.decode(chunk_tokens)
        start_char = len(ENCODING.decode(tokens[:start_tok]))
        end_char = start_char + len(chunk_text)

        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                chunk_text=chunk_text,
                section_reference=_section_reference_at(headings, start_char),
                token_count=len(chunk_tokens),
                is_table=_overlaps_table(tables, start_char, end_char),
            )
        )
        chunk_index += 1

        if end_tok >= len(tokens):
            break
        start_tok = max(start_tok + 1, end_tok - overlap)

    return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/rag/test_chunking.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/regradar/rag/chunking.py tests/unit/rag/__init__.py tests/unit/rag/test_chunking.py
git commit -m "Add chunk_filing: token-based chunking with section-boundary and table awareness (AGENT-04)"
```

---

### Task 4: Full-suite check, lint, mypy, push

**Files:** none (verification only)

- [ ] **Step 1: Run the full default test suite**

Run: `.venv/bin/pytest -v --ignore=tests/integration/test_flows.py`
Expected: PASS (all tests, including the new `tests/unit/rag/` ones; `test_flows.py` needs a live
Postgres this ticket doesn't touch, consistent with prior tickets).

- [ ] **Step 2: Run lint and type checks**

Run: `.venv/bin/ruff check src/regradar/rag src/regradar/core/config.py tests/unit/rag tests/unit/test_config.py`
Run: `.venv/bin/mypy src/regradar/rag src/regradar/core/config.py`
Expected: no errors. Fix any and commit the fix as part of this task.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin agent-04-pdf-chunking
```

Do not merge to `master` — per project convention, merging is a separate explicit step the user
confirms.
