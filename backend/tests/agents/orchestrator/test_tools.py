"""Orchestrator tool-handler tests.

Each handler is exercised against a seeded run + items. The
scope is: valid inputs persist, invalid inputs feed
`rejected_claims_buffer`, and the code-side `apply_rule_requirements`
fires the right gap rules without the model's participation."""

from __future__ import annotations

import pytest

from app.agents.orchestrator.tools import (
    apply_rule_requirements,
    handle_add_requirements,
    handle_check_gap_rules,
    handle_classify_items,
    handle_flag_conflict,
)
from app.contracts.common import SourceRef
from app.contracts.orchestrator import (
    AddRequirementEntry,
    AddRequirementsInput,
    CheckGapRulesInput,
    ClassifyItemsInput,
    ConflictSideInput,
    FlagConflictInput,
    TriageEntry,
)
from app.db import repo


class TestClassifyItems:
    async def test_persists_valid_entries_rejects_unknown_ids(self, orch_ctx) -> None:
        payload = ClassifyItemsInput(
            entries=[
                TriageEntry(item_id="it_sill", category="price", reason="lumber"),
                TriageEntry(item_id="it_post", category="price", reason="lumber"),
                TriageEntry(item_id="nope", category="price", reason="bad id"),
            ]
        )
        result = await handle_classify_items(payload, ctx=orch_ctx)
        assert result["ok"] == 2
        assert result["rejected"] == 1
        rows = await repo.list_item_triage(orch_ctx.db, run_id=orch_ctx.run_id)
        assert {r["item_id"] for r in rows} == {"it_sill", "it_post"}
        assert any(
            rc["kind"] == "classify_items" and rc["reason"] == "unknown_item_id"
            for rc in orch_ctx.rejected_claims_buffer
        )


class TestApplyRuleRequirements:
    async def test_code_applies_sill_plate_rule_without_model(self, orch_ctx) -> None:
        """Antara's refinement: even if the model never calls
        add_requirements, the sill-plate PT requirement is still
        applied by code before pricing."""

        # Triage so the matcher has `category=price` for both items.
        await handle_classify_items(
            ClassifyItemsInput(
                entries=[
                    TriageEntry(item_id="it_sill", category="price", reason="x"),
                    TriageEntry(item_id="it_post", category="price", reason="x"),
                ]
            ),
            ctx=orch_ctx,
        )
        result = await apply_rule_requirements(orch_ctx)
        assert result["ok"] >= 2
        # Rule ids that fired include the sill-plate one.
        assert "pt_sill_plates_on_concrete" in orch_ctx.fired_requirement_rule_ids
        # Persisted into item_requirements with created_by='orchestrator'.
        rows = await repo.list_item_requirements(orch_ctx.db, run_id=orch_ctx.run_id)
        sill_rows = [r for r in rows if r["item_id"] == "it_sill"]
        assert sill_rows, "sill-plate requirement should land in item_requirements"
        assert all(r["created_by"] == "orchestrator" for r in sill_rows)
        assert any(r["field"] == "treatment" for r in sill_rows)


class TestCheckGapRules:
    async def test_reports_question_rules_and_tracks_ids(self, orch_ctx) -> None:
        await handle_classify_items(
            ClassifyItemsInput(
                entries=[
                    TriageEntry(item_id="it_sill", category="price", reason="x"),
                    TriageEntry(item_id="it_post", category="price", reason="x"),
                ]
            ),
            ctx=orch_ctx,
        )
        res = await handle_check_gap_rules(CheckGapRulesInput(item_ids=[]), ctx=orch_ctx)
        assert res["ok"] is True
        # Question rules fired for both items (PT variant on sill;
        # fasteners/hurricane/etc. bid-scope rules land in bid_rules).
        assert "pt_variant_above_vs_ground_contact" in orch_ctx.fired_question_rule_ids


