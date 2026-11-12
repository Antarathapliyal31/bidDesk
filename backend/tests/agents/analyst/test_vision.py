"""Vision transcription + verify invariants (SPEC §6.2, DECISIONS #26).

Tests here cover:
  * `transcribe_vision_pages` fans out one call per vision /
    text+vision page, populates `.text` with the transcription,
    reports empty transcriptions as unreadable, and never
    transcribes more than `max_pages`.
  * `verify_items` stamps items from vision / text+vision pages
    with `read_via='vision'`, `verified=False`, and the
    `read_via_vision` note — even when the quote substring-
    matches the transcription.
  * The `ItemView` invariant rejects `read_via_vision` on
    non-vision sources.
  * ConflictView construction refuses a vision side (separate
    contract-level test to make the two-track rule explicit).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from pydantic import ValidationError

from app.agents.analyst.extraction import ItemDraft
from app.agents.analyst.routing import PageExtract
from app.agents.analyst.verify import verify_items
from app.agents.analyst.vision import transcribe_vision_pages
from app.contracts import ItemView, SourceRef
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse
from tests._seeds import seed_run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _page(
    page_no: int,
    text: str,
    route: str = "text",
) -> PageExtract:
    return PageExtract(
        page_no=page_no,
        text=text,
        route=route,  # type: ignore[arg-type]
        text_source="pdf_text_layer" if route != "vision" else "vision_transcription",
        table_issue=(route == "text+vision"),
    )


@dataclass
class _FakeRender:
    """Deterministic renderer: returns one byte per page_no so each
    call is distinguishable. Empty bytes for pages that should be
    treated as un-renderable."""

    empty_pages: set[int]

    def __call__(self, pdf_path: Path, page_no: int, *, dpi: int) -> bytes:
        _ = (pdf_path, dpi)
        if page_no in self.empty_pages:
            return b""
        return bytes([page_no % 256]) * 8  # arbitrary non-empty


class _TextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _SdkResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content

        class _Usage:
            input_tokens = 10
            output_tokens = 20

        self.usage = _Usage()


@pytest_asyncio.fixture
async def ctx(db: Database, bus: EventBus) -> AsyncIterator[AgentContext]:
    await seed_run(db)
    budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
    budget.start()
    yield AgentContext(run_id="run_abc", bus=bus, budget=budget)


# ---------------------------------------------------------------------------
# transcribe_vision_pages
# ---------------------------------------------------------------------------


class TestTranscribeVisionPages:
    async def test_transcribes_vision_and_text_plus_vision_but_not_text(
        self, ctx: AgentContext
    ) -> None:
        pages = [
            _page(1, "text layer page 1", route="text"),
            _page(2, "", route="vision"),
            _page(3, "pdfplumber text for messy page", route="text+vision"),
        ]
        responses = [
            ScriptedResponse(content=[_TextBlock("transcribed page 2 body")]),
            ScriptedResponse(content=[_TextBlock("transcribed messy page 3")]),
        ]
        llm = FakeLLM(responses=responses)
        rendered = _FakeRender(empty_pages=set())

        updated, unreadable = await transcribe_vision_pages(
            llm,
            bus=ctx.bus,
            run_id=ctx.run_id,
            pdf_path=Path("/tmp/dummy.pdf"),
            pages=pages,
            document_tag="d_test",
            max_pages=10,
            render=rendered,
        )

        assert unreadable == []
        # Order preserved; text page 1 untouched.
        assert updated[0].text == "text layer page 1"
        assert updated[0].route == "text"
        # Vision page 2 picked up its transcription.
        assert updated[1].text == "transcribed page 2 body"
        assert updated[1].route == "vision"
        # text+vision page 3 ALSO got transcribed.
        assert updated[2].text == "transcribed messy page 3"
        assert updated[2].route == "text+vision"
        # Exactly two LLM calls were made (one per vision-ish page).
        assert llm.calls_made == 2

    async def test_empty_transcription_marks_page_unreadable(self, ctx: AgentContext) -> None:
        pages = [_page(1, "", route="vision")]
        llm = FakeLLM(responses=[ScriptedResponse(content=[_TextBlock("   ")])])
        updated, unreadable = await transcribe_vision_pages(
            llm,
            bus=ctx.bus,
            run_id=ctx.run_id,
            pdf_path=Path("/tmp/dummy.pdf"),
            pages=pages,
            document_tag="d_test",
            max_pages=10,
            render=_FakeRender(empty_pages=set()),
        )
        assert unreadable == [1]
        # .text stays empty — it wasn't trustworthy.
        assert updated[0].text == ""

    async def test_cap_pages_beyond_max_pages_go_to_unreadable(self, ctx: AgentContext) -> None:
        pages = [_page(i + 1, "", route="vision") for i in range(5)]
        # Only the first 2 should be transcribed — the rest skipped
        # without LLM calls.
        responses = [ScriptedResponse(content=[_TextBlock(f"p{i + 1}")]) for i in range(2)]
        llm = FakeLLM(responses=responses)
        updated, unreadable = await transcribe_vision_pages(
            llm,
            bus=ctx.bus,
            run_id=ctx.run_id,
            pdf_path=Path("/tmp/dummy.pdf"),
            pages=pages,
            document_tag="d_test",
            max_pages=2,
            render=_FakeRender(empty_pages=set()),
        )
        assert llm.calls_made == 2
        # Pages 3, 4, 5 overflow cap → unreadable.
        assert unreadable == [3, 4, 5]
        assert updated[0].text == "p1"
        assert updated[1].text == "p2"
        # The skipped pages retain their empty text.
        assert updated[2].text == ""


# ---------------------------------------------------------------------------
# verify_items: vision invariants
# ---------------------------------------------------------------------------


class TestRealPdfiumRendering:
    """Guard against the PDFium thread-safety crash (double-free)
    that happened when `asyncio.gather` fanned out concurrent
    `to_thread(render)` calls. Uses the REAL `_render_page_to_png`
    against `samples/rfp_scanned.pdf` (3 vision pages) with
    FakeLLM responses + the normal Semaphore(3) concurrency path.
    Must complete without crashing and produce 3 distinct PNGs.
    """

    async def test_real_render_three_pages_no_crash(self, ctx: AgentContext) -> None:
        samples_root = Path(__file__).resolve().parents[3].parent / "samples"
        pdf_path = samples_root / "rfp_scanned.pdf"
        assert pdf_path.exists(), "sample fixture missing"

        # All three pages of rfp_scanned are vision-route.
        from app.agents.analyst.routing import route_pdf

        pages = route_pdf(pdf_path)
        assert all(p.route == "vision" for p in pages)
        assert len(pages) == 3

        # Three FakeLLM responses so each page's transcription call
        # has a scripted reply (the real LLM never runs in this
        # test — only the real renderer does).
        responses = [
            ScriptedResponse(content=[_TextBlock(f"transcription page {i + 1}")]) for i in range(3)
        ]
        llm = FakeLLM(responses=responses)

        updated, unreadable = await transcribe_vision_pages(
            llm,
            bus=ctx.bus,
            run_id=ctx.run_id,
            pdf_path=pdf_path,
            pages=pages,
            document_tag="d_real_render",
            max_pages=10,
            # Default render — hits real pypdfium2. If PDFium
            # concurrency safety regresses, this test crashes the
            # whole suite (SIGABRT).
        )

        assert unreadable == []
        assert llm.calls_made == 3
        # Each page got its scripted transcription.
        assert all(p.text.startswith("transcription page ") for p in updated)


class TestVerifyVisionInvariants:
    def test_vision_page_item_is_unverified_even_when_quote_matches(self) -> None:
        page = _page(1, "exactly this quote text", route="vision")
        drafts = [
            ItemDraft(
                kind="material",
                description="Nails",
                quantity_text="10",
                unit_raw="BOX",
                source_page=1,
                source_quote="exactly this quote text",
            )
        ]
        items = verify_items(drafts, pages=[page])
        it = items[0]
        assert it.source.read_via == "vision"
        assert it.source.verified is False
        assert "read_via_vision" in it.notes
        # No `quote_not_found` on the vision path — we never tried
        # a byte-match.
        assert "quote_not_found" not in it.notes

    def test_text_plus_vision_item_is_unverified(self) -> None:
        # Even if the pdfplumber text contains the exact quote, a
        # text+vision page's items go through the vision path
        # (DECISIONS #26, mirrors frontend fixture assertion on
        # rfp_messy items 21-23).
        page = _page(1, "some pdfplumber text with Nails 10 boxes inside", route="text+vision")
        drafts = [
            ItemDraft(
                kind="material",
                description="Nails",
                quantity_text="10",
                unit_raw="BOX",
                source_page=1,
                source_quote="Nails 10 boxes",
            )
        ]
        items = verify_items(drafts, pages=[page])
        it = items[0]
        assert it.source.read_via == "vision"
        assert it.source.verified is False
        assert "read_via_vision" in it.notes


# ---------------------------------------------------------------------------
# ItemView contract — read_via_vision note requires vision SourceRef
# ---------------------------------------------------------------------------


class TestItemViewReadViaVisionInvariant:
    def test_valid_baseline_builds(self) -> None:
        iv = ItemView(
            id="item_1",
            kind="material",
            description="Nails",
            quantity_text="10",
            unit_raw="BOX",
            source=SourceRef(page=1, quote="q", read_via="vision", verified=False),
            notes=["read_via_vision"],
        )
        assert iv.source.read_via == "vision"

    def test_rejects_read_via_vision_on_text_source(self) -> None:
        with pytest.raises(ValidationError) as exc:
            ItemView(
                id="item_1",
                kind="material",
                description="Nails",
                quantity_text="10",
                unit_raw="BOX",
                source=SourceRef(page=1, quote="q", read_via="text", verified=False),
                notes=["read_via_vision"],
            )
        assert "read_via_vision" in str(exc.value)


# ---------------------------------------------------------------------------
# ConflictView refuses vision sides (DECISIONS #16 is unchanged;
# included here so Phase 5c has an explicit guard-rail test).
# ---------------------------------------------------------------------------


class TestConflictViewRefusesVisionSide:
    def test_vision_side_rejected(self) -> None:
        from app.contracts.bid import ConflictView

        text_side = {
            "item_id": "item_1",
            "source": SourceRef(page=1, quote="t", read_via="text", verified=True).model_dump(
                mode="python"
            ),
            "statement": "A",
        }
        vision_side = {
            "item_id": "item_2",
            "source": SourceRef(page=2, quote="v", read_via="vision", verified=False).model_dump(
                mode="python"
            ),
            "statement": "B",
        }
        with pytest.raises(ValidationError):
            ConflictView(
                id="conflict_1",
                type="spec_vs_spec",
                side_a=text_side,
                side_b=vision_side,
                explanation="demo",
            )
