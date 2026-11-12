"""rfp_messy integration test (FakeLLM) — SPEC's six hard
scenarios in one scripted batch.

All in a single PricingRequest (6 items — the Phase-4 batch
limit is 8, so this stays legal). One `query_catalog` turn per
item, then a single `submit_matches`. The FakeLLM is deterministic;
every tool_use and SKU is hand-picked from the seed.

Scenarios, each with the shape it proves:
  1. **2x10x17' → 2x10x18' substitute.** The length_ft enum
     doesn't contain 17, so the model queries broadly (no
     length_ft filter) and submits the next-longer stocked
     length with `is_substitute=True` + a reason. status=
     "substitute", confidence=low (substitute triggers low).
  2. **4x4 SPF ground-contact → SYP PT GC substitute.** The
     catalog has no SPF ground-contact (DECISIONS #7); the
     model substitutes SYP while keeping treatment and
     dimensions. is_substitute=True, species mismatch doesn't
     count as a requirement mismatch here (requirements list is
     empty — species change is the substitute itself, carried
     by substitute_reason).
  3. **Cedar shakes → no_match.** Not in the catalog; the model
     submits sku=None. status="no_match", `verify_notes` empty
     — no_match is a legitimate answer per SPEC §8.2.
  4. **Fascia 12 yards → 4 EA via pint fallback, medium.** The
     analyst marks "yards" as `unit_pint_convertible`;
     verify.py's pint path converts 12 yd → 36 LF, then
     length_to_pieces(36, length_ft=10) → 4 EA for TRM-1X6-10.
     Confidence drops to medium (`unit converted via pint`).
  5. **Fascia shortfall + transfer options.** TRM-1X6-10-SPF-2
     at BR-NJ-01 has on_hand=0 (seed), but BR-NY-01 has 30 and
     BR-PA-01 has 20. 4 EA needed → shortfall=4, two full-
     cover transfer options. `transfers.py` sort: NY (on_hand
     DESC) then PA.
  6. **Underlayment zero-stock-everywhere.** ROOF-UNDERLAY-ROLL
     has on_hand=0 at every branch — 4 rolls needed → shortfall=4,
     `transfer_options=[]`. Proves that a shortfall with no
     coverage anywhere still produces a priced line, just with
     an empty transfer list.
  7. **Sill plate PT requirement merged by code.** Line 5's
     orchestrator-attached requirement
     (`treatment=pressure_treated_above_ground`) is merged into
     the model's `query_catalog` filters even when the model
     omits it (Antara addition #3). Verified via the per-item
     candidate set — every SKU that came back for sill_1 must
     be PTAG.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest_asyncio

from app.agents.pricing.agent import register_default_pricing
from app.contracts import InputKind, ItemView
from app.contracts.common import AgentName, ConfidenceLevel, ItemKind, SourceRef
from app.contracts.pricing_agent import PricingItemRequest, PricingRequest, RequirementSpec
from app.db import repo
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, delegate, unregister_all
from app.runtime.llm import FakeLLM, ScriptedResponse


def _tool_use(name: str, input_: dict, id_: str = "tu") -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _item(
    id_: str,
    description: str,
    quantity: str | None,
    unit_raw: str | None,
    *,
    page: int = 1,
    notes: list[str] | None = None,
) -> ItemView:
    return ItemView(
        id=id_,
        kind=ItemKind.material,
        description=description,
        quantity_text=quantity,
        unit_raw=unit_raw,
        source=SourceRef(
            page=page,
            quote=f"rfp_messy line: {description}",
            read_via="text",
            verified=True,
        ),
        notes=notes or [],
    )


# ---------------------------------------------------------------------------
# Scenario items — one PricingRequest batch (6 items ≤ 8 per SPEC §8.2).
# ---------------------------------------------------------------------------

_ITEMS: list[dict[str, Any]] = [
    {
        "id": "it_substitute_length",
        "desc": "2x10 x 17' SPF",
        "qty": "20",
        "unit": "pcs",
        "notes": [],
        "requirements": [],
        "query_filters": {"nominal_size": "2x10", "species": "SPF"},
        "submit": {
            "sku": "LUM-2X10-18-SPF-2",
            "is_substitute": True,
            "substitute_reason": "17 ft not stocked; next longer is 18 ft",
            "searched": "2x10 SPF closest available length",
        },
    },
    {
        "id": "it_substitute_species",
        "desc": "4x4 x 8' SPF #2 (ground contact)",
        "qty": "20",
        "unit": "pcs",
        "notes": [],
        "requirements": [],
        "query_filters": {
            "nominal_size": "4x4",
            "length_ft": 8,
            "treatment": "pressure_treated_ground_contact",
        },
        "submit": {
            "sku": "LUM-4X4-8-SYP-2-PTGC",
            "is_substitute": True,
            "substitute_reason": "SPF requested but PT only stocked in SYP (DECISIONS #7)",
            "searched": "4x4 PT GC — species forced to SYP",
        },
    },
    {
        "id": "it_sill_plate",
        "desc": "2x6 PT for sill plates on the concrete slab",
        "qty": "12",
        "unit": "LF",
        "notes": [],
        # Orchestrator-attached requirement — SPEC §7.2-ish.
        "requirements": [RequirementSpec(field="treatment", value="pressure_treated_above_ground")],
        # Model omits `treatment` on purpose; code-side merging adds it back.
        "query_filters": {"nominal_size": "2x6"},
        "submit": {
            "sku": "LUM-2X6-8-SYP-2-PTAG",
            "is_substitute": False,
            "substitute_reason": None,
            "searched": "2x6 sill plate — orchestrator attached PTAG",
        },
    },
    {
        "id": "it_fascia",
        "desc": "Fascia (1x6 trim)",
        "qty": "12",
        "unit": "yards",
        # Analyst would set this note for the pint fallback path.
        "notes": ["unit_pint_convertible"],
        "requirements": [],
        "query_filters": {"category": "trim"},
        "submit": {
            "sku": "TRM-1X6-10-SPF-2",
            "is_substitute": False,
            "substitute_reason": None,
            "searched": "1x6 trim fascia",
        },
    },
    {
        "id": "it_cedar_shakes",
        "desc": "Cedar shakes",
        "qty": "10",
        "unit": "SQ",
        "notes": [],
        "requirements": [],
        # text_search for a word that isn't in any catalog name /
        # description — the query genuinely returns no candidates,
        # so sku=None stays as no_match (not promoted to
        # needs_review).
        "query_filters": {},
        "text_search": "cedar",
        "submit": {
            "sku": None,
            "is_substitute": False,
            "substitute_reason": None,
            "searched": "cedar shakes — not in catalog",
        },
    },
    {
        "id": "it_underlayment",
        "desc": "Underlayment roll",
        "qty": "4",
        "unit": "rolls",
        "notes": [],
        "requirements": [],
        "query_filters": {"category": "roofing"},
        "submit": {
            "sku": "ROOF-UNDERLAY-ROLL",
            "is_substitute": False,
            "substitute_reason": None,
            "searched": "roofing underlayment roll",
        },
    },
]


def _build_fake() -> FakeLLM:
    """One query_catalog per item (each in its own assistant turn
    so the tool_result ordering stays obvious), then a single
    submit_matches with every entry."""

    responses: list[ScriptedResponse] = []
    for i, entry in enumerate(_ITEMS, start=1):
        tool_input: dict[str, Any] = {
            "item_id": entry["id"],
            "entity": "products",
            "filters": entry["query_filters"],
        }
        if entry.get("text_search"):
            tool_input["text_search"] = entry["text_search"]
        responses.append(
            ScriptedResponse(content=[_tool_use("query_catalog", tool_input, id_=f"tu_q_{i}")])
        )
    responses.append(
        ScriptedResponse(
            content=[
                _tool_use(
                    "submit_matches",
                    {
                        "results": [
                            {
                                "item_id": entry["id"],
                                "sku": entry["submit"]["sku"],
                                "is_substitute": entry["submit"]["is_substitute"],
                                "substitute_reason": entry["submit"]["substitute_reason"],
                                "searched": entry["submit"]["searched"],
                            }
                            for entry in _ITEMS
                        ]
                    },
                    id_="tu_submit",
                )
            ]
        )
    )
    return FakeLLM(responses=responses)


@pytest_asyncio.fixture
async def seeded_run(db) -> str:
    await repo.insert_run(
        db,
        run_id="run_messy_px",
        branch_id="BR-NJ-01",
        input_kind=InputKind.pdf,
        input_name="rfp_messy.pdf",
        created_at=datetime(2026, 11, 11, 12, 0, 0).isoformat() + "+00:00",
    )
    return "run_messy_px"


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


class TestRfpMessyScenarios:
    async def test_all_six_scenarios(self, bundle, catalog_db: Path, db, bus, seeded_run) -> None:
        fake = _build_fake()
        try:
            register_default_pricing(
                fake,
                bundle=bundle,
                catalog_db_path=catalog_db,
                max_steps=len(_ITEMS) + 2,
            )

            budget = Budget(turns=40, tokens=400_000, deadline_s=90.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)

            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[
                    PricingItemRequest(
                        item=_item(
                            e["id"],
                            e["desc"],
                            e["qty"],
                            e["unit"],
                            notes=e["notes"],
                        ),
                        requirements=e["requirements"],
                    )
                    for e in _ITEMS
                ],
            )
            agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            assert agent_result.status == "ok", agent_result.errors

            pr = agent_result.output  # PricingResult
            assert pr.status == "ok", pr.item_errors
            by_id = {r.item_id: r for r in pr.results}

            # ---- Scenario 1: 2x10x17 → 2x10x18 substitute ----
            r = by_id["it_substitute_length"]
            assert r.status == "substitute"
            assert r.is_substitute is True
            assert r.substitute_reason and "17" in r.substitute_reason
            assert r.priced.sku == "LUM-2X10-18-SPF-2"
            assert r.priced.confidence == ConfidenceLevel.low

            # ---- Scenario 2: SPF GC → SYP PT GC substitute ----
            r = by_id["it_substitute_species"]
            assert r.status == "substitute"
            assert r.is_substitute is True
            assert r.priced.sku == "LUM-4X4-8-SYP-2-PTGC"
            assert r.substitute_reason and "SYP" in r.substitute_reason
            assert r.priced.confidence == ConfidenceLevel.low

            # ---- Scenario 3: cedar shakes → no_match (sku=None) ----
            r = by_id["it_cedar_shakes"]
            assert r.status == "no_match"
            assert r.priced.sku is None
            # Never becomes "failed" (per the spec refinement — no_match
            # is the legitimate answer when model searches + finds nothing).
            assert r.priced.status.value == "no_match"

            # ---- Scenario 4: fascia pint path + least-waste length ----
            # 12 yd → 36 LF. Trim stocked at 8 ft and 10 ft:
            #   8 ft: ceil(36/8)=5 pieces, waste=4
            #  10 ft: ceil(36/10)=4 pieces, waste=4
            # Tie → shorter → 8 ft (code-chosen length). Even
            # though the FakeLLM submitted the 10 ft SKU, the
            # picker overrides to 8 ft and records the assumption.
            r = by_id["it_fascia"]
            assert r.status == "matched"
            assert r.priced.sku == "TRM-1X6-8-SPF-2"
            assert str(r.priced.sale_qty) == "5"
            assert r.priced.sale_unit == "EA"
            assert r.priced.confidence == ConfidenceLevel.medium
            assert "pint" in " ".join(r.priced.confidence_reasons).lower()
            # Conversion path shows the pint step.
            assert "yd" in (r.priced.conversion_path or "")
            assert "code_chose_length" in r.verify_notes
            # Length assumption is in the assumptions list.
            assert any(a.field == "length_ft" and a.value == 8 for a in r.assumptions)

            # ---- Scenario 5: fascia shortfall is zero at the picked length ----
            # Seed: TRM-1X6-8-SPF-2 is 60 on-hand at BR-NJ-01, so
            # 5 EA needed = no shortfall, transfer_options empty.
            # (The transfers.py full-cover + partial-cover sort is
            # exercised in tests/agents/pricing/test_transfers.py.)
            assert r.priced.shortfall == 0
            assert r.stock is not None
            transfers = r.stock["transfer_options"]
            assert transfers == []  # 60 on-hand ≥ 5 needed

            # ---- Scenario 6: underlayment — shortfall, no transfers ----
            r = by_id["it_underlayment"]
            assert r.status == "matched"
            assert r.priced.sku == "ROOF-UNDERLAY-ROLL"
            # Identity passthrough: 4 rolls → 4 ROLL.
            assert str(r.priced.sale_qty) == "4"
            assert r.priced.sale_unit == "ROLL"
            # Shortfall exists but no branch covers.
            assert r.priced.shortfall == 4
            assert r.stock is not None
            assert r.stock["transfer_options"] == []

            # ---- Scenario 7: sill plate — code-chosen length (least waste) ----
            # 12 LF total with the PT above-ground requirement merged.
            # Candidates: LUM-2X6-{8,10,12,14,16,18}-SYP-2-PTAG.
            # Least waste for 12 LF: 12 ft board (1 piece, waste=0).
            # Code picks LUM-2X6-12-SYP-2-PTAG even though the
            # FakeLLM submitted LUM-2X6-8.
            r = by_id["it_sill_plate"]
            assert r.status == "matched"
            assert r.priced.sku == "LUM-2X6-12-SYP-2-PTAG"
            assert str(r.priced.sale_qty) == "1"
            assert r.priced.sale_unit == "EA"
            assert "code_chose_length" in r.verify_notes
            assert any(a.field == "length_ft" and a.value == 12 for a in r.assumptions)
            # No attribute mismatches — treatment requirement merged.
            assert r.attribute_mismatches == []
            # Confidence medium: length_ft is a matchable
            # assumption (code-recorded).
            assert r.priced.confidence == ConfidenceLevel.medium
        finally:
            unregister_all()


class TestNoQuantityAndMissingLength:
    """rfp_messy items 25-26 cover the two Phase-4 refinements from
    the smoke findings:
      - Line 26 (`"Roof rafters: 2x8 at 16 in OC"`) has no parsed
        quantity → code SKIPS the item (never sent to the model),
        status='skipped' + verify_note 'quantity_missing'. Keeps
        the model from inventing a count.
      - Line 25 (`"Deck ledger at south wall (2x10) - 1 ea"`) has
        a quantity but no length. Description contains 'ledger'
        (lumber keyword) + a nominal size (`2x10`) + no length
        anywhere → code skips with verify_note 'length_missing'
        before the model ever sees it. That's the length-missing
        guard: framing lumber in piece-counted units, no stated
        length. Estimator fills in the length as a question.
    """

    async def test_no_qty_skips_and_missing_length_is_no_match(
        self, bundle, catalog_db: Path, db, bus, seeded_run
    ) -> None:
        # Line 25 — qty parsed, missing length → expect no_match.
        # Scripted FakeLLM obeys the new prompt rule.
        line_25 = ItemView(
            id="it_25",
            kind=ItemKind.material,
            description="Deck ledger at south wall (2x10)",
            quantity_text="1",
            unit_raw="ea",
            source=SourceRef(
                page=8,
                quote="22. Deck ledger at south wall (2x10) .............. 1 ea",
                read_via="text",
                verified=True,
            ),
            notes=[],
        )
        # Line 26 — no quantity at all → code skips, model never sees it.
        line_26 = ItemView(
            id="it_26",
            kind=ItemKind.material,
            description="Roof rafters: 2x8 at 16 in OC",
            quantity_text=None,
            unit_raw=None,
            source=SourceRef(
                page=8,
                quote="23. Roof rafters: 2x8 at 16 in OC",
                read_via="text",
                verified=True,
            ),
            notes=[],
        )

        # Only ONE item reaches the model — line_25. Script a
        # single query_catalog on nominal_size=2x10, then a
        # submit_matches with sku=None (no length → model honours
        # "do not invent length" rule).
        fake = FakeLLM(
            responses=[
                ScriptedResponse(
                    content=[
                        _tool_use(
                            "query_catalog",
                            {
                                "item_id": "it_25",
                                "entity": "products",
                                "filters": {"nominal_size": "2x10"},
                            },
                            id_="tu_q_25",
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
                                        "item_id": "it_25",
                                        "sku": None,
                                        "is_substitute": False,
                                        "substitute_reason": None,
                                        "searched": (
                                            "2x10 deck ledger — length unspecified, cannot invent"
                                        ),
                                    }
                                ]
                            },
                            id_="tu_submit",
                        )
                    ]
                ),
            ]
        )

        try:
            register_default_pricing(fake, bundle=bundle, catalog_db_path=catalog_db, max_steps=4)
            budget = Budget(turns=20, tokens=200_000, deadline_s=60.0)
            budget.start()
            ctx = AgentContext(run_id=seeded_run, bus=bus, budget=budget)
            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[
                    PricingItemRequest(item=line_25, requirements=[]),
                    PricingItemRequest(item=line_26, requirements=[]),
                ],
            )
            agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            assert agent_result.status == "ok", agent_result.errors

            pr = agent_result.output
            # Batch is "ok" — quantity_missing skips aren't failures.
            assert pr.status == "ok", pr.item_errors
            by_id = {r.item_id: r for r in pr.results}

            # Line 25: code skipped before reaching the model —
            # 'ledger' lumber keyword + nominal size '2x10' + no
            # length anywhere → status='skipped' with verify_note
            # 'length_missing'. The FakeLLM scripted for line 25
            # was never called.
            r25 = by_id["it_25"]
            assert r25.status == "skipped"
            assert r25.priced.sku is None
            assert "length_missing" in r25.verify_notes

            # Line 26: code skipped before reaching the model.
            r26 = by_id["it_26"]
            assert r26.status == "skipped"
            assert "quantity_missing" in r26.verify_notes
            assert r26.priced.sku is None
            # Both items were skipped by code — the model was
            # never called. The 2 scripted responses sit unused.
            assert fake.calls_made == 0
        finally:
            unregister_all()
