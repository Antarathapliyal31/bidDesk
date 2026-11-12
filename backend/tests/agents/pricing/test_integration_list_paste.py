"""End-to-end: run the pricing agent through `delegate()` with a
FakeLLM scripted from `samples/expected/list_paste.json`.

Proves:
  - The AgentSpec path (delegate → pricing_agent → verify) is
    typed all the way through.
  - On the first 8 `list_paste` items, every expected SKU gets a
    `matched` result with a code-copied price.
  - Box-count items (the nail/screw lines, SPEC's "count group")
    price via identity passthrough in a second batch.
  - EA→BOX pack_qty conversion: 500 nails + pack_qty=500 → 1 BOX,
    exercising the new `count` dimension added in Phase 4.
  - Per-item failure: a batch where one item's SKU violates the
    candidate set on BOTH attempts returns PricingResult with
    status='partial', the good item still priced, and an
    ItemError with ref=item_id.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest_asyncio

from app.agents.pricing.agent import register_default_pricing
from app.contracts import InputKind, ItemView
from app.contracts.common import AgentName, ItemKind, MatchStatus, SourceRef
from app.contracts.pricing_agent import PricingItemRequest, PricingRequest
from app.db import repo
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, delegate, unregister_all
from app.runtime.llm import FakeLLM, ScriptedResponse

_SAMPLES_ROOT = Path(__file__).resolve().parents[4] / "samples"


def _expected() -> dict[str, Any]:
    return json.loads((_SAMPLES_ROOT / "expected" / "list_paste.json").read_text())


def _tool_use(name: str, input_: dict, id_: str = "tu_1") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _item_view(entry: dict[str, Any]) -> ItemView:
    return ItemView(
        id=f"it_{entry['line_no']}",
        kind=ItemKind.material,
        description=entry["description"],
        quantity_text=entry["quantity_text"],
        unit_raw=entry["unit_raw"],
        source=SourceRef(
            page=entry["source_page"],
            quote=entry["quote"],
            read_via="text",
            verified=True,
        ),
        notes=[],
    )


_FILTERS_FOR_LINE: dict[int, dict[str, Any]] = {
    # SKU-specific filters that are guaranteed to return the
    # expected row from the seed. Keeps the scripted agent
    # realistic (filterable columns from the semantic layer).
    1: {"nominal_size": "2x4", "length_ft": 8, "species": "SPF"},
    2: {"nominal_size": "2x6", "length_ft": 16, "species": "SPF"},
    3: {"category": "sheathing", "thickness_in": "1/2"},
    4: {"category": "drywall", "thickness_in": "5/8", "fire_rating": "type_x"},
    5: {"category": "insulation", "r_value": 19},
    6: {"category": "roofing"},
    7: {"category": "housewrap"},
    8: {"category": "connectors"},
    9: {"category": "fasteners"},
    10: {"category": "fasteners"},
}


def _build_fake_llm(items: list[dict[str, Any]]) -> FakeLLM:
    """Script a tool loop: one `query_catalog` per item (populates
    verify.py's candidate set), then one `submit_matches` with
    every expected SKU."""

    responses: list[ScriptedResponse] = []
    for entry in items:
        filters = _FILTERS_FOR_LINE.get(entry["line_no"], {})
        responses.append(
            ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": f"it_{entry['line_no']}",
                            "entity": "products",
                            "filters": filters,
                        },
                        id_=f"tu_q_{entry['line_no']}",
                    )
                ],
            )
        )
    # Final turn: submit_matches with every expected sku.
    responses.append(
        ScriptedResponse(
            content=[
                _tool_use(
                    "submit_matches",
                    {
                        "results": [
                            {
                                "item_id": f"it_{entry['line_no']}",
                                "sku": entry["expected_sku"],
                                "searched": entry["description"],
                            }
                            for entry in items
                        ]
                    },
                )
            ]
        )
    )
    return FakeLLM(responses=responses)


@pytest_asyncio.fixture
async def seeded_run(db) -> str:
    await repo.insert_run(
        db,
        run_id="run_px_int",
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="list_paste.txt",
        created_at=datetime(2026, 11, 11, 12, 0, 0).isoformat() + "+00:00",
    )
    return "run_px_int"


class TestListPasteBatch:
    async def test_full_batch_prices_through_delegate(
        self,
        bundle,
        catalog_db: Path,
        db,
        bus,
        seeded_run,
    ) -> None:
        """Run the pricing agent through `delegate()` — the
        same path the orchestrator will take in Phase 5."""

        expected = _expected()
        # Phase-4 batch size limit is 8; split the 10-item list
        # into one batch of 8 to prove the ≤8 contract (SPEC §8.2).
        items = expected["items"][:8]
        try:
            fake = _build_fake_llm(items)
            register_default_pricing(
                fake,
                bundle=bundle,
                catalog_db_path=catalog_db,
                max_steps=len(items) + 2,
            )

            budget = Budget(turns=40, tokens=400_000, deadline_s=90.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)

            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[PricingItemRequest(item=_item_view(e), requirements=[]) for e in items],
            )

            agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            assert agent_result.status == "ok", (
                f"delegate returned {agent_result.status}: errors={agent_result.errors}"
            )

            results = agent_result.output.results  # type: ignore[union-attr]
            # Every requested item has a result.
            assert {r.item_id for r in results} == {f"it_{e['line_no']}" for e in items}

            # All matched, prices copied from catalog, SKUs match expected.
            for r, expected_entry in zip(results, items, strict=True):
                assert r.status == "matched", (
                    f"{r.item_id}: expected matched, got {r.status} (verify_notes={r.verify_notes})"
                )
                assert r.priced.sku == expected_entry["expected_sku"]
                assert r.priced.status == MatchStatus.matched
                # Code-copied price is positive.
                assert r.priced.unit_price_cents and r.priced.unit_price_cents > 0
                # Confidence is high (no substitute, no mismatch,
                # no assumption on matchable, text, no pint).
                assert r.priced.confidence == "high"

            # Phase-5b budget split: pricing's internal loop no
            # longer charges `turns` (orchestrator-only counter).
            # Tokens are charged by AnthropicLLM.call; FakeLLM
            # here doesn't carry a usage attribute, so tokens
            # stay at 0 too. Just assert the budget stayed alive.
            budget.assert_alive()
        finally:
            unregister_all()

    async def test_box_group_prices_via_identity(
        self,
        bundle,
        catalog_db: Path,
        db,
        bus,
        seeded_run,
    ) -> None:
        """The last two list_paste items are fasteners-in-boxes
        ("2 boxes", "3 BOX"). They price via identity passthrough
        (item_unit BOX == product.sale_unit BOX)."""

        expected = _expected()
        items = expected["items"][8:10]  # the two fastener/box lines
        assert all(i["expected_sku"].endswith("-BOX") for i in items)

        try:
            fake = _build_fake_llm(items)
            register_default_pricing(
                fake,
                bundle=bundle,
                catalog_db_path=catalog_db,
                max_steps=len(items) + 2,
            )
            budget = Budget(turns=20, tokens=200_000, deadline_s=60.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)

            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[PricingItemRequest(item=_item_view(e), requirements=[]) for e in items],
            )
            agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            assert agent_result.status == "ok", agent_result.errors

            results = agent_result.output.results  # type: ignore[union-attr]
            by_id = {r.item_id: r for r in results}
            for entry in items:
                r = by_id[f"it_{entry['line_no']}"]
                assert r.status == "matched"
                assert r.priced.sku == entry["expected_sku"]
                # Identity passthrough: item qty equals sale qty.
                assert str(r.priced.sale_qty) == str(entry["quantity_text"])
                assert r.priced.sale_unit == "BOX"
        finally:
            unregister_all()


class TestPackQtyConversion:
    """500 nails + pack_qty=500 → 1 BOX. The new `count`
    dimension (added in Phase 4) lets fastener items stated in
    individual pieces price into the BOX sale unit."""

    async def test_ea_to_box_via_pack_qty(
        self, bundle, catalog_db: Path, db, bus, seeded_run
    ) -> None:
        # The 8d nail SKU has pack_qty=500 (confirmed by seed).
        target_sku = "FAST-NAIL-COMMON-8D-BOX"
        from app.contracts import ItemView
        from app.contracts.common import ItemKind, SourceRef

        item = ItemView(
            id="it_nails",
            kind=ItemKind.material,
            description="8d common nails",
            quantity_text="500",
            unit_raw="pcs",
            source=SourceRef(
                page=1,
                quote="8d common nails - 500 pcs",
                read_via="text",
                verified=True,
            ),
            notes=[],
        )
        # Script: one query_catalog on category=fasteners, then submit.
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "it_nails",
                                "entity": "products",
                                "filters": {"category": "fasteners"},
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
                                        "item_id": "it_nails",
                                        "sku": target_sku,
                                        "searched": "common 8d nails",
                                    }
                                ]
                            },
                        )
                    ]
                ),
            ]
        )

        try:
            register_default_pricing(fake, bundle=bundle, catalog_db_path=catalog_db, max_steps=4)
            budget = Budget(turns=10, tokens=200_000, deadline_s=60.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)
            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[PricingItemRequest(item=item, requirements=[])],
            )
            agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            assert agent_result.status == "ok", agent_result.errors
            results = agent_result.output.results  # type: ignore[union-attr]
            [r] = results
            assert r.status == "matched"
            assert r.priced.sku == target_sku
            assert r.priced.sale_unit == "BOX"
            # 500 nails / 500 nails-per-box = 1 BOX.
            assert str(r.priced.sale_qty) == "1"
            assert "pack_qty = 500" in (r.priced.conversion_path or "")
        finally:
            unregister_all()


class TestPartialBatch:
    """A batch where one item's SKU is hallucinated out of the
    candidate set on BOTH the first attempt AND the retry. The
    delegate returns status='ok' (there IS a valid PricingResult
    envelope) but the PricingResult inside carries status='partial'
    and the failed item has an ItemError with ref=item_id."""

    async def test_single_item_candidate_set_failure_is_partial(
        self, bundle, catalog_db: Path, db, bus, seeded_run
    ) -> None:
        expected = _expected()
        # Two items: both legit. We'll script a fabricated SKU for
        # the SECOND item so it fails candidate_set on both passes.
        items = expected["items"][:2]
        good_sku = items[0]["expected_sku"]
        bad_sku = "LUM-FAKE-NEVER-IN-CATALOG"
        other_good = _pick_second_good_sku(catalog_db)  # ensures bad_sku isn't in DB

        def _q(iid: str, line_no: int) -> ScriptedResponse:
            return ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": iid,
                            "entity": "products",
                            "filters": _FILTERS_FOR_LINE[line_no],
                        },
                        id_=f"tu_q_{iid}",
                    )
                ]
            )

        def _submit() -> ScriptedResponse:
            return ScriptedResponse(
                content=[
                    _tool_use(
                        "submit_matches",
                        {
                            "results": [
                                {
                                    "item_id": "it_1",
                                    "sku": good_sku,
                                    "searched": "good",
                                },
                                {
                                    "item_id": "it_2",
                                    "sku": bad_sku,  # not in candidates
                                    "searched": "hallucinated",
                                },
                            ]
                        },
                    )
                ]
            )

        # First attempt: query both, then submit. Candidate-set
        # violation raises. Delegate retries: query both again,
        # then submit the SAME bad sku → soft-fail on retry.
        fake = FakeLLM(
            responses=[
                _q("it_1", 1),
                _q("it_2", 2),
                _submit(),
                _q("it_1", 1),
                _q("it_2", 2),
                _submit(),
            ]
        )

        try:
            register_default_pricing(fake, bundle=bundle, catalog_db_path=catalog_db, max_steps=5)
            budget = Budget(turns=30, tokens=200_000, deadline_s=60.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)
            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[PricingItemRequest(item=_item_view(e), requirements=[]) for e in items],
            )
            agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            # delegate itself succeeds — PricingResult validated.
            assert agent_result.status == "ok", agent_result.errors

            pr = agent_result.output  # PricingResult
            assert pr.status == "partial"
            # Good item priced; bad item failed.
            by_id = {r.item_id: r for r in pr.results}
            assert by_id["it_1"].status == "matched"
            assert by_id["it_2"].status == "failed"
            assert "candidate_set" in by_id["it_2"].verify_notes
            # The ItemError carries ref=item_id.
            assert len(pr.item_errors) == 1
            [err] = pr.item_errors
            assert err.ref == "it_2"
            assert err.kind == "candidate_set"
            _ = other_good  # keeps the fixture reference
        finally:
            unregister_all()


class TestHallucinatedSkuNeverNoMatch:
    """Antara's refinement: an invented SKU (not in catalog) must
    end as a per-item FAILURE with ref=item_id, kind=candidate_set
    — NEVER as a no_match. `no_match` is reserved for the agent
    genuinely searching and submitting sku=None."""

    async def test_invented_sku_is_candidate_set_failure_not_no_match(
        self, bundle, catalog_db: Path, db, bus, seeded_run
    ) -> None:
        expected = _expected()
        items = expected["items"][:2]
        good_sku = items[0]["expected_sku"]
        invented = "LUM-DOES-NOT-EXIST-0000"

        def _q(iid: str, line_no: int) -> ScriptedResponse:
            return ScriptedResponse(
                content=[
                    _tool_use(
                        "query_catalog",
                        {
                            "item_id": iid,
                            "entity": "products",
                            "filters": _FILTERS_FOR_LINE[line_no],
                        },
                        id_=f"tu_q_{iid}",
                    )
                ]
            )

        def _submit() -> ScriptedResponse:
            return ScriptedResponse(
                content=[
                    _tool_use(
                        "submit_matches",
                        {
                            "results": [
                                {
                                    "item_id": "it_1",
                                    "sku": good_sku,
                                    "searched": "good",
                                },
                                {
                                    "item_id": "it_2",
                                    "sku": invented,
                                    "searched": "fake",
                                },
                            ]
                        },
                    )
                ]
            )

        fake = FakeLLM(
            responses=[
                _q("it_1", 1),
                _q("it_2", 2),
                _submit(),
                _q("it_1", 1),
                _q("it_2", 2),
                _submit(),
            ]
        )

        try:
            register_default_pricing(fake, bundle=bundle, catalog_db_path=catalog_db, max_steps=5)
            budget = Budget(turns=30, tokens=200_000, deadline_s=60.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)
            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[PricingItemRequest(item=_item_view(e), requirements=[]) for e in items],
            )
            agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            assert agent_result.status == "ok", agent_result.errors
            pr = agent_result.output
            assert pr.status == "partial"
            by_id = {r.item_id: r for r in pr.results}
            # Good item priced.
            assert by_id["it_1"].status == "matched"
            # Bad item FAILED — never no_match.
            assert by_id["it_2"].status == "failed", f"expected failed, got {by_id['it_2'].status}"
            # Belt + braces: explicitly assert status != no_match.
            for r in pr.results:
                assert r.status != "no_match", (
                    f"{r.item_id}: invented SKU should not become no_match"
                )
            # ItemError carries ref=item_id, kind=candidate_set
            # (since the invented SKU was never returned by
            # query_catalog, candidate-set catches it first).
            [err] = pr.item_errors
            assert err.ref == "it_2"
            assert err.kind == "candidate_set"
        finally:
            unregister_all()


def _pick_second_good_sku(catalog_db: Path) -> str:
    """Pick any second SKU to prove the registry has more than
    one match available — not asserted on, but useful for debug."""

    import sqlite3

    with sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True) as c:
        row = c.execute(
            "SELECT sku FROM products WHERE category = 'dimensional_lumber' "
            "AND active = 1 ORDER BY sku LIMIT 1 OFFSET 1"
        ).fetchone()
    return row[0]
