"""Pricing tool-loop tests (FakeLLM multi-turn).

Covers:
  - Happy path: one query_catalog turn, then submit_matches.
  - Requirements are merged by code (Antara addition #3): the
    sill-plate PT requirement lands in filters even when the
    model omits it.
  - Candidate SKUs are tracked across turns.
  - Multi-tool-use per turn: one assistant block with two
    query_catalog calls is handled correctly.
  - `max_steps` cap: when the loop hits it without submit_matches,
    the outcome carries `partial_item_ids` + `submit=None` instead
    of raising (Antara addition #4).
  - PricingShapeError fires when a turn has neither tool.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.agents.pricing.extraction import (
    PricingLoopInput,
    PricingShapeError,
    run_pricing_loop,
)
from app.contracts import InputKind, ItemView
from app.contracts.common import ItemKind, SourceRef
from app.contracts.pricing_agent import PricingItemRequest, RequirementSpec
from app.db import repo
from app.runtime.budgets import Budget
from app.runtime.llm import FakeLLM, ScriptedResponse


def _tool_use(name: str, input_: dict, id_: str = "tu_1") -> SimpleNamespace:
    """SDK-shape block object (content list entries)."""

    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _item(id_: str, description: str, qty: str = "10", unit: str = "ea") -> ItemView:
    return ItemView(
        id=id_,
        kind=ItemKind.material,
        description=description,
        quantity_text=qty,
        unit_raw=unit,
        source=SourceRef(
            page=1,
            quote="irrelevant for extraction tests",
            read_via="text",
            verified=True,
        ),
        notes=[],
    )


@pytest_asyncio.fixture
async def budget() -> Budget:
    b = Budget(turns=20, tokens=200_000, deadline_s=60.0)
    b.start()
    return b


@pytest_asyncio.fixture
async def seeded_run(db) -> str:
    await repo.insert_run(
        db,
        run_id="run_px",
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="list_paste.txt",
        created_at=datetime(2026, 11, 11, 12, 0, 0).isoformat() + "+00:00",
    )
    return "run_px"


# ---------------------------------------------------------------------------
# Happy path + candidate tracking
# ---------------------------------------------------------------------------


class TestHappyPath:
    async def test_loop_completes_in_two_turns(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        # Turn 1: model issues a query_catalog
        # Turn 2: model issues submit_matches
        fake = FakeLLM(
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
                        )
                    ],
                    tokens_in=200,
                    tokens_out=50,
                ),
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "submit_matches",
                            {
                                "results": [
                                    {
                                        "item_id": "it_1",
                                        "sku": "__PLACEHOLDER__",
                                        "searched": "SPF 2x4x8",
                                    }
                                ]
                            },
                        )
                    ],
                    tokens_in=100,
                    tokens_out=30,
                ),
            ]
        )

        req = PricingItemRequest(
            item=_item("it_1", "SPF 2x4x8 studs"),
            requirements=[],
        )

        outcome = await run_pricing_loop(
            fake,
            input_=PricingLoopInput(
                run_id=seeded_run,
                branch_id="BR-NJ-01",
                items=[req],
                document_tag="document_test",
                bundle=bundle,
                catalog_db_path=catalog_db,
            ),
            bus=bus,
            budget=budget,
        )

        assert outcome.submit is not None
        assert outcome.steps == 2
        assert outcome.partial_item_ids == []
        # Candidate set populated by query_catalog
        assert outcome.candidates_by_item["it_1"]
        # The placeholder sku is not actually in the DB — but that
        # becomes a verify.py problem, not an extraction one.


# ---------------------------------------------------------------------------
# Requirement merging (Antara addition #3)
# ---------------------------------------------------------------------------


class TestRequirementMerging:
    """The sill-plate PT requirement is merged by code; the model
    didn't include `treatment` in its filters, but the executed
    query does."""

    async def test_sill_plate_pt_requirement_merged(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        # Model's query omits `treatment` — code should inject it.
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "sill_1",
                                "entity": "products",
                                "filters": {
                                    "nominal_size": "2x6",
                                    "length_ft": 16,
                                },
                            },
                        )
                    ]
                ),
                # Reply with submit_matches so the loop ends cleanly.
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "submit_matches",
                            {
                                "results": [
                                    {
                                        "item_id": "sill_1",
                                        "sku": None,
                                        "searched": "sill plate PT",
                                    }
                                ]
                            },
                        )
                    ]
                ),
            ]
        )

        req = PricingItemRequest(
            item=_item("sill_1", "2x6x16 sill plate"),
            requirements=[
                RequirementSpec(field="treatment", value="pressure_treated_ground_contact")
            ],
        )

        outcome = await run_pricing_loop(
            fake,
            input_=PricingLoopInput(
                run_id=seeded_run,
                branch_id="BR-NJ-01",
                items=[req],
                document_tag="document_test",
                bundle=bundle,
                catalog_db_path=catalog_db,
            ),
            bus=bus,
            budget=budget,
        )

        # Candidate SKUs for sill_1 must only contain PT-ground-contact
        # SKUs — if the merge didn't happen, the query would have
        # returned untreated SPF 2x6x16 rows too.
        import sqlite3

        with sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True) as c:
            placeholders = ",".join("?" * len(outcome.candidates_by_item["sill_1"]))
            rows = c.execute(
                f"SELECT treatment FROM products WHERE sku IN ({placeholders})",
                list(outcome.candidates_by_item["sill_1"]),
            ).fetchall()
        assert rows, "sill plate PT query should return at least one row"
        assert all(r[0] == "pressure_treated_ground_contact" for r in rows), (
            "merged requirement should restrict candidates to PT-ground-contact"
        )


# ---------------------------------------------------------------------------
# Multi tool_use per turn
# ---------------------------------------------------------------------------


class TestMultiToolUsePerTurn:
    async def test_two_query_calls_in_one_turn(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        # One assistant message with two tool_use blocks, one per item.
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "it_a",
                                "entity": "products",
                                "filters": {"species": "SPF", "length_ft": 8},
                            },
                            id_="tu_a",
                        ),
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "it_b",
                                "entity": "products",
                                "filters": {"species": "SPF", "length_ft": 10},
                            },
                            id_="tu_b",
                        ),
                    ]
                ),
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "submit_matches",
                            {
                                "results": [
                                    {
                                        "item_id": "it_a",
                                        "sku": None,
                                        "searched": "x",
                                    },
                                    {
                                        "item_id": "it_b",
                                        "sku": None,
                                        "searched": "y",
                                    },
                                ]
                            },
                        )
                    ]
                ),
            ]
        )

        outcome = await run_pricing_loop(
            fake,
            input_=PricingLoopInput(
                run_id=seeded_run,
                branch_id="BR-NJ-01",
                items=[
                    PricingItemRequest(item=_item("it_a", "a"), requirements=[]),
                    PricingItemRequest(item=_item("it_b", "b"), requirements=[]),
                ],
                document_tag="document_test",
                bundle=bundle,
                catalog_db_path=catalog_db,
            ),
            bus=bus,
            budget=budget,
        )
        # Both items' candidate sets populated by the SAME turn.
        assert outcome.candidates_by_item["it_a"]
        assert outcome.candidates_by_item["it_b"]


# ---------------------------------------------------------------------------
# max_steps partial outcome (Antara addition #4)
# ---------------------------------------------------------------------------


class TestMaxStepsPartial:
    async def test_cap_without_submit_returns_partial(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        # Infinite query_catalog loop → set max_steps=1 so the cap
        # triggers after one turn.
        def _query_turn() -> ScriptedResponse:
            return ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": "loop_1",
                            "entity": "products",
                            "filters": {"species": "SPF"},
                        },
                    )
                ]
            )

        fake = FakeLLM(responses=[_query_turn(), _query_turn()])

        outcome = await run_pricing_loop(
            fake,
            input_=PricingLoopInput(
                run_id=seeded_run,
                branch_id="BR-NJ-01",
                items=[PricingItemRequest(item=_item("loop_1", "loop"), requirements=[])],
                document_tag="document_test",
                bundle=bundle,
                catalog_db_path=catalog_db,
                max_steps=1,
            ),
            bus=bus,
            budget=budget,
        )

        # Soft outcome, not a raise.
        assert outcome.submit is None
        assert outcome.partial_item_ids == ["loop_1"]


# ---------------------------------------------------------------------------
# Shape errors
# ---------------------------------------------------------------------------


class TestShapeErrors:
    async def test_no_tool_use_raises(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        # Response with text only, no tool_use.
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[SimpleNamespace(type="text", text="hello")],
                )
            ]
        )
        with pytest.raises(PricingShapeError, match=r"no query_catalog or submit_matches"):
            await run_pricing_loop(
                fake,
                input_=PricingLoopInput(
                    run_id=seeded_run,
                    branch_id="BR-NJ-01",
                    items=[PricingItemRequest(item=_item("x", "x"), requirements=[])],
                    document_tag="document_test",
                    bundle=bundle,
                    catalog_db_path=catalog_db,
                ),
                bus=bus,
                budget=budget,
            )

    async def test_bad_submit_payload_raises_shape_error(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        # submit_matches with wrong shape (missing required fields).
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "submit_matches",
                            {"results": [{"item_id": "x"}]},  # missing sku / searched
                        )
                    ]
                )
            ]
        )
        with pytest.raises(PricingShapeError, match=r"submit_matches payload failed"):
            await run_pricing_loop(
                fake,
                input_=PricingLoopInput(
                    run_id=seeded_run,
                    branch_id="BR-NJ-01",
                    items=[PricingItemRequest(item=_item("x", "x"), requirements=[])],
                    document_tag="document_test",
                    bundle=bundle,
                    catalog_db_path=catalog_db,
                ),
                bus=bus,
                budget=budget,
            )


# ---------------------------------------------------------------------------
# DECISIONS F15: step cap scales with batch size
# ---------------------------------------------------------------------------


class TestStepsForBatch:
    def test_scales_linearly_with_floor(self) -> None:
        from app.agents.pricing.agent import steps_for_batch

        # Floor at 6 for small batches.
        assert steps_for_batch(1) == 6
        assert steps_for_batch(2) == 6
        # 2*N+2 from N=3 onward.
        assert steps_for_batch(3) == 8
        assert steps_for_batch(4) == 10
        assert steps_for_batch(8) == 18

    async def test_eight_item_batch_that_used_to_hit_cap_now_submits(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        """Scripted 8-item batch: each step issues ONE query_catalog
        for a different item (the pathological case the real model
        occasionally falls into). Old floor-6 cap would stop at
        step 7 with `partial_item_ids` populated; the new cap
        (steps_for_batch(8)=18) lets the submit land."""

        from app.agents.pricing.agent import steps_for_batch

        items = [
            PricingItemRequest(
                item=_item(f"it_{i}", f"2x4 x 8 SPF item {i}", qty="1", unit="ea"),
                requirements=[],
            )
            for i in range(8)
        ]

        responses: list[ScriptedResponse] = []
        for i in range(8):
            responses.append(
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": f"it_{i}",
                                "entity": "products",
                                "filters": {
                                    "nominal_size": "2x4",
                                    "length_ft": 8,
                                    "species": "SPF",
                                },
                            },
                            id_=f"tu_q{i}",
                        )
                    ]
                )
            )
        responses.append(
            ScriptedResponse(
                content=[
                    _tool_use(
                        "submit_matches",
                        {
                            "results": [
                                {
                                    "item_id": f"it_{i}",
                                    "sku": "LUM-2X4-8-SPF-2",
                                    "searched": "SPF 2x4x8",
                                }
                                for i in range(8)
                            ]
                        },
                        id_="tu_submit",
                    )
                ]
            )
        )
        fake = FakeLLM(responses=responses)

        outcome = await run_pricing_loop(
            fake,
            input_=PricingLoopInput(
                run_id=seeded_run,
                branch_id="BR-NJ-01",
                items=items,
                document_tag="document_test",
                bundle=bundle,
                catalog_db_path=catalog_db,
                max_steps=steps_for_batch(len(items)),
            ),
            bus=bus,
            budget=budget,
        )
        assert outcome.submit is not None
        assert outcome.partial_item_ids == []
        # 9 model steps: 8 query turns + 1 submit.
        assert outcome.steps == 9

    async def test_eight_item_batch_with_old_cap_still_partials(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        """Same scripted sequence but with the pre-F15 cap (6)
        — proves the regression we're guarding against."""

        items = [
            PricingItemRequest(
                item=_item(f"it_{i}", f"2x4 x 8 SPF item {i}", qty="1", unit="ea"),
                requirements=[],
            )
            for i in range(8)
        ]
        responses = [
            ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": f"it_{i}",
                            "entity": "products",
                            "filters": {"species": "SPF"},
                        },
                        id_=f"tu_q{i}",
                    )
                ]
            )
            for i in range(8)
        ]
        fake = FakeLLM(responses=responses)

        outcome = await run_pricing_loop(
            fake,
            input_=PricingLoopInput(
                run_id=seeded_run,
                branch_id="BR-NJ-01",
                items=items,
                document_tag="document_test",
                bundle=bundle,
                catalog_db_path=catalog_db,
                max_steps=6,  # pre-F15 default
            ),
            bus=bus,
            budget=budget,
        )
        assert outcome.submit is None
        assert len(outcome.partial_item_ids) == 8


