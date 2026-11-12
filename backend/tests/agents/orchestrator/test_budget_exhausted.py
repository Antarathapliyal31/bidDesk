"""Regression test: a BudgetExhausted raised INSIDE a sub-agent
(pricing's internal loop) must not propagate out of
`run_orchestrator`. Loop catches it inside the handler dispatch,
emits `budget.exhausted`, records `partial_reasons`, and falls
through to bid assembly with `generated_by='fallback'`.

Before the fix, the exception leaked out of `delegate()` and
killed the whole run (observed in the first real-API smoke).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio

from app.agents.analyst.agent import register_default_analyst
from app.agents.orchestrator.loop import RunPlan, run_orchestrator
from app.agents.orchestrator.tools import register_pricing_for_run
from app.contracts import InputKind
from app.contracts.bid import BranchInfo
from app.contracts.common import GeneratedBy
from app.db import repo
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse


def _tool_use(name: str, input_: dict, id_: str = "tu") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


@pytest_asyncio.fixture
async def seeded_run(db: Database) -> str:
    await repo.insert_run(
        db,
        run_id="run_budget",
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="budget_regression.txt",
        created_at="2026-11-12T12:00:00+00:00",
    )
    return "run_budget"


class TestBudgetExhaustedLeak:
    async def test_pricing_budget_trip_falls_back_not_crashes(
        self, db: Database, bus: EventBus, bundle, catalog_db: Path, seeded_run: str
    ) -> None:
        """Tiny budget (turns=3) forces the pricing agent's internal
        loop to blow its first `assert_alive` mid-handler. The
        orchestrator loop must catch it and still write a fallback
        bid; no exception escapes `run_orchestrator`."""

        # Analyst returns one tiny item.
        analyst_fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content={
                        "items": [
                            {
                                "kind": "material",
                                "description": "2x4x8 SPF #2",
                                "quantity_text": "50",
                                "unit_raw": "pcs",
                                "source_page": 1,
                                "source_quote": "2x4x8 SPF #2 - 50 pcs",
                            },
                        ],
                        "facts": {
                            "project_name": None,
                            "due_date_text": None,
                            "general_notes": [],
                        },
                        "unreadable_pages": [],
                    }
                )
            ]
        )

        # Pricing fake: multiple scripted responses so the first
        # `budget.charge(turns=1)` inside its loop trips the cap.
        pricing_fake = FakeLLM(
            responses=[
                # Turn 1: query_catalog. This call consumes a turn
                # on the shared budget.
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "item_1",
                                "entity": "products",
                                "filters": {"nominal_size": "2x4", "species": "SPF"},
                            },
                            id_="tu_q",
                        )
                    ]
                ),
                # Turn 2: submit_matches (would normally finish the
                # batch). Won't be reached — assert_alive raises
                # before this response is consumed.
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "submit_matches",
                            {
                                "results": [
                                    {
                                        "item_id": "item_1",
                                        "sku": "LUM-2X4-8-SPF-2",
                                        "searched": "SPF 2x4x8",
                                    }
                                ]
                            },
                            id_="tu_s",
                        )
                    ]
                ),
            ]
        )

        # Orchestrator fake: classify + price + submit_bid in one turn.
        orch_fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "classify_items",
                            {
                                "entries": [
                                    {
                                        "item_id": "item_1",
                                        "category": "price",
                                        "reason": "stud",
                                    }
                                ]
                            },
                            id_="tu_cls",
                        ),
                        _tool_use(
                            "price_items",
                            {"item_ids": ["item_1"]},
                            id_="tu_price",
                        ),
                        _tool_use(
                            "submit_bid",
                            {
                                "summary": (
                                    "Single stud line; the pricing agent "
                                    "did not finish in the time allotted."
                                ),
                                "assumptions": [],
                                "contractor_questions": [],
                            },
                            id_="tu_submit",
                        ),
                    ]
                )
            ]
        )

        unregister_all()
        register_default_analyst(analyst_fake)
        register_pricing_for_run(pricing_fake, bundle=bundle, catalog_db_path=catalog_db)

        # Turn cap of 0: the orchestrator's first `budget.charge(
        # turns=1)` AFTER its model call overflows (0 limit) and
        # raises mid-loop. This is the exact code path that leaked
        # out of `run_orchestrator` in the first real-API smoke
        # (charge was outside any try/except).
        budget = Budget(turns=0, tokens=200_000, deadline_s=60.0)
        budget.start()
        ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)

        branch = BranchInfo(id="BR-NJ-01", name="NJ Branch", tax_rate_ppm=66_250)
        try:
            outcome = await run_orchestrator(
                RunPlan(
                    kind="text",
                    pdf_path=None,
                    pasted_text="2x4x8 SPF #2 - 50 pcs\n",
                    branch_id="BR-NJ-01",
                    document_tag="document_budget_regression",
                ),
                ctx=ctx,
                db=db,
                bundle=bundle,
                catalog_db_path=catalog_db,
                llm=orch_fake,
                branch=branch,
            )
        finally:
            unregister_all()

        # No exception escaped. Outcome carries the trip + fallback.
        assert outcome.status == "partial", (outcome.status, outcome.partial_reasons)
        assert outcome.generated_by == GeneratedBy.fallback
        assert any("budget_exhausted" in r for r in outcome.partial_reasons), (
            outcome.partial_reasons
        )

        # Agent + draft bids were still persisted (fallback path).
        assert outcome.agent_bid_id and outcome.draft_bid_id

        # `budget.exhausted` event landed on the bus (persisted
        # via EventBus → repo.list_events_after).
        events = await repo.list_events_after(db, run_id=seeded_run, after_id=0)
        types = [e["type"] for e in events]
        assert "budget.exhausted" in types
