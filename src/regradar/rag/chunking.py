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
