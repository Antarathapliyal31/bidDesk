"""Per-page routing (SPEC §6.2).

For each PDF page, decide one of four routes:
  - `text`         — pdfplumber extracted >= MIN_TEXT_CHARS, no
                     messy tables
  - `vision`       — pdfplumber extracted < MIN_TEXT_CHARS
                     (scanned page; needs rasterization)
  - `text+vision`  — enough text, but the tables look messy
                     (unequal column counts or lots of empty cells)
  - `unreadable`   — vision transcription later came back empty;
                     set downstream (not here)

Pure, synchronous, deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pdfplumber

# A page with less text than this falls out of the text route.
MIN_TEXT_CHARS = 40

# A table is "messy" if any of these are true (SPEC §6.2.2).
_MAX_EMPTY_CELL_FRACTION = 0.30


PageRouteT = Literal["text", "vision", "text+vision"]


@dataclass(frozen=True)
class PageExtract:
    """Pdfplumber output for one page + the routing decision."""

    page_no: int
    text: str
    route: PageRouteT
    text_source: Literal["pdf_text_layer", "vision_transcription"]
    table_issue: bool


def _table_is_messy(table: list[list[str | None]]) -> bool:
    """A table is "messy" (worth a vision read alongside the text,
    SPEC §6.2.2) when any of these fire:
      - uneven column counts across rows
      - > 30% empty cells
      - ANY `None` cell — pdfplumber returns None when it couldn't
        parse a cell confidently; empty-string `""` is a real empty
        cell, but None signals the table layout is off (e.g.
        line-wrap into a neighbour column).
    """

    if not table:
        return False
    col_counts = {len(row) for row in table}
    if len(col_counts) > 1:
        return True
    total = sum(len(row) for row in table)
    if total == 0:
        return True
    if any(cell is None for row in table for cell in row):
        return True
    empty = sum(1 for row in table for cell in row if not cell or not str(cell).strip())
    return (empty / total) > _MAX_EMPTY_CELL_FRACTION


def route_page(page: pdfplumber.page.Page, page_no: int) -> PageExtract:
    """Make the routing decision for one open pdfplumber page."""

    text = page.extract_text() or ""
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return PageExtract(
            page_no=page_no,
            text="",
            route="vision",
            text_source="vision_transcription",
            table_issue=False,
        )

    # Only scan the first few tables; a document with 100 tables on
    # one page is pathological and gets text+vision anyway.
    tables = page.extract_tables() or []
    messy = any(_table_is_messy(t) for t in tables[:5])
    return PageExtract(
        page_no=page_no,
        text=text,  # keep the raw extraction; verification happens downstream
        route="text+vision" if messy else "text",
        text_source="pdf_text_layer",
        table_issue=messy,
    )


def route_pdf(pdf_path: Path) -> list[PageExtract]:
    """Route every page in `pdf_path`. Returns PageExtract per
    page in document order."""

    with pdfplumber.open(pdf_path) as pdf:
        return [route_page(page, i + 1) for i, page in enumerate(pdf.pages)]


def route_pasted_text(text: str) -> PageExtract:
    """Pasted text is treated as a 1-page `text` document
    (SPEC §6.1)."""

    return PageExtract(
        page_no=1,
        text=text,
        route="text",
        text_source="pdf_text_layer",
        table_issue=False,
    )