class TestAddRequirementsValidation:
    async def test_unknown_field_rejected(self, orch_ctx) -> None:
        payload = AddRequirementsInput(
            entries=[
                AddRequirementEntry(
                    item_id="it_sill",
                    field="not_a_field",
                    value="x",
                    reason="bad",
                )
            ]
        )
        result = await handle_add_requirements(payload, ctx=orch_ctx)
        assert result["ok"] == 0
        assert result["rejected"] == 1
        assert any(
            rc["kind"] == "add_requirements" and rc["reason"] == "unknown_field"
            for rc in orch_ctx.rejected_claims_buffer
        )

    async def test_non_matchable_field_rejected(self, orch_ctx) -> None:
        # `name` is searchable but not matchable.
        payload = AddRequirementsInput(
            entries=[
                AddRequirementEntry(
                    item_id="it_sill",
                    field="name",
                    value="something",
                    reason="bad",
                )
            ]
        )
        result = await handle_add_requirements(payload, ctx=orch_ctx)
        assert result["ok"] == 0
        assert any(rc["reason"] == "field_not_matchable" for rc in orch_ctx.rejected_claims_buffer)

    async def test_value_not_in_enum_rejected(self, orch_ctx) -> None:
        payload = AddRequirementsInput(
            entries=[
                AddRequirementEntry(
                    item_id="it_sill",
                    field="treatment",
                    value="not_a_treatment",
                    reason="bad",
                )
            ]
        )
        result = await handle_add_requirements(payload, ctx=orch_ctx)
        assert result["ok"] == 0
        assert any("value_not_allowed" in rc["reason"] for rc in orch_ctx.rejected_claims_buffer)

    async def test_valid_requirement_persists(self, orch_ctx) -> None:
        payload = AddRequirementsInput(
            entries=[
                AddRequirementEntry(
                    item_id="it_post",
                    field="treatment",
                    value="pressure_treated_ground_contact",
                    reason="post in ground",
                    source_page=2,
                    source_quote="4x4 x 8' SPF #2 (ground contact)",
                )
            ]
        )
        result = await handle_add_requirements(payload, ctx=orch_ctx)
        assert result["ok"] == 1
        rows = await repo.list_item_requirements(orch_ctx.db, run_id=orch_ctx.run_id)
        assert any(r["item_id"] == "it_post" and r["field"] == "treatment" for r in rows)


class TestFlagConflict:
    async def test_both_text_verified_persists(self, orch_ctx) -> None:
        payload = FlagConflictInput(
            type="size_mismatch",
            side_a=ConflictSideInput(
                item_id=None,
                source=SourceRef(page=3, quote="7/16 OSB", read_via="text", verified=True),
            ),
            side_b=ConflictSideInput(
                item_id=None,
                source=SourceRef(page=8, quote="1/2 OSB", read_via="text", verified=True),
            ),
            explanation="Thickness mismatch between schedule and spec.",
        )
        res = await handle_flag_conflict(payload, ctx=orch_ctx)
        assert res["ok"] is True
        assert res.get("conflict_id") is not None

    async def test_vision_side_routes_to_question(self, orch_ctx) -> None:
        payload = FlagConflictInput(
            type="excluded_but_listed",
            side_a=ConflictSideInput(
                item_id=None,
                source=SourceRef(page=5, quote="excl", read_via="text", verified=True),
            ),
            side_b=ConflictSideInput(
                item_id=None,
                source=SourceRef(page=7, quote=None, read_via="vision", verified=False),
            ),
            explanation="vision page can't carry a byte-verified quote",
        )
        res = await handle_flag_conflict(payload, ctx=orch_ctx)
        assert res["ok"] is True
        assert res["routed"] == "contractor_question"
        assert res["question_ref_id"] in orch_ctx.fired_question_rule_ids


_ = pytest


# ---------------------------------------------------------------------------
# DECISIONS F16: R-value inference + material-only rule scope
# ---------------------------------------------------------------------------


