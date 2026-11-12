"""FakeLLM-scripted tests for the Live-view SSE event fan-out
added on `frontend-hookup` (DECISIONS F12).

Asserted events + payload shapes:

  - `page.routed`    — one per analyst page_route entry.
  - `item.extracted` — one per analyst item, payload matches the
                       `item` ItemView (SPEC §10.3).
  - `item.priced`    — one per priced row after `handle_price_items`
                       commits, payload matches PricedLineView.
  - `conflict.flagged` — once per accepted `handle_flag_conflict`.
  - `item.flagged`   — mirrors in_conflict onto both sides of a
                       conflict; mirrors `failed`/`low_confidence`/
                       etc. off priced rows.

All payloads validate through `RunEventAdapter` on the way out of
`EventBus.emit`, so missing/extra fields fail loudly at emit-time
— the tests focus on content + ordering.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio

from app.agents.analyst.agent import register_default_analyst
from app.agents.orchestrator.loop import RunPlan, run_orchestrator
from app.agents.orchestrator.tools import (
    handle_flag_conflict,
    handle_price_items,
    register_pricing_for_run,
)
from app.contracts import InputKind
from app.contracts.bid import BranchInfo
from app.contracts.common import SourceRef
from app.contracts.orchestrator import (
    ConflictSideInput,
    FlagConflictInput,
    PriceItemsInput,
)
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
        run_id="run_live",
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="list_paste.txt",
        created_at="2026-11-12T00:00:00+00:00",
    )
    return "run_live"


def _analyst_fake() -> FakeLLM:
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
                                {"item_id": item_a, "sku": stud_sku, "searched": "SPF 2x4x8"},
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
                    _tool_use("price_items", {"item_ids": [item_a, item_b]}, id_="tu_price"),
                    _tool_use(
                        "submit_bid",
                        {
                            "summary": (
                                "Two material lines priced: 2x4x8 SPF studs and 5/8 Type "
                                "X drywall for the garage ceiling."
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


class TestLiveEventFanOut:
    async def test_full_loop_emits_extracted_priced_and_ordering(
        self, db: Database, bus: EventBus, bundle, catalog_db: Path, seeded_run: str
    ) -> None:
        stud_sku = "LUM-2X4-8-SPF-2"
        drywall_sku = "DRY-5_8-48X96-TYPEX"
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
            await run_orchestrator(
                RunPlan(
                    kind="text",
                    pdf_path=None,
                    pasted_text="2x4x8 SPF #2 - 50 pcs\n5/8 Type X drywall - 2400 sqft\n",
                    branch_id="BR-NJ-01",
                    document_tag="document_live_events",
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

        events = await repo.list_events_after(db, run_id=seeded_run, after_id=0)
        types = [e["type"] for e in events]

        # Each expected type fires at least once.
        assert "page.routed" in types
        assert types.count("item.extracted") == 2
        assert types.count("item.priced") == 2
        assert "bid.ready" in types

        # Ordering: every item.extracted comes BEFORE every item.priced,
        # and both fire before bid.ready.
        first_extracted = next(i for i, e in enumerate(events) if e["type"] == "item.extracted")
        last_extracted = max(i for i, e in enumerate(events) if e["type"] == "item.extracted")
        first_priced = next(i for i, e in enumerate(events) if e["type"] == "item.priced")
        bid_ready_idx = next(i for i, e in enumerate(events) if e["type"] == "bid.ready")
        assert last_extracted < first_priced < bid_ready_idx
        assert first_extracted < first_priced

    async def test_item_extracted_payload_shape(
        self, db: Database, bus: EventBus, bundle, catalog_db: Path, seeded_run: str
    ) -> None:
        unregister_all()
        register_default_analyst(_analyst_fake())
        register_pricing_for_run(
            _pricing_fake("LUM-2X4-8-SPF-2", "DRY-5_8-48X96-TYPEX", "item_1", "item_2"),
            bundle=bundle,
            catalog_db_path=catalog_db,
        )
        budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
        budget.start()
        ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)
        branch = BranchInfo(id="BR-NJ-01", name="NJ Branch", tax_rate_ppm=66_250)
        try:
            await run_orchestrator(
                RunPlan(
                    kind="text",
                    pdf_path=None,
                    pasted_text="2x4x8 SPF #2 - 50 pcs\n5/8 Type X drywall - 2400 sqft\n",
                    branch_id="BR-NJ-01",
                    document_tag="document_live_events_shape",
                ),
                ctx=ctx,
                db=db,
                bundle=bundle,
                catalog_db_path=catalog_db,
                llm=_orch_fake("item_1", "item_2"),
                branch=branch,
            )
        finally:
            unregister_all()

        import json

        events = await repo.list_events_after(db, run_id=seeded_run, after_id=0)
        extracted = [e for e in events if e["type"] == "item.extracted"]
        payloads = [json.loads(e["payload_json"]) for e in extracted]
        # Per-item payload wraps an ItemView under `item`.
        ids = [p["item"]["id"] for p in payloads]
        assert ids == ["item_1", "item_2"]
        for p in payloads:
            iv = p["item"]
            # Required keys per SPEC §10.3 ItemView.
            assert set(iv.keys()) >= {
                "id",
                "kind",
                "description",
                "quantity_text",
                "unit_raw",
                "source",
                "notes",
            }
            src = iv["source"]
            assert set(src.keys()) >= {"page", "quote", "read_via", "verified"}

    async def test_item_priced_payload_shape_and_substitute_default(
        self, orch_ctx
    ) -> None:
        """`handle_price_items` emits one `item.priced` per row with
        the full PricedLineView shape — and `is_substitute=False`
        when the pricing agent didn't substitute."""

        # Seed the orchestrator's items + triage so the real pricing
        # agent will run against the catalog.
        from app.agents.pricing.agent import build_pricing_spec
        from app.runtime.delegate import register_agent

        unregister_all()
        # Scripted pricing: one query + submit_matches for the sill
        # plate; result should NOT be a substitute.
        pricing_llm = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "it_sill",
                                "entity": "products",
                                "filters": {
                                    "nominal_size": "2x6",
                                    "species": "SYP",
                                    "treatment": "pressure_treated_above_ground",
                                },
                            },
                            id_="tu_q",
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
                                        "item_id": "it_sill",
                                        "sku": "LUM-2X6-8-SYP-2-PTAG",
                                        "searched": "2x6 PT sill plate",
                                    }
                                ]
                            },
                            id_="tu_sm",
                        )
                    ]
                ),
            ]
        )
        register_agent(
            build_pricing_spec(pricing_llm, bundle=orch_ctx.bundle, catalog_db_path=orch_ctx.catalog_db_path)
        )
        try:
            await handle_price_items(
                PriceItemsInput(item_ids=["it_sill"]),
                ctx=orch_ctx,
            )
        finally:
            unregister_all()

        import json

        events = await repo.list_events_after(orch_ctx.db, run_id=orch_ctx.run_id, after_id=0)
        priced = [e for e in events if e["type"] == "item.priced"]
        assert len(priced) == 1
        line = json.loads(priced[0]["payload_json"])["line"]
        # Every PricedLineView field must be present.
        assert set(line.keys()) == {
            "item_id",
            "status",
            "sku",
            "product_name",
            "sale_qty",
            "sale_unit",
            "unit_price_cents",
            "extended_cents",
            "conversion_path",
            "confidence",
            "confidence_reasons",
            "shortfall",
            "is_substitute",
            "substitute_reason",
        }
        assert line["item_id"] == "it_sill"
        assert line["is_substitute"] is False
        assert line["substitute_reason"] is None

    async def test_flag_conflict_emits_conflict_flagged_and_item_flagged(
        self, orch_ctx
    ) -> None:
        """A persisted conflict emits:
          - one `conflict.flagged` with the ConflictView shape;
          - one `item.flagged(reason_code='in_conflict')` per
            referenced item id.
        """

        import json

        side_a = ConflictSideInput(
            item_id="it_sill",
            source=SourceRef(
                page=2,
                quote="2x6 PT for sill plates on the concrete slab",
                read_via="text",
                verified=True,
            ),
        )
        side_b = ConflictSideInput(
            item_id="it_post",
            source=SourceRef(
                page=2,
                quote="4x4 x 8' SPF #2 (ground contact)",
                read_via="text",
                verified=True,
            ),
        )
        result = await handle_flag_conflict(
            FlagConflictInput(
                type="spec_vs_list",
                side_a=side_a,
                side_b=side_b,
                explanation="demo conflict",
            ),
            ctx=orch_ctx,
        )
        assert result["ok"] is True
        conflict_id = result["conflict_id"]

        events = await repo.list_events_after(orch_ctx.db, run_id=orch_ctx.run_id, after_id=0)
        flagged = [e for e in events if e["type"] == "conflict.flagged"]
        assert len(flagged) == 1
        conflict = json.loads(flagged[0]["payload_json"])["conflict"]
        assert conflict["id"] == conflict_id
        assert conflict["type"] == "spec_vs_list"
        assert conflict["side_a"]["item_id"] == "it_sill"
        assert conflict["side_b"]["item_id"] == "it_post"
        assert conflict["explanation"] == "demo conflict"

        item_flags = [e for e in events if e["type"] == "item.flagged"]
        reasons = {
            (
                json.loads(e["payload_json"])["item_id"],
                json.loads(e["payload_json"])["reason_code"],
            )
            for e in item_flags
        }
        assert ("it_sill", "in_conflict") in reasons
        assert ("it_post", "in_conflict") in reasons

    async def test_flag_conflict_vision_routed_to_question_emits_no_events(
        self, orch_ctx
    ) -> None:
        """Vision-side conflicts route to contractor_questions and
        MUST NOT emit `conflict.flagged` or `item.flagged`."""

        side_a = ConflictSideInput(
            item_id="it_sill",
            source=SourceRef(
                page=2,
                quote="2x6 PT sill plates",
                read_via="vision",
                verified=False,
            ),
        )
        side_b = ConflictSideInput(
            item_id="it_post",
            source=SourceRef(
                page=2,
                quote="4x4 PT posts",
                read_via="text",
                verified=True,
            ),
        )
        result = await handle_flag_conflict(
            FlagConflictInput(
                type="spec_vs_list",
                side_a=side_a,
                side_b=side_b,
                explanation="demo vision conflict",
            ),
            ctx=orch_ctx,
        )
        assert result["routed"] == "contractor_question"

        events = await repo.list_events_after(orch_ctx.db, run_id=orch_ctx.run_id, after_id=0)
        assert not any(e["type"] == "conflict.flagged" for e in events)
        assert not any(e["type"] == "item.flagged" for e in events)
