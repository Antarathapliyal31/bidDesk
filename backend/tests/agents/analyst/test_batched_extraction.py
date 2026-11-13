"""DECISIONS F21: page-batched extraction fallback.

Covers the City-of-Winnipeg 818-2009 scenario: one oversized
extraction call hits `max_tokens` and the tool_use block comes
back empty; the handler must fall back to page-batched
extraction and the merge must dedup items that land in the
1-page overlap.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from app.agents.analyst.batched_extraction import (
    chunk_pages,
    extract_items_in_batches,
)
from app.agents.analyst.extraction import (
    ExtractionInput,
    ExtractionTruncated,
    extract_items,
)
from app.agents.analyst.routing import PageExtract
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse


def _page(n: int, text: str = "line") -> PageExtract:
    return PageExtract(
        page_no=n, route="text", text=text, text_source="pdfplumber", table_issue=None
    )


def _tool_use(name: str, input_: dict, id_: str = "tu") -> Any:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


@pytest_asyncio.fixture
async def seeded_run(db) -> str:
    from app.contracts import InputKind
    from app.db import repo

    await repo.insert_run(
        db,
        run_id="run_batch",
        branch_id="BR-NJ-01",
        input_kind=InputKind.pdf,
        input_name="winnipeg.pdf",
        created_at="2026-11-13T00:00:00+00:00",
    )
    return "run_batch"


class TestChunkPages:
    def test_split_with_overlap(self) -> None:
        pages = [_page(i) for i in range(1, 8)]  # 7 pages
        batches = chunk_pages(pages, size=5, overlap=1)
        page_ranges = [(b[0].page_no, b[-1].page_no) for b in batches]
        assert page_ranges == [(1, 5), (5, 7)]

    def test_exact_multiple(self) -> None:
        pages = [_page(i) for i in range(1, 11)]  # 10 pages
        batches = chunk_pages(pages, size=5, overlap=1)
        page_ranges = [(b[0].page_no, b[-1].page_no) for b in batches]
        # stride = 4 → starts at 1, 5, 9; batch at 9 covers 9-10.
        assert page_ranges == [(1, 5), (5, 9), (9, 10)]

    def test_no_overlap(self) -> None:
        pages = [_page(i) for i in range(1, 7)]
        batches = chunk_pages(pages, size=3, overlap=0)
        assert [(b[0].page_no, b[-1].page_no) for b in batches] == [(1, 3), (4, 6)]


class TestMaxTokensDetection:
    async def test_stop_reason_max_tokens_raises_truncated(
        self, bus: EventBus, seeded_run: str
    ) -> None:
        # ScriptedResponse with an empty tool_input + stop_reason.
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[_tool_use("report_extraction", {})],
                    stop_reason="max_tokens",
                )
            ]
        )
        with pytest.raises(ExtractionTruncated):
            await extract_items(
                fake,
                input_=ExtractionInput(
                    run_id=seeded_run,
                    pages=[_page(1, "long dense text")],
                    document_tag="document_test",
                ),
                bus=bus,
            )

    async def test_empty_tool_input_raises_truncated_even_without_stop_reason(
        self, bus: EventBus, seeded_run: str
    ) -> None:
        # Common SDK symptom: tool_use block arrives but its input
        # is `{}` — treat as truncation regardless of whether the
        # SDK bothered to set stop_reason.
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[_tool_use("report_extraction", {})],
                    stop_reason=None,
                )
            ]
        )
        with pytest.raises(ExtractionTruncated):
            await extract_items(
                fake,
                input_=ExtractionInput(
                    run_id=seeded_run,
                    pages=[_page(1, "text")],
                    document_tag="document_test",
                ),
                bus=bus,
            )


class TestBatchedExtractionDedupe:
    async def test_items_on_overlap_page_deduped(
        self, bus: EventBus, seeded_run: str
    ) -> None:
        """Two batches, the overlap page (page 5) contains the same
        item in both batches. Dedup by (source_page, normalized
        quote) keeps exactly one copy."""

        # 7 pages → batches at (1-5) and (5-7).
        pages = [_page(i, f"body {i}") for i in range(1, 8)]

        batch_a_items = [
            {
                "kind": "material",
                "description": f"item on page {i}",
                "source_page": i,
                "source_quote": f"line on page {i}",
            }
            for i in range(1, 6)
        ]
        batch_b_items = [
            # Overlap: same source_page=5 + same quote (with
            # whitespace drift to prove normalization collapses it).
            {
                "kind": "material",
                "description": "item on page 5",
                "source_page": 5,
                "source_quote": "  line on   page 5  ",
            },
            {
                "kind": "material",
                "description": "item on page 6",
                "source_page": 6,
                "source_quote": "line on page 6",
            },
            {
                "kind": "material",
                "description": "item on page 7",
                "source_page": 7,
                "source_quote": "line on page 7",
            },
        ]
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "report_extraction",
                            {"items": batch_a_items, "facts": {"project_name": "City of X"}},
                            id_="tu_a",
                        )
                    ]
                ),
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "report_extraction",
                            {"items": batch_b_items, "facts": {}},
                            id_="tu_b",
                        )
                    ]
                ),
            ]
        )

        result = await extract_items_in_batches(
            fake,
            input_=ExtractionInput(
                run_id=seeded_run, pages=pages, document_tag="document_test"
            ),
            bus=bus,
            batch_pages=5,
            overlap_pages=1,
            concurrency=3,
        )

        # 5 items from batch A + 2 new from batch B (page 6, page 7);
        # the page-5 overlap item collapses.
        assert len(result.draft.items) == 7
        pages_seen = sorted(i.source_page for i in result.draft.items)
        assert pages_seen == [1, 2, 3, 4, 5, 6, 7]
        # First-non-null wins on project_name.
        assert result.draft.facts.project_name == "City of X"
        assert result.batches_total == 2
        assert result.batches_failed_page_ranges == []

    async def test_failing_batch_marks_pages_unreadable(
        self, bus: EventBus, seeded_run: str
    ) -> None:
        pages = [_page(i, f"body {i}") for i in range(1, 8)]
        # First batch OK; second batch returns another max_tokens
        # response.
        batch_a_items = [
            {
                "kind": "material",
                "description": f"item {i}",
                "source_page": i,
                "source_quote": f"quote {i}",
            }
            for i in range(1, 6)
        ]
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[_tool_use("report_extraction", {"items": batch_a_items})]
                ),
                ScriptedResponse(
                    content=[_tool_use("report_extraction", {})], stop_reason="max_tokens"
                ),
            ]
        )

        result = await extract_items_in_batches(
            fake,
            input_=ExtractionInput(
                run_id=seeded_run, pages=pages, document_tag="document_test"
            ),
            bus=bus,
            batch_pages=5,
            overlap_pages=1,
            concurrency=1,
        )
        assert result.batches_failed_page_ranges == [(5, 7)]
        assert set(result.draft.unreadable_pages) == {5, 6, 7}
        # Items from the ok batch still land.
        assert len(result.draft.items) == 5


# ---------------------------------------------------------------------------
# DECISIONS F22: long-doc short-circuit + vision pages batched
# ---------------------------------------------------------------------------


class TestLongDocShortCircuit:
    async def test_over_single_call_max_pages_skips_single_call(
        self, bus: EventBus, seeded_run: str
    ) -> None:
        """A 31-page doc (single_call_max_pages default 30) must NOT
        issue a single oversized call; the handler goes straight to
        the batched pipeline. Prove it by scripting the FakeLLM
        with ONLY batched responses (no single-call response) — if
        the handler tried a single call first, FakeLLM would exhaust
        and the test would fail with AssertionError."""

        from app.agents.analyst.agent import _AnalystHandler, AnalystRequest
        from app.runtime.budgets import Budget
        from app.runtime.delegate import AgentContext

        pages = [
            PageExtract(
                page_no=i,
                route="text",
                text=f"body for page {i}",
                text_source="pdfplumber",
                table_issue=None,
            )
            for i in range(1, 32)
        ]
        # Enough scripted batch responses: 31 pages / stride 4 = 8
        # batches. Each returns one item.
        responses = []
        # Batches start at p=1, 5, 9, 13, 17, 21, 25, 29 (size 5,
        # overlap 1, stride 4). Last one covers 29-31.
        batch_starts = list(range(1, 32, 4))  # 1,5,9,13,17,21,25,29
        for start in batch_starts:
            end = min(start + 4, 31)
            responses.append(
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "report_extraction",
                            {
                                "items": [
                                    {
                                        "kind": "material",
                                        "description": f"item page {start}",
                                        "source_page": start,
                                        "source_quote": f"quote page {start}",
                                    }
                                ]
                            },
                        )
                    ]
                )
            )

        fake = FakeLLM(responses=responses)
        # Monkey-patch the routing so the handler sees our in-memory
        # pages (otherwise it tries to open winnipeg.pdf).
        handler = _AnalystHandler(llm=fake)
        handler._route = lambda req: pages  # type: ignore[attr-defined]

        budget = Budget(turns=20, tokens=400_000, deadline_s=300.0)
        budget.start()
        ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)

        # kind="text" with pasted_text=" " avoids the pdf route
        # path; our monkey-patched _route returns `pages` either
        # way.
        req = AnalystRequest(kind="text", pasted_text=" ", document_tag="document_test")
        result = await handler(req, ctx, feedback=None)
        # Every scripted batch response consumed → the single-call
        # path was skipped.
        assert len(result["items"]) == len(batch_starts)


class TestVisionPagesBatched:
    async def test_batched_extraction_keeps_read_via_vision(
        self, bus: EventBus, seeded_run: str
    ) -> None:
        """A page routed 'vision' (transcribed by vision.py into
        `p.text`) must still end up with `read_via='vision'` on its
        extracted item, even when the batched pipeline is the one
        that parses it. Proof: `_AnalystHandler.verify_items` is
        called with the FULL original pages list, which keeps the
        route metadata intact across the batched merge."""

        from app.agents.analyst.agent import _AnalystHandler, AnalystRequest
        from app.runtime.budgets import Budget
        from app.runtime.delegate import AgentContext

        # Mixed 6-page doc: pages 1-4 are text, 5-6 are
        # vision-transcribed. With default size=5, overlap=1:
        # batch A covers 1-5, batch B covers 5-6.
        pages = [
            PageExtract(
                page_no=i,
                route="text",
                text=f"body {i}",
                text_source="pdfplumber",
                table_issue=None,
            )
            for i in range(1, 5)
        ] + [
            PageExtract(
                page_no=5,
                route="vision",
                text="vision transcript for page 5 with the sill-plate line",
                text_source="transcribed_image",
                table_issue=None,
            ),
            PageExtract(
                page_no=6,
                route="vision",
                text="vision transcript for page 6 with roof shingles",
                text_source="transcribed_image",
                table_issue=None,
            ),
        ]

        # Single-call path would work here too, so force the
        # batched route by lowering the handler's threshold via
        # settings override at runtime.
        from app.config import get_settings

        settings = get_settings()
        orig = settings.single_call_max_pages
        settings.single_call_max_pages = 4  # anything < 6
        try:
            batch_a_items = [
                {
                    "kind": "material",
                    "description": "framing line",
                    "source_page": 2,
                    "source_quote": "body 2",
                },
                {
                    "kind": "material",
                    "description": "sill plate from vision page",
                    "source_page": 5,
                    "source_quote": "sill-plate line",
                },
            ]
            batch_b_items = [
                # Overlap duplicate — must collapse.
                {
                    "kind": "material",
                    "description": "sill plate from vision page",
                    "source_page": 5,
                    "source_quote": "sill-plate line",
                },
                {
                    "kind": "material",
                    "description": "roof shingles from vision page",
                    "source_page": 6,
                    "source_quote": "roof shingles",
                },
            ]
            responses = [
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "report_extraction",
                            {"items": batch_a_items},
                            id_="tu_a",
                        )
                    ]
                ),
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "report_extraction",
                            {"items": batch_b_items},
                            id_="tu_b",
                        )
                    ]
                ),
            ]
            fake = FakeLLM(responses=responses)
            handler = _AnalystHandler(llm=fake)
            handler._route = lambda req: pages  # type: ignore[attr-defined]

            budget = Budget(turns=20, tokens=400_000, deadline_s=300.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)
            req = AnalystRequest(kind="text", pasted_text=" ", document_tag="document_test")
            result = await handler(req, ctx, feedback=None)
        finally:
            settings.single_call_max_pages = orig

        # One sill plate (dedup across overlap) + one roof shingle +
        # one text framing line = 3 items.
        items = result["items"]
        by_page = {it["source"]["page"]: it for it in items}
        assert set(by_page.keys()) == {2, 5, 6}
        # Text page keeps read_via='text'.
        assert by_page[2]["source"]["read_via"] == "text"
        # Vision pages keep read_via='vision' through the batched
        # pipeline.
        assert by_page[5]["source"]["read_via"] == "vision"
        assert by_page[6]["source"]["read_via"] == "vision"
        # And they're NOT verified (vision rule — DECISIONS §9 /
        # SPEC §6.4).
        assert by_page[5]["source"]["verified"] is False
        assert by_page[6]["source"]["verified"] is False
