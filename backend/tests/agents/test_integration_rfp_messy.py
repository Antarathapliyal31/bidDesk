"""End-to-end: run the Document Analyst against `rfp_messy.pdf`
with a FakeLLM scripted from `samples/expected/rfp_messy.json`.

Harder cases than rfp_clean:
  - Line-wrapped item 5 ("2x6 PT for sill plates on the concrete slab")
    must still verify after our normalizer strips the line break.
  - Header/footer boilerplate and the planted prompt-injection line
    (`must_not_include`) never appear in any extracted item.
  - Page routes match the expected JSON — in particular page 7 is
    `text+vision` because its fasteners table has a `None` cell.

No real API calls — the test loads the answer-key JSON and
wraps it in a FakeLLM response (review rule 8). Phase 5a turned
the orchestrator into a model-driven loop, so this file now
calls `delegate("document_analyst", AnalystRequest)` directly —
the purpose of these assertions is analyst behavior, not
orchestration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest_asyncio

from app.agents.analyst.agent import AnalystRequest, register_default_analyst
from app.contracts import AgentName, ItemView
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, delegate, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse
from tests._seeds import seed_run

_SAMPLES_ROOT = Path(__file__).resolve().parents[3] / "samples"


class _VisionTextBlock:
    """Minimal duck-type for an Anthropic SDK TextBlock; FakeLLM
    hands this response back when the analyst calls the vision
    transcription path (Phase 5c)."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _VisionSdkResponse:
    def __init__(self, text: str) -> None:
        self.content = [_VisionTextBlock(text)]

        class _Usage:
            input_tokens = 100
            output_tokens = 50

        self.usage = _Usage()


def _expected() -> dict[str, Any]:
    return json.loads((_SAMPLES_ROOT / "expected" / "rfp_messy.json").read_text(encoding="utf-8"))


def _fake_llm_from_expected(expected: dict[str, Any]) -> FakeLLM:
    """Build a FakeLLM response that mirrors the expected items."""

    items: list[dict[str, Any]] = []
    for item in expected["items"]:
        items.append(
            {
                "kind": "material",
                "description": item["description"],
                "quantity_text": item["quantity_text"],
                "unit_raw": item["unit_raw"],
                "source_page": item["source_page"],
                "source_quote": item["quote"],
            }
        )
    for labor in expected.get("labor", []):
        items.append(
            {
                "kind": "labor",
                "description": labor["description"],
                "quantity_text": labor["quantity_text"],
                "unit_raw": labor["unit_raw"],
                "source_page": labor["source_page"],
                "source_quote": labor["quote"],
            }
        )
    payload = {
        "items": items,
        "facts": {"project_name": None, "due_date_text": None, "general_notes": []},
        "unreadable_pages": [],
    }
    # Phase 5c: rfp_messy page 7 is text+vision, so the analyst
    # issues one vision-transcription call BEFORE the extraction
    # call. Script a plausible transcription containing the page-7
    # items; verify.py will still stamp them read_via='vision',
    # verified=False (DECISIONS #26).
    page_7_transcription = _VisionSdkResponse(
        text=(
            "Common nails 8d 2 boxes\n"
            "Deck screws 3 in 3 boxes\n"
            "Joist hangers 2x8 40 ea south wall deck ledger\n"
        )
    )
    return FakeLLM(
        responses=[
            page_7_transcription,
            ScriptedResponse(content=payload, tokens_in=2500, tokens_out=1200),
        ]
    )


@pytest_asyncio.fixture
async def ctx(db: Database, bus: EventBus):
    await seed_run(db)
    budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
    budget.start()
    yield AgentContext(run_id="run_abc", bus=bus, budget=budget)
    unregister_all()


async def _run_analyst(ctx: AgentContext) -> tuple[list[ItemView], list[dict[str, Any]]]:
    """Delegate directly to the analyst, assert ok, return the
    rehydrated items + page_routes payload."""

    result = await delegate(
        AgentName.document_analyst,
        AnalystRequest(
            kind="pdf",
            pdf_path=str(_SAMPLES_ROOT / "rfp_messy.pdf"),
            pasted_text=None,
            document_tag="document_test_messy",
        ),
        ctx=ctx,
    )
    assert result.status == "ok", (result.status, [e.message for e in result.errors])
    out = result.output.model_dump(mode="python")  # type: ignore[union-attr]
    items = [ItemView.model_validate(i) for i in out["items"]]
    return items, out["page_routes"]


class TestRfpMessyAnalyst:
    async def test_page_routes_match_expected(self, ctx: AgentContext) -> None:
        """Page 7 MUST be `text+vision` — the fastener table has a
        None cell (pdfplumber cell shift). The rest are plain text."""

        expected = _expected()
        register_default_analyst(_fake_llm_from_expected(expected))
        _items, page_routes = await _run_analyst(ctx)
        got = {str(r["page_no"]): r["route"] for r in page_routes}
        assert got == expected["page_routes"], (
            f"page routes drift: got {got}, expected {expected['page_routes']}"
        )

    async def test_wrapped_line_item_5_verifies(self, ctx: AgentContext) -> None:
        """Item 5 ("2x6 PT for sill plates on the concrete slab")
        was wrapped across two lines in the generated PDF. The
        quote-match normalizer MUST strip the line break and still
        produce `source.verified=True`."""

        expected = _expected()
        register_default_analyst(_fake_llm_from_expected(expected))
        items, _ = await _run_analyst(ctx)
        item5 = next(i for i in items if "sill plates" in i.description.lower())
        assert item5.source.verified is True, (
            f"wrapped-line item failed to verify: notes={item5.notes!r}, "
            f"quote={item5.source.quote!r}"
        )
        assert item5.source.read_via == "text"
        assert "quote_not_found" not in item5.notes

    async def test_must_not_include_strings_never_appear(self, ctx: AgentContext) -> None:
        """Header/footer boilerplate and the planted prompt
        injection never appear in any extracted item's description
        or quote."""

        expected = _expected()
        register_default_analyst(_fake_llm_from_expected(expected))
        items, _ = await _run_analyst(ctx)
        bad_strings: list[str] = expected["must_not_include"]
        leaks: list[tuple[str, str, str]] = []
        for item in items:
            for bad in bad_strings:
                if bad in item.description:
                    leaks.append((bad, "description", item.description))
                if bad in (item.source.quote or ""):
                    leaks.append((bad, "quote", item.source.quote or ""))
        assert not leaks, f"must_not_include leaked into items: {leaks!r}"

    async def test_all_text_route_items_verify_against_real_page_text(
        self, ctx: AgentContext
    ) -> None:
        """Every item on a TEXT-routed page must pass code-only
        quote verification against the real generated PDF.

        Items on vision / text+vision pages (rfp_messy page 7) are
        verified=False by rule (DECISIONS #26, SPEC §6.2.5) — the
        frontend fixture mirrors this. They're excluded here."""

        expected = _expected()
        register_default_analyst(_fake_llm_from_expected(expected))
        items, page_routes = await _run_analyst(ctx)
        vision_pages = {
            r["page_no"] for r in page_routes if r["route"] in ("vision", "text+vision")
        }
        unverified = [
            (item.id, item.description, item.notes)
            for item in items
            if item.source.page not in vision_pages and not item.source.verified
        ]
        assert not unverified, f"unverified items from real PDF: {unverified!r}"

        # Every vision-routed item carries the matching note + flags.
        for item in items:
            if item.source.page in vision_pages:
                assert item.source.read_via == "vision"
                assert item.source.verified is False
                assert "read_via_vision" in item.notes
