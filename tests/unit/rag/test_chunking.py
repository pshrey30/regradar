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