class TestRValueInferenceAndMaterialOnly:
    """Regressions from `run_9534f056`: `insulation_r_value_missing`
    fired even though items said "R-19" / "R-30" / "R-13" — root
    cause was an exclusion line ("Insulation by others") with no
    R-value triggering the item-scope rule. Fix: product-category
    item rules only fire for `kind=="material"`.
    """

    async def test_r_value_inferred_from_description(self) -> None:
        from app.agents.orchestrator.tools import _item_dict_for_matcher

        for desc, expected in [
            ("R-19 insulation", 19),
            ("R-30 insulation for attic", 30),
            ("R-13 insulation for 2x4 walls", 13),
            ("R19 batt", 19),
            ("R 30 batt in attic", 30),
        ]:
            view = _item_dict_for_matcher(
                {"description": desc, "source_quote": desc, "specs": {}}, None
            )
            assert view["category"] == "insulation", desc
            assert view["r_value"] == expected, desc

    async def test_r_value_rule_does_not_fire_when_text_has_r_value(
        self, orch_ctx, bundle, catalog_db
    ) -> None:
        from app.contracts import ItemKind
        from app.db import repo as _repo
        from app.agents.orchestrator.tools import apply_rule_questions

        # Insert a real insulation material line with R-19 in text.
        await _repo.insert_item(
            orch_ctx.db,
            run_id=orch_ctx.run_id,
            item_id="it_r19",
            kind=ItemKind.material,
            description="R-19 insulation",
            quantity_text="1200",
            unit_raw="sq ft",
            source_page=5,
            source_quote="14. R-19 insulation ............................... 1200 sq ft",
            read_via="text",
            verified=True,
            verification_notes=[],
            created_at="2026-11-13T00:00:00+00:00",
        )
        orch_ctx.items_by_id["it_r19"] = {
            "id": "it_r19",
            "kind": "material",
            "description": "R-19 insulation",
            "source_quote": "14. R-19 insulation ............................... 1200 sq ft",
            "specs": {},
        }
        qs = await apply_rule_questions(orch_ctx)
        assert not any(q["basis"]["ref_id"] == "insulation_r_value_missing" for q in qs)
        assert "insulation_r_value_missing" not in orch_ctx.fired_question_rule_ids

    async def test_exclusion_line_does_not_fire_product_item_rules(
        self, orch_ctx
    ) -> None:
        from app.agents.orchestrator.tools import apply_rule_questions

        # Add the exact exclusion that triggered the false fire in
        # `run_9534f056`. category=insulation inferred from wording,
        # r_value=None, but kind=exclusion — rule MUST NOT fire.
        orch_ctx.items_by_id["it_excl"] = {
            "id": "it_excl",
            "kind": "exclusion",
            "description": "Insulation by others",
            "source_quote": "- Insulation by others.",
            "specs": {},
        }
        qs = await apply_rule_questions(orch_ctx)
        assert not any(q["basis"]["ref_id"] == "insulation_r_value_missing" for q in qs)
        assert "insulation_r_value_missing" not in orch_ctx.fired_question_rule_ids


# ---------------------------------------------------------------------------
# DECISIONS F17: self-conflict guard
# ---------------------------------------------------------------------------


class TestFlagConflictSelfConflict:
    async def test_same_page_and_same_quote_routes_to_question(
        self, orch_ctx
    ) -> None:
        from app.contracts.common import SourceRef as _SourceRef

        src = _SourceRef(
            page=2,
            quote="4x4 x 8' SPF #2 (ground contact)",
            read_via="text",
            verified=True,
        )
        payload = FlagConflictInput(
            type="spec_vs_list",
            side_a=ConflictSideInput(item_id="it_post", source=src),
            side_b=ConflictSideInput(item_id="it_post", source=src),
            explanation="model submitted a line against itself",
        )
        res = await handle_flag_conflict(payload, ctx=orch_ctx)
        assert res["ok"] is True
        assert res["routed"] == "contractor_question"
        assert res.get("reason") == "self_conflict"
        assert res["question_ref_id"] in orch_ctx.fired_question_rule_ids
        # NO persisted conflict row.
        rows = await repo.list_conflicts(orch_ctx.db, run_id=orch_ctx.run_id)
        assert not any(
            (r.get("side_a") or {}).get("item_id") == "it_post" for r in rows
        ), rows

    async def test_same_page_distinct_quote_still_persists(self, orch_ctx) -> None:
        from app.contracts.common import SourceRef as _SourceRef

        payload = FlagConflictInput(
            type="spec_vs_list",
            side_a=ConflictSideInput(
                item_id="it_sill",
                source=_SourceRef(
                    page=2,
                    quote="2x6 PT for sill plates on the concrete slab",
                    read_via="text",
                    verified=True,
                ),
            ),
            side_b=ConflictSideInput(
                item_id="it_post",
                source=_SourceRef(
                    page=2,
                    quote="4x4 x 8' SPF #2 (ground contact)",
                    read_via="text",
                    verified=True,
                ),
            ),
            explanation="two different lines on the same page",
        )
        res = await handle_flag_conflict(payload, ctx=orch_ctx)
        assert res["ok"] is True
        assert "conflict_id" in res
