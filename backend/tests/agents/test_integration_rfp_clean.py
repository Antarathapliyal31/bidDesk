"""End-to-end: run the orchestrator against `rfp_clean.pdf` with a
scripted FakeLLM that mirrors `samples/expected/rfp_clean.json`.

No real API calls. Proves:
  - Ingest + routing + extraction + verify compose.
  - Items from the FakeLLM get `source.verified=True` once code
    checks them against the real page text (SPEC §6.4).
  - Orchestrator → delegate → analyst → return path is typed
    all the way through.
  - `must_not_include` strings (header boilerplate) never land on
    items.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest_asyncio

from app.agents.analyst.agent import register_default_analyst
from app.agents.orchestrator.loop import RunPlan, run_orchestrator
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse
from tests._seeds import seed_run

_SAMPLES_ROOT = Path(__file__).resolve().parents[3] / "samples"


def _expected(name: str) -> dict[str, Any]:
    return json.loads((_SAMPLES_ROOT / "expected" / name).read_text(encoding="utf-8"))


def _fake_llm_from_expected(expected: dict[str, Any]) -> FakeLLM:
    """Build a FakeLLM that returns a `report_extraction` payload
    built from the expected JSON. That means the test loads the
    same samples the Phase-1 answer-key produced, so hand-typed
    counts are out of the loop (review rule 8)."""

    items = [
        {
            "kind": "material",
            "description": item["description"],
            "quantity_text": item["quantity_text"],
            "unit_raw": item["unit_raw"],
            "source_page": item["source_page"],
            "source_quote": item["quote"],
        }
        for item in expected["items"]
    ]
    for excl in expected.get("exclusions", []):
        items.append(
            {
                "kind": "exclusion",
                "description": excl["description"],
                "quantity_text": None,
                "unit_raw": None,
                "source_page": excl["source_page"],
                "source_quote": excl["quote"],
            }
        )
    payload = {
        "items": items,
        "facts": {"project_name": None, "due_date_text": None, "general_notes": []},
        "unreadable_pages": [],
    }
    return FakeLLM(responses=[ScriptedResponse(content=payload, tokens_in=1000, tokens_out=500)])


@pytest_asyncio.fixture
async def ctx(db: Database, bus: EventBus):
    await seed_run(db)
    budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
    budget.start()
    yield AgentContext(run_id="run_abc", bus=bus, budget=budget)
    unregister_all()


class TestRfpCleanIntegration:
    async def test_items_match_expected_and_verify(self, ctx: AgentContext, db: Database) -> None:
        expected = _expected("rfp_clean.json")
        register_default_analyst(_fake_llm_from_expected(expected))

        outcome = await run_orchestrator(
            RunPlan(
                kind="pdf",
                pdf_path=str(_SAMPLES_ROOT / "rfp_clean.pdf"),
                pasted_text=None,
                branch_id="BR-NJ-01",
                document_tag="document_test_clean",
            ),
            ctx=ctx,
        )

        # Count matches the expected items (materials + exclusions).
        expected_total = len(expected["items"]) + len(expected.get("exclusions", []))
        assert len(outcome.items) == expected_total

        # Every item verified against the real PDF's page text.
        for item in outcome.items:
            assert item.source.verified is True, (
                f"item {item.id} {item.description!r} failed to verify against real page text"
            )
            assert item.source.read_via == "text"
            assert "quote_not_found" not in item.notes

        # must_not_include check: none of those strings appear in any
        # item description or quote.
        for bad in expected["must_not_include"]:
            for item in outcome.items:
                assert bad not in item.description, f"'{bad}' leaked into {item.description!r}"
                assert bad not in (item.source.quote or "")

        # Orchestrator charged one turn.
        assert ctx.budget.snapshot()["turns"] >= 1

        # Submit_bid shim returned a non-empty summary.
        assert outcome.summary, "submit_bid shim produced empty summary"

    async def test_run_summary_has_no_dollar_amounts(self, ctx: AgentContext) -> None:
        """SPEC §7.5 / §9.2: summary MUST NOT state prices or
        totals. The Phase-3 shim summary is deterministic — it
        counts sections only."""

        expected = _expected("rfp_clean.json")
        register_default_analyst(_fake_llm_from_expected(expected))
        outcome = await run_orchestrator(
            RunPlan(
                kind="pdf",
                pdf_path=str(_SAMPLES_ROOT / "rfp_clean.pdf"),
                pasted_text=None,
                branch_id="BR-NJ-01",
                document_tag="document_test_clean_no_money",
            ),
            ctx=ctx,
        )
        import re

        assert re.search(r"\$\s?\d", outcome.summary) is None, (
            f"summary leaked a currency amount: {outcome.summary!r}"
        )