# ---------------------------------------------------------------------------
# DECISIONS F19: _merge_requirements coerces int → bool on bool fields
# ---------------------------------------------------------------------------


class TestMergeRequirementsBoolCoercion:
    async def test_mr_drywall_requirement_with_int_1_finds_mr_sku(
        self, bus, budget, bundle, catalog_db: Path, seeded_run
    ) -> None:
        """`wet_area_drywall_mr.requirement.value` used to be int `1`
        against a bool field; the query returned zero rows and the
        model landed on no_match. With the YAML flipped to `true`
        AND the coercion in `_merge_requirements`, either path
        reaches `DRY-1_2-48X96-MR`. Script the model to send the
        int form; coercion is what lets the query land."""

        from app.agents.pricing.extraction import run_pricing_loop
        from app.contracts.pricing_agent import RequirementSpec

        item = PricingItemRequest(
            item=_item(
                "it_mr",
                "1/2 in drywall for upstairs bathrooms",
                qty="800",
                unit="sq ft",
            ),
            requirements=[RequirementSpec(field="moisture_resistant", value=1)],
        )
        responses = [
            ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": "it_mr",
                            "entity": "products",
                            "filters": {"category": "drywall", "thickness_in": "1/2"},
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
                                    "item_id": "it_mr",
                                    "sku": "DRY-1_2-48X96-MR",
                                    "searched": "1/2 drywall MR",
                                }
                            ]
                        },
                        id_="tu_sm",
                    )
                ]
            ),
        ]
        outcome = await run_pricing_loop(
            FakeLLM(responses=responses),
            input_=PricingLoopInput(
                run_id=seeded_run,
                branch_id="BR-NJ-01",
                items=[item],
                document_tag="document_test",
                bundle=bundle,
                catalog_db_path=catalog_db,
            ),
            bus=bus,
            budget=budget,
        )
        # The query_catalog step must have found the MR SKU in the
        # merged candidate set (coercion worked).
        assert "DRY-1_2-48X96-MR" in {
            row.get("sku") for row in outcome.candidate_rows_by_item["it_mr"]
        }
        assert outcome.submit is not None
        assert outcome.submit.results[0].sku == "DRY-1_2-48X96-MR"


class TestMergeRequirementsCoerceFunctions:
    def test_int_one_coerces_to_true_on_bool_field(self) -> None:
        from app.agents.pricing.extraction import _coerce_requirement_value

        types = {"moisture_resistant": "bool"}
        assert _coerce_requirement_value("moisture_resistant", 1, types) is True
        assert _coerce_requirement_value("moisture_resistant", 0, types) is False
        assert _coerce_requirement_value("moisture_resistant", "true", types) is True
        assert _coerce_requirement_value("moisture_resistant", "false", types) is False
        # Already-bool pass-through.
        assert _coerce_requirement_value("moisture_resistant", True, types) is True

    def test_non_bool_fields_untouched(self) -> None:
        from app.agents.pricing.extraction import _coerce_requirement_value

        types = {"length_ft": "int", "species": "enum"}
        assert _coerce_requirement_value("length_ft", 8, types) == 8
        assert _coerce_requirement_value("species", "SPF", types) == "SPF"
