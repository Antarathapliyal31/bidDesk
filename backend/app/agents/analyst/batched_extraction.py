"""Page-batched extraction fallback (DECISIONS #20 / F21).

The single-call extraction (`extract_items`) hits `max_tokens` on
long / dense RFPs — the model's `report_extraction` tool_use
block gets truncated to empty input. This module splits the
document into N-page chunks with a 1-page overlap, runs each
chunk through `extract_items` in parallel under a semaphore, and
merges the results by `(source_page, normalized quote)` so items
that land in the overlap aren't double-counted.

Boundaries:
  - Each batch is itself an `extract_items` call. Its own
    `ExtractionTruncated` means that chunk is still too dense;
    we drop it from the merge and report the pages in
    `unreadable_pages` so the estimator sees them flagged.
  - `general_notes` dedupe like items, by `(page, normalized quote)`.
  - `facts.project_name` / `facts.due_date_text` come from the
    FIRST batch that produced a non-empty value — typically the
    early pages where the project heading sits.
  - `unreadable_pages` is the union across batches plus the
    pages from failed-to-extract batches.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from app.agents.analyst.extraction import (
    DocumentFactsDraft,
    ExtractionDraft,
    ExtractionInput,
    ExtractionShapeError,
    ExtractionTruncated,
    GeneralNoteDraft,
    ItemDraft,
    extract_items,
    _LLMCallable,
)
from app.agents.analyst.routing import PageExtract
from app.contracts import AgentName
from app.runtime.events import EventBus


def _normalize_quote(s: str) -> str:
    """Whitespace-collapse + lowercase for the dedup key.

    Mirrors the matcher's quote-normalisation elsewhere in the
    codebase — same shape so duplicates across the overlap page
    (one copy from each batch) collapse deterministically.
    """

    return re.sub(r"\s+", " ", s or "").strip().lower()


def chunk_pages(
    pages: list[PageExtract], *, size: int, overlap: int
) -> list[list[PageExtract]]:
    """Split `pages` into batches of `size` with `overlap`
    carry-over (SPEC §6.3 / DECISIONS #20).

    Invariants:
      - `size >= 1`, `0 <= overlap < size`.
      - Every original page appears in at least one batch.
      - Consecutive batches share exactly `overlap` pages.
      - A batch may be shorter than `size` at the tail.
    """

    if size < 1:
        raise ValueError(f"size must be >= 1, got {size}")
    if overlap < 0 or overlap >= size:
        raise ValueError(f"overlap must be in [0, size); got {overlap} for size={size}")
    if not pages:
        return []
    stride = size - overlap
    out: list[list[PageExtract]] = []
    i = 0
    while i < len(pages):
        batch = pages[i : i + size]
        out.append(batch)
        if i + size >= len(pages):
            break
        i += stride
    return out


@dataclass(frozen=True)
class BatchedExtractionResult:
    draft: ExtractionDraft
    batches_total: int
    batches_failed_page_ranges: list[tuple[int, int]]


async def extract_items_in_batches(
    llm: _LLMCallable,
    *,
    input_: ExtractionInput,
    bus: EventBus,
    batch_pages: int,
    overlap_pages: int,
    concurrency: int,
    max_tokens: int | None = None,
) -> BatchedExtractionResult:
    """Run `extract_items` over `input_.pages` in chunks.

    A batch that raises `ExtractionTruncated` or
    `ExtractionShapeError` is logged via `agent.activity` and its
    page range is added to `batches_failed_page_ranges` (so the
    caller can mark those pages as `unreadable_pages`). Successful
    batches are merged with `(source_page, normalized_quote)`
    dedup; the overlap page's duplicate items collapse.
    """

    batches = chunk_pages(input_.pages, size=batch_pages, overlap=overlap_pages)
    if not batches:
        return BatchedExtractionResult(
            draft=ExtractionDraft(items=[], facts=DocumentFactsDraft(), unreadable_pages=[]),
            batches_total=0,
            batches_failed_page_ranges=[],
        )

    sem = asyncio.Semaphore(concurrency)

    async def _one(batch: list[PageExtract]) -> tuple[ExtractionDraft | None, tuple[int, int] | None]:
        page_range = (batch[0].page_no, batch[-1].page_no)
        try:
            async with sem:
                draft = await extract_items(
                    llm,
                    input_=ExtractionInput(
                        run_id=input_.run_id,
                        pages=batch,
                        document_tag=input_.document_tag,
                    ),
                    bus=bus,
                    max_tokens=max_tokens,
                )
        except ExtractionTruncated as exc:
            await bus.emit(
                type="agent.activity",
                run_id=input_.run_id,
                agent=AgentName.document_analyst,
                payload={
                    "text": (
                        f"extraction batch pages {page_range[0]}–{page_range[1]} "
                        f"truncated ({exc.reason}); pages marked unreadable"
                    )[:200],
                },
            )
            return None, page_range
        except ExtractionShapeError as exc:
            await bus.emit(
                type="agent.activity",
                run_id=input_.run_id,
                agent=AgentName.document_analyst,
                payload={
                    "text": (
                        f"extraction batch pages {page_range[0]}–{page_range[1]} "
                        f"shape error; pages marked unreadable: {exc}"
                    )[:200],
                },
            )
            return None, page_range
        return draft, None

    results = await asyncio.gather(*(_one(b) for b in batches))

    # Merge.
    item_keys: set[tuple[int, str]] = set()
    merged_items: list[ItemDraft] = []
    note_keys: set[tuple[int, str]] = set()
    merged_notes: list[GeneralNoteDraft] = []
    project_name: str | None = None
    due_date_text: str | None = None
    unreadable: set[int] = set()
    failed_ranges: list[tuple[int, int]] = []

    for draft, failed in results:
        if failed is not None:
            failed_ranges.append(failed)
            for page_no in range(failed[0], failed[1] + 1):
                unreadable.add(page_no)
            continue
        assert draft is not None
        for item in draft.items:
            key = (item.source_page, _normalize_quote(item.source_quote))
            if key in item_keys:
                continue
            item_keys.add(key)
            merged_items.append(item)
        for note in draft.facts.general_notes:
            key = (note.page, _normalize_quote(note.quote))
            if key in note_keys:
                continue
            note_keys.add(key)
            merged_notes.append(note)
        if project_name is None and draft.facts.project_name:
            project_name = draft.facts.project_name
        if due_date_text is None and draft.facts.due_date_text:
            due_date_text = draft.facts.due_date_text
        unreadable.update(draft.unreadable_pages)

    draft = ExtractionDraft(
        items=sorted(merged_items, key=lambda i: (i.source_page, i.description)),
        facts=DocumentFactsDraft(
            project_name=project_name,
            due_date_text=due_date_text,
            general_notes=merged_notes,
        ),
        unreadable_pages=sorted(unreadable),
    )
    return BatchedExtractionResult(
        draft=draft, batches_total=len(batches), batches_failed_page_ranges=failed_ranges
    )
