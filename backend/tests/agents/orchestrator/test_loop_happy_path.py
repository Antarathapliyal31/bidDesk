"""FakeLLM-scripted end-to-end: Document Analyst + Pricing Agent
+ Orchestrator loop → a validated `agent_bid` + `draft_bid`.

Scope stays small so the scripting is tractable:
  - `list_paste.txt` pasted-text run
  - 2 scripted items (2x4 SPF stud + 5/8 Type X drywall)
  - 1 pricing batch (both items in one go)
  - 1 orchestrator turn carrying classify_items + price_items +
    submit_bid in a single response

What it proves:
  - Loop plumbing: analyst delegate → orchestrator turn →
    pricing delegate → bid assembly → `bid.ready` event.
  - Tool dispatch, trace spans, event stream.
  - Final `OrchestratorOutcome.status == 'completed'`,
    `generated_by == 'orchestrator'`.
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
        run_id="run_e2e",
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="list_paste.txt",
        created_at="2026-11-12T00:00:00+00:00",
    )
    return "run_e2e"


def _analyst_fake() -> FakeLLM:
    """Analyst scripts two priceable items."""

    payload = {
        "items": [
            {
                "kind": "material",
                "description": "2x4x8 SPF #2",
                "quantity_text": "50",
                "unit_raw": "pcs",
                "source_page": 1,
                "source_quote": "2x4x8 SPF #2 - 50 pcs",
            },
            {
                "kind": "material",
                "description": "5/8 Type X drywall",
                "quantity_text": "2400",
                "unit_raw": "sqft",
                "source_page": 1,
                "source_quote": "5/8 Type X drywall - 2400 sqft",
            },
        ],
        "facts": {"project_name": None, "due_date_text": None, "general_notes": []},
        "unreadable_pages": [],
    }
    return FakeLLM(responses=[ScriptedResponse(content=payload, tokens_in=500, tokens_out=200)])


def _pricing_fake(stud_sku: str, drywall_sku: str, item_a: str, item_b: str) -> FakeLLM:
    """Pricing: one query per item + one submit_matches."""

    return FakeLLM(
        responses=[
            ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": item_a,
                            "entity": "products",
                            "filters": {"nominal_size": "2x4", "length_ft": 8, "species": "SPF"},
                        },
                        id_="tu_qa",
                    )
                ]
            ),
            ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": item_b,
                            "entity": "products",
                            "filters": {"category": "drywall", "thickness_in": "5/8"},
                        },
                        id_="tu_qb",
                    )
                ]
            ),
            ScriptedResponse(
                content=[
                    _tool_use(
                        "submit_matches",
                        {
                            "results": [
                                {
                                    "item_id": item_a,
                                    "sku": stud_sku,
                                    "searched": "SPF 2x4x8",
                                },
                                {
                                    "item_id": item_b,
                                    "sku": drywall_sku,
                                    "searched": "5/8 Type X drywall",
                                },
                            ]
                        },
                        id_="tu_submit_matches",
                    )
                ]
            ),
        ]
    )


def _orch_fake(item_a: str, item_b: str) -> FakeLLM:
    """Single orchestrator turn: classify both items → price both
    → submit_bid with a clean summary."""

    return FakeLLM(
        responses=[
            ScriptedResponse(
                content=[
                    _tool_use(
                        "classify_items",
                        {
                            "entries": [
                                {"item_id": item_a, "category": "price", "reason": "stud"},
                                {"item_id": item_b, "category": "price", "reason": "drywall"},
                            ]
                        },
                        id_="tu_classify",
                    ),
                    _tool_use(
                        "price_items",
                        {"item_ids": [item_a, item_b]},
                        id_="tu_price",
                    ),
                    _tool_use(
                        "submit_bid",
                        {
                            "summary": (
                                "Two material lines priced: 2x4x8 SPF studs and "
                                "5/8 Type X drywall for the garage ceiling."
                            ),
                            "assumptions": [],
                            "contractor_questions": [],
                        },
                        id_="tu_submit_bid",
                    ),
                ]
            )
        ]
    )


class TestOrchestratorHappyPath:
    async def test_full_loop_produces_valid_bid(
        self, db: Database, bus: EventBus, bundle, catalog_db: Path, seeded_run: str
    ) -> None:
        # Known seed SKUs — don't mine the DB at runtime; picking
        # from the Phase-1 answer keys.
        stud_sku = "LUM-2X4-8-SPF-2"
        drywall_sku = "DRY-5_8-48X96-TYPEX"

        # The analyst assigns "item_1", "item_2" to the two items it
        # extracts; `register_default_analyst` wires FakeLLM in.
        item_a, item_b = "item_1", "item_2"

        unregister_all()
        register_default_analyst(_analyst_fake())
        register_pricing_for_run(
            _pricing_fake(stud_sku, drywall_sku, item_a, item_b),
            bundle=bundle,
            catalog_db_path=catalog_db,
        )

        budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
        budget.start()
        ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)

        branch = BranchInfo(id="BR-NJ-01", name="NJ Branch", tax_rate_ppm=66_250)
        try:
            outcome = await run_orchestrator(
                RunPlan(
                    kind="text",
                    pdf_path=None,
                    pasted_text="2x4x8 SPF #2 - 50 pcs\n5/8 Type X drywall - 2400 sqft\n",
                    branch_id="BR-NJ-01",
                    document_tag="document_test_e2e",
                ),
                ctx=ctx,
                db=db,
                bundle=bundle,
                catalog_db_path=catalog_db,
                llm=_orch_fake(item_a, item_b),
                branch=branch,
            )
        finally:
            unregister_all()

        # Outcome assertions.
        assert outcome.status == "completed", (outcome.status, outcome.partial_reasons)
        assert outcome.outcome == "bid_ready"
        assert outcome.generated_by == GeneratedBy.orchestrator
        assert outcome.agent_bid_id and outcome.draft_bid_id

        # Agent bid persisted + has both lines priced.
        cur = await db.read_conn.execute(
            "SELECT id, kind, revision FROM bids WHERE run_id = ? ORDER BY revision",
            (seeded_run,),
        )
        try:
            rows = await cur.fetchall()
        finally:
            await cur.close()
        assert [(r[1], r[2]) for r in rows] == [("agent", 0), ("draft", 1)]

        # Events: bid.ready landed on the bus (persisted via EventBus).
        events = await repo.list_events_after(db, run_id=seeded_run, after_id=0)
        types = [e["type"] for e in events]
        assert "bid.ready" in types
        # Tool activity appears in events.
        payloads = [str(e["payload_json"]) for e in events if e["type"] == "tool.called"]
        joined = "\n".join(payloads)
        assert "classify_items" in joined
        assert "submit_bid" in joined
