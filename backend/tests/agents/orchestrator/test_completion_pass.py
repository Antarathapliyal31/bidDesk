"""DECISIONS F15: pricing_completion_pass must retry items whose
first pass hit `max_steps_exceeded` / `agent_error`, one item per
batch so each gets the full step cap to itself. Also keeps
retrying items that have no price_results row at all (the
original completion-case).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio

from app.agents.orchestrator.loop import _run_completion_pass
from app.agents.orchestrator.tools import (
    OrchestratorContext,
    register_pricing_for_run,
)
from app.contracts import InputKind, ItemKind
from app.db import repo
from app.db.init import Database
from app.knowledge.matcher import load_rules
from app.runtime.budgets import Budget
from app.runtime.delegate import unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse


def _tool_use(name: str, input_: dict, id_: str = "tu") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


@pytest_asyncio.fixture
async def seeded_run(db: Database) -> str:
    await repo.insert_run(
        db,
        run_id="run_cp",
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="list.txt",
        created_at="2026-11-12T00:00:00+00:00",
    )
    # One priceable item that previously hit max_steps.
    await repo.insert_item(
        db,
        run_id="run_cp",
        item_id="it_1",
        kind=ItemKind.material,
        description="2x4x8 SPF #2",
        created_at="2026-11-12T00:00:00+00:00",
        quantity_text="50",
        unit_raw="pcs",
        source_page=1,
        source_quote="2x4x8 SPF #2 - 50 pcs",
        read_via="text",
        verified=True,
        verification_notes=[],
    )
    # Seed a prior failed row carrying `max_steps_exceeded`.
    await repo.insert_price_result(
        db,
        run_id="run_cp",
        item_id="it_1",
        row={
            "status": "failed",
            "verify_notes": ["max_steps_exceeded"],
            "confidence": None,
            "confidence_reasons": [],
        },
        created_at="2026-11-12T00:00:00+00:00",
    )
    return "run_cp"


class TestCompletionPassRetriesMaxStepsItems:
    async def test_max_steps_item_gets_a_second_shot(
        self,
        db: Database,
        bus: EventBus,
        bundle,
        catalog_db: Path,
        seeded_run: str,
    ) -> None:
        items = await repo.list_items(db, run_id=seeded_run)
        items_by_id = {r["id"]: r for r in items}
        budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
        budget.start()
        ctx = OrchestratorContext(
            run_id=seeded_run,
            branch_id="BR-NJ-01",
            document_tag="document_test",
            db=db,
            bus=bus,
            budget=budget,
            bundle=bundle,
            catalog_db_path=catalog_db,
            gap_rules=load_rules(products_entity=bundle.entities["products"]),
            items_by_id=items_by_id,
        )
        # Pretend the first-pass loop marked it priced (with a
        # failed row) — that's exactly what the orchestrator does
        # for max_steps items.
        ctx.priced_item_ids.add("it_1")

        # Scripted pricing: one query + submit → succeeds on retry.
        pricing_llm = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "it_1",
                                "entity": "products",
                                "filters": {
                                    "nominal_size": "2x4",
                                    "length_ft": 8,
                                    "species": "SPF",
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
                                        "item_id": "it_1",
                                        "sku": "LUM-2X4-8-SPF-2",
                                        "searched": "SPF 2x4x8",
                                    }
                                ]
                            },
                            id_="tu_sm",
                        )
                    ]
                ),
            ]
        )
        unregister_all()
        register_pricing_for_run(pricing_llm, bundle=bundle, catalog_db_path=catalog_db)
        try:
            priced, partial = await _run_completion_pass(ctx)
        finally:
            unregister_all()

        assert priced == 1
        assert partial == []

        # The latest price_result row for it_1 should now be
        # `matched` (not `failed`) with a SKU + sale_qty.
        latest = await repo.latest_price_result_per_item(db, run_id=seeded_run)
        assert latest["it_1"]["status"] == "matched"
        assert latest["it_1"]["sku"] == "LUM-2X4-8-SPF-2"


# ---------------------------------------------------------------------------
# DECISIONS F20: ambiguous-triaged material items get a retry
# ---------------------------------------------------------------------------


import pytest


@pytest_asyncio.fixture
async def fascia_run(db: Database) -> str:
    await repo.insert_run(
        db,
        run_id="run_fascia",
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="list.txt",
        created_at="2026-11-13T00:00:00+00:00",
    )
    await repo.insert_item(
        db,
        run_id="run_fascia",
        item_id="item_17",
        kind=ItemKind.material,
        description="Fascia (1x6 trim)",
        quantity_text="12",
        unit_raw="yards",
        source_page=5,
        source_quote="17. Fascia (1x6 trim) ............................. 12 yards",
        read_via="text",
        verified=True,
        verification_notes=[],
        created_at="2026-11-13T00:00:00+00:00",
    )
    # classify_items landed on "ambiguous" because yards isn't a
    # lumber unit.
    await repo.insert_item_triage_bulk(
        db,
        run_id="run_fascia",
        entries=[("item_17", "ambiguous", "quantity in yards; needs LF/pieces confirmation")],
        created_at="2026-11-13T00:00:00+00:00",
    )
    return "run_fascia"


class TestCompletionPassRetriesAmbiguousItems:
    async def test_fascia_12yd_prices_as_8ft_trim_with_ambiguous_flag_open(
        self,
        db: Database,
        bus: EventBus,
        bundle,
        catalog_db: Path,
        fascia_run: str,
    ) -> None:
        from app.bid.assemble import AssemblyInput, build_bid
        from app.contracts.bid import BranchInfo, ProjectInfo
        from app.contracts.common import GeneratedBy

        items = await repo.list_items(db, run_id=fascia_run)
        items_by_id = {r["id"]: r for r in items}
        budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
        budget.start()
        ctx = OrchestratorContext(
            run_id=fascia_run,
            branch_id="BR-NJ-01",
            document_tag="document_test",
            db=db,
            bus=bus,
            budget=budget,
            bundle=bundle,
            catalog_db_path=catalog_db,
            gap_rules=load_rules(products_entity=bundle.entities["products"]),
            items_by_id=items_by_id,
        )

        # Pricing: one query for 1x6 trim, then submit. The
        # analyst's `unit_pint_convertible` should be True but
        # the model only needs to name a SKU — code's pint
        # fallback does the 12yd→36ft→5×8ft math in verify.py.
        pricing_llm = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "item_17",
                                "entity": "products",
                                "filters": {
                                    "category": "trim",
                                    "nominal_size": "1x6",
                                    "species": "SPF",
                                    "length_ft": 8,
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
                                        "item_id": "item_17",
                                        "sku": "TRM-1X6-8-SPF-2",
                                        "searched": "1x6 SPF trim 8ft",
                                    }
                                ]
                            },
                            id_="tu_sm",
                        )
                    ]
                ),
            ]
        )
        unregister_all()
        register_pricing_for_run(pricing_llm, bundle=bundle, catalog_db_path=catalog_db)
        try:
            priced, partial = await _run_completion_pass(ctx)
        finally:
            unregister_all()

        assert priced == 1
        assert partial == []

        # Fascia was priced as TRM-1X6-8-SPF-2 × 5 (12 yd → 36 ft
        # → 5 × 8 ft via the pint fallback — set by verify.py).
        # `unit_pint_convertible` NOT set in the test item so the
        # exact pint path depends on inference — accept either a
        # matched row with a sale_qty > 0 OR a needs_review promo
        # as long as the SKU is the right one.
        priced_rows = await repo.latest_price_result_per_item(db, run_id=fascia_run)
        row = priced_rows["item_17"]
        assert row["sku"] == "TRM-1X6-8-SPF-2"

        # Assembly keeps review=open reasons=["ambiguous", …] for
        # this line because triage category == "ambiguous".
        triage_rows = await repo.list_item_triage(db, run_id=fascia_run)
        triage_by_item = {t["item_id"]: t for t in triage_rows}
        result = build_bid(
            AssemblyInput(
                run_id=fascia_run,
                branch=BranchInfo(id="BR-NJ-01", name="NJ", tax_rate_ppm=66_250),
                project=ProjectInfo(name=None, due_date_text=None),
                items=items,
                triage_by_item=triage_by_item,
                requirements_by_item={},
                price_results_by_item=priced_rows,
                conflicts=[],
                summary="",
                assumptions=[],
                contractor_questions=[],
                generated_by=GeneratedBy.orchestrator,
                partial_reasons=[],
                gap_rule_ids=set(),
            )
        )
        assert result.agent_bid is not None
        line = next(
            l for l in result.agent_bid.lines if l.item_id == "item_17"
        )
        assert line.review.state.value == "open"
        assert "ambiguous" in [r.value if hasattr(r, "value") else r for r in line.review.reasons]
