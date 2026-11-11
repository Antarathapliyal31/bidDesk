"""gap_rules.yaml + matcher tests.

Covers schema shape (per rule), cross-validation against the products
semantic bundle, word-boundary keyword matching, bid-scope absent_match
semantics, evaluation order (requirements → questions on the applied
item), and the shared YAML reader's failure modes (missing / bad-UTF8 /
empty / duplicate-key).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
import yaml as yaml_module

from app.catalog.semantic_layer import Entity
from app.knowledge.matcher import (
    GapRulesError,
    evaluate_item_rules,
    fires_for_bid,
    load_rules,
    matches_item,
)

_HERE = Path(__file__).resolve()
_BACKEND_ROOT = _HERE.parents[2]
_PRODUCTS_YAML = _BACKEND_ROOT / "app" / "catalog" / "semantic" / "products.yaml"
_RULES_PATH = _BACKEND_ROOT / "app" / "knowledge" / "gap_rules.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def products_entity() -> Entity:
    """Build the products Entity directly from products.yaml.

    Avoids the full bundle loader so these tests don't need a seeded
    catalog.db. Mirrors the small `key: str → [str]` shim the real loader
    applies.
    """
    raw = yaml_module.safe_load(_PRODUCTS_YAML.read_text(encoding="utf-8"))
    if isinstance(raw.get("key"), str):
        raw["key"] = [raw["key"]]
    return Entity.model_validate(raw)


@pytest.fixture(scope="module")
def rules(products_entity: Entity):
    return load_rules(products_entity=products_entity)


def _rule(rules, rule_id: str):
    for r in rules:
        if r.id == rule_id:
            return r
    raise AssertionError(f"rule {rule_id!r} not found")


def _clone_rules(tmp_path: Path, mutate: Callable[[str], str]) -> Path:
    text = _RULES_PATH.read_text(encoding="utf-8")
    new = mutate(text)
    assert new != text, "mutation was a no-op — target text not found"
    p = tmp_path / "gap_rules.yaml"
    p.write_text(new, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_loads(self, rules) -> None:
        assert len(rules) >= 12

    def test_ids_unique(self, rules) -> None:
        ids = [r.id for r in rules]
        assert len(set(ids)) == len(ids)

    def test_code_ref_discipline(self, rules) -> None:
        for r in rules:
            if r.code_ref is None:
                assert r.basis is not None and "common practice" in r.basis.lower(), r.id
            else:
                assert "(verify)" in r.code_ref, r.id

    def test_action_shape_coherence(self, rules) -> None:
        for r in rules:
            if r.action == "requirement":
                assert r.requirement is not None, r.id
            else:
                assert r.question_template and r.question_template.strip(), r.id

    def test_scope_applies_to_coherence(self, rules) -> None:
        for r in rules:
            if r.scope == "item":
                assert r.applies_to_item is not None, r.id
                assert r.applies_to_bid is None, r.id
            else:
                assert r.applies_to_bid is not None, r.id
                assert r.applies_to_item is None, r.id


# ---------------------------------------------------------------------------
# Cross-validation against the products bundle
# ---------------------------------------------------------------------------


class TestCrossValidation:
    def test_products_entity_required(self) -> None:
        # Positional-only arg; keyword-only `products_entity` is required.
        with pytest.raises(TypeError):
            load_rules()  # type: ignore[call-arg]

    def test_typo_in_category_raises(self, tmp_path: Path, products_entity: Entity) -> None:
        broken = _clone_rules(
            tmp_path,
            lambda s: s.replace("category: dimensional_lumber", "category: dimensional_lumbber", 1),
        )
        with pytest.raises(GapRulesError, match=r"dimensional_lumbber"):
            load_rules(broken, products_entity=products_entity)

    def test_typo_in_field_raises(self, tmp_path: Path, products_entity: Entity) -> None:
        broken = _clone_rules(
            tmp_path, lambda s: s.replace("field: treatment", "field: treatmnet", 1)
        )
        with pytest.raises(GapRulesError, match=r"treatmnet"):
            load_rules(broken, products_entity=products_entity)

    def test_absent_match_sale_unit_typo_raises(
        self, tmp_path: Path, products_entity: Entity
    ) -> None:
        broken = _clone_rules(
            tmp_path, lambda s: s.replace("sale_unit: ROLL", "sale_unit: NOT_A_UNIT", 1)
        )
        with pytest.raises(GapRulesError, match=r"NOT_A_UNIT"):
            load_rules(broken, products_entity=products_entity)


# ---------------------------------------------------------------------------
# Shared YAML reader coverage
# ---------------------------------------------------------------------------


class TestSharedYamlReader:
    def test_missing_file(self, tmp_path: Path, products_entity: Entity) -> None:
        with pytest.raises(GapRulesError, match=r"missing file: nope\.yaml"):
            load_rules(tmp_path / "nope.yaml", products_entity=products_entity)

    def test_non_utf8(self, tmp_path: Path, products_entity: Entity) -> None:
        p = tmp_path / "gap_rules.yaml"
        p.write_bytes(b"\xff\x80\xff garbage")
        with pytest.raises(GapRulesError, match=r"gap_rules\.yaml: not valid UTF-8"):
            load_rules(p, products_entity=products_entity)

    def test_empty_file(self, tmp_path: Path, products_entity: Entity) -> None:
        p = tmp_path / "gap_rules.yaml"
        p.write_text("", encoding="utf-8")
        with pytest.raises(GapRulesError, match=r"gap_rules\.yaml: file is empty"):
            load_rules(p, products_entity=products_entity)

    def test_duplicate_key(self, tmp_path: Path, products_entity: Entity) -> None:
        # Two top-level `rules:` keys — StrictSafeLoader catches it.
        p = tmp_path / "gap_rules.yaml"
        p.write_text("rules: []\nrules: []\n", encoding="utf-8")
        with pytest.raises(GapRulesError, match=r"duplicate key 'rules' at line \d+"):
            load_rules(p, products_entity=products_entity)


# ---------------------------------------------------------------------------
# Word-boundary keyword matching
# ---------------------------------------------------------------------------


class TestWordBoundaryMatching:
    def test_post_does_not_match_postpone(self, rules) -> None:
        rule = _rule(rules, "pt_posts_in_ground")
        item = {
            "category": "dimensional_lumber",
            "nominal_size": "4x4",
            "treatment": None,
            "source_quote": "postpone the delivery",
        }
        assert not matches_item(rule, item)

    def test_post_does_not_match_composite(self, rules) -> None:
        rule = _rule(rules, "pt_posts_in_ground")
        item = {
            "category": "dimensional_lumber",
            "nominal_size": "4x4",
            "treatment": None,
            "source_quote": "composite decking",
        }
        assert not matches_item(rule, item)

    def test_ridge_does_not_match_fridge(self, rules) -> None:
        rule = _rule(rules, "hurricane_ties_for_roof_framing")
        bid = [
            {
                "category": "dimensional_lumber",
                "treatment": "none",
                "context_text": "near the fridge",
            }
        ]
        assert not fires_for_bid(rule, bid)

    def test_multi_word_keyword_double_space(self, rules) -> None:
        rule = _rule(rules, "pt_sill_plates_on_concrete")
        item = {
            "category": "dimensional_lumber",
            "treatment": None,
            "source_quote": "2x6 sill  plates on concrete slab",  # double space
        }
        assert matches_item(rule, item)

    def test_multi_word_keyword_newline(self, rules) -> None:
        rule = _rule(rules, "pt_sill_plates_on_concrete")
        item = {
            "category": "dimensional_lumber",
            "treatment": None,
            "source_quote": "2x6 sill\nplates on concrete slab",  # newline
        }
        assert matches_item(rule, item)


# ---------------------------------------------------------------------------
# context_all_of AND-of-OR semantics
# ---------------------------------------------------------------------------


class TestContextAllOf:
    def test_pt_sill_fires_with_both_groups(self, rules) -> None:
        rule = _rule(rules, "pt_sill_plates_on_concrete")
        item = {
            "category": "dimensional_lumber",
            "treatment": None,
            "source_quote": "2x6 sill plates on concrete slab",
        }
        assert matches_item(rule, item)

    def test_pt_sill_silent_with_only_plate_group(self, rules) -> None:
        # "top plates" matches group 1 (plate terms) but there's no
        # concrete/slab/masonry in the quote → group 2 misses → silent.
        rule = _rule(rules, "pt_sill_plates_on_concrete")
        item = {
            "category": "dimensional_lumber",
            "treatment": None,
            "source_quote": "2x6 top plates",
        }
        assert not matches_item(rule, item)

    def test_pt_sill_silent_with_only_concrete_group(self, rules) -> None:
        rule = _rule(rules, "pt_sill_plates_on_concrete")
        item = {
            "category": "dimensional_lumber",
            "treatment": None,
            "source_quote": "2x6 for concrete forms",
        }
        assert not matches_item(rule, item)


# ---------------------------------------------------------------------------
# Evaluation order: requirement → applied item → question fires
# ---------------------------------------------------------------------------


class TestEvaluationOrder:
    def test_pt_plates_on_concrete_fires_both(self, rules) -> None:
        item = {
            "category": "dimensional_lumber",
            "nominal_size": "2x6",
            "treatment": None,
            "source_quote": "2x6 plates on concrete slab",
        }
        reqs, questions = evaluate_item_rules(rules, item)
        req_ids = {r.id for r in reqs}
        q_ids = {r.id for r in questions}
        assert "pt_sill_plates_on_concrete" in req_ids
        assert "pt_variant_above_vs_ground_contact" in q_ids

    def test_emitted_requirement_keeps_full_value_in(self, rules) -> None:
        # The first-value choice is strictly for evaluation; the EMITTED
        # requirement object retains both options so the orchestrator /
        # estimator can still decide.
        item = {
            "category": "dimensional_lumber",
            "nominal_size": "2x6",
            "treatment": None,
            "source_quote": "2x6 sill plates on concrete slab",
        }
        reqs, _ = evaluate_item_rules(rules, item)
        pt_req = next(r for r in reqs if r.id == "pt_sill_plates_on_concrete")
        assert pt_req.requirement is not None
        assert pt_req.requirement.value_in == [
            "pressure_treated_above_ground",
            "pressure_treated_ground_contact",
        ]
        assert pt_req.requirement.value is None


# ---------------------------------------------------------------------------
# Per-rule fires-on-match / silent-on-near-miss
# ---------------------------------------------------------------------------


class TestItemRuleBehaviour:
    def test_wet_area_drywall_fires(self, rules) -> None:
        rule = _rule(rules, "wet_area_drywall_mr")
        item = {
            "category": "drywall",
            "moisture_resistant": 0,
            "source_quote": "1/2 drywall for upstairs bathroom",
        }
        assert matches_item(rule, item)

    def test_wet_area_drywall_silent_when_mr(self, rules) -> None:
        rule = _rule(rules, "wet_area_drywall_mr")
        item = {
            "category": "drywall",
            "moisture_resistant": 1,
            "source_quote": "1/2 MR drywall for upstairs bathroom",
        }
        assert not matches_item(rule, item)

    def test_wet_area_drywall_silent_without_keywords(self, rules) -> None:
        rule = _rule(rules, "wet_area_drywall_mr")
        item = {
            "category": "drywall",
            "moisture_resistant": 0,
            "source_quote": "1/2 drywall for living room",
        }
        assert not matches_item(rule, item)

    def test_garage_type_x_fires(self, rules) -> None:
        rule = _rule(rules, "garage_ceiling_type_x")
        item = {
            "category": "drywall",
            "fire_rating": "none",
            "source_quote": "5/8 drywall at garage ceiling",
        }
        assert matches_item(rule, item)

    def test_garage_type_x_silent_when_already_type_x(self, rules) -> None:
        rule = _rule(rules, "garage_ceiling_type_x")
        item = {
            "category": "drywall",
            "fire_rating": "type_x",
            "source_quote": "5/8 Type X at garage ceiling",
        }
        assert not matches_item(rule, item)

    def test_drywall_type_missing_fires(self, rules) -> None:
        # triggered_when: fire_rating=none AND moisture_resistant=0
        rule = _rule(rules, "drywall_type_missing")
        item = {
            "category": "drywall",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "source_quote": "1/2 drywall",
        }
        assert matches_item(rule, item)

    def test_drywall_type_missing_silent_when_mr(self, rules) -> None:
        rule = _rule(rules, "drywall_type_missing")
        item = {
            "category": "drywall",
            "fire_rating": "none",
            "moisture_resistant": 1,
            "source_quote": "MR drywall",
        }
        assert not matches_item(rule, item)

    def test_drywall_type_missing_silent_when_type_x(self, rules) -> None:
        rule = _rule(rules, "drywall_type_missing")
        item = {
            "category": "drywall",
            "fire_rating": "type_x",
            "moisture_resistant": 0,
            "source_quote": "Type X drywall",
        }
        assert not matches_item(rule, item)

    def test_sheathing_thickness_missing_fires(self, rules) -> None:
        rule = _rule(rules, "sheathing_thickness_missing")
        item = {"category": "sheathing", "thickness_in": None, "source_quote": "OSB"}
        assert matches_item(rule, item)

    def test_sheathing_thickness_missing_silent(self, rules) -> None:
        rule = _rule(rules, "sheathing_thickness_missing")
        item = {"category": "sheathing", "thickness_in": "1/2", "source_quote": "OSB 1/2"}
        assert not matches_item(rule, item)

    def test_insulation_r_value_missing_fires(self, rules) -> None:
        rule = _rule(rules, "insulation_r_value_missing")
        item = {"category": "insulation", "r_value": None, "source_quote": "batt insulation"}
        assert matches_item(rule, item)

    def test_insulation_r_value_missing_silent(self, rules) -> None:
        rule = _rule(rules, "insulation_r_value_missing")
        item = {"category": "insulation", "r_value": 19, "source_quote": "R-19 batts"}
        assert not matches_item(rule, item)

    def test_roofing_type_missing_fires_on_bdl(self, rules) -> None:
        rule = _rule(rules, "roofing_type_missing")
        item = {
            "category": "roofing",
            "sale_unit": "BDL",
            "source_quote": "asphalt shingle bundles",
        }
        assert matches_item(rule, item)

    def test_roofing_type_missing_silent_on_roll(self, rules) -> None:
        # triggered_when: sale_unit=BDL; underlayment ROLL doesn't trigger.
        rule = _rule(rules, "roofing_type_missing")
        item = {"category": "roofing", "sale_unit": "ROLL", "source_quote": "underlayment"}
        assert not matches_item(rule, item)


# ---------------------------------------------------------------------------
# Bid-scope absent_match semantics
# ---------------------------------------------------------------------------


class TestBidScope:
    def test_underlayment_fires_when_only_shingles(self, rules) -> None:
        rule = _rule(rules, "underlayment_missing_for_shingles")
        bid = [{"category": "roofing", "sale_unit": "BDL"}]
        assert fires_for_bid(rule, bid)

    def test_underlayment_silent_when_roll_present(self, rules) -> None:
        rule = _rule(rules, "underlayment_missing_for_shingles")
        bid = [
            {"category": "roofing", "sale_unit": "BDL"},
            {"category": "roofing", "sale_unit": "ROLL"},
        ]
        assert not fires_for_bid(rule, bid)

    def test_hurricane_ties_fires_with_rafter(self, rules) -> None:
        rule = _rule(rules, "hurricane_ties_for_roof_framing")
        bid = [
            {"category": "dimensional_lumber", "context_text": "roof rafter 2x8 framing"},
        ]
        assert fires_for_bid(rule, bid)

    def test_hurricane_ties_silent_when_hurricane_tie_present(self, rules) -> None:
        rule = _rule(rules, "hurricane_ties_for_roof_framing")
        bid = [
            {"category": "dimensional_lumber", "context_text": "roof rafter 2x8"},
            {"category": "connectors", "name": "Hurricane tie H2.5A"},
        ]
        assert not fires_for_bid(rule, bid)

    def test_hurricane_ties_not_silenced_by_joist_hangers(self, rules) -> None:
        # joist hangers are connectors but name doesn't contain "hurricane"
        # → absent_match stays unsatisfied → rule fires.
        rule = _rule(rules, "hurricane_ties_for_roof_framing")
        bid = [
            {"category": "dimensional_lumber", "context_text": "roof rafter 2x8"},
            {"category": "connectors", "name": "Joist hanger for 2x8"},
        ]
        assert fires_for_bid(rule, bid)

    def test_housewrap_fires_when_wall_sheathing(self, rules) -> None:
        rule = _rule(rules, "housewrap_missing_for_wall_sheathing")
        bid = [{"category": "sheathing", "context_text": "exterior wall sheathing OSB"}]
        assert fires_for_bid(rule, bid)

    def test_housewrap_silent_when_wrap_present(self, rules) -> None:
        rule = _rule(rules, "housewrap_missing_for_wall_sheathing")
        bid = [
            {"category": "sheathing", "context_text": "exterior wall sheathing"},
            {"category": "housewrap"},
        ]
        assert not fires_for_bid(rule, bid)

    def test_pt_fasteners_fires_on_pt_present(self, rules) -> None:
        rule = _rule(rules, "pt_fasteners_for_pt_bid")
        bid = [{"category": "dimensional_lumber", "treatment": "pressure_treated_above_ground"}]
        assert fires_for_bid(rule, bid)

    def test_pt_fasteners_still_fires_even_when_fasteners_present(self, rules) -> None:
        # No absent_match on this rule → fasteners don't silence it.
        rule = _rule(rules, "pt_fasteners_for_pt_bid")
        bid = [
            {"category": "dimensional_lumber", "treatment": "pressure_treated_ground_contact"},
            {"category": "fasteners", "sale_unit": "BOX"},
        ]
        assert fires_for_bid(rule, bid)

    def test_pt_fasteners_silent_when_no_pt(self, rules) -> None:
        rule = _rule(rules, "pt_fasteners_for_pt_bid")
        bid = [{"category": "dimensional_lumber", "treatment": "none"}]
        assert not fires_for_bid(rule, bid)

    def test_deck_ledger_flashing_fires_once_per_bid(self, rules) -> None:
        rule = _rule(rules, "deck_ledger_flashing")
        bid = [
            {"category": "dimensional_lumber", "source_quote": "2x10 deck ledger at south wall"},
            {"category": "dimensional_lumber", "source_quote": "2x10 deck ledger at east wall"},
            {"category": "dimensional_lumber", "source_quote": "2x10 deck ledger at north wall"},
        ]
        # Bid-scope rules fire at most once regardless of how many items
        # trigger. Just one True from fires_for_bid.
        assert fires_for_bid(rule, bid)


# ---------------------------------------------------------------------------
# is_null operator — supports both true (field null) and false (field populated)
# ---------------------------------------------------------------------------


class TestIsNullOperator:
    def test_is_null_true_fires_when_field_missing(self, rules) -> None:
        rule = _rule(rules, "sheathing_thickness_missing")
        item = {"category": "sheathing", "thickness_in": None}
        assert matches_item(rule, item)

    def test_is_null_true_silent_when_field_populated(self, rules) -> None:
        rule = _rule(rules, "sheathing_thickness_missing")
        item = {"category": "sheathing", "thickness_in": "1/2"}
        assert not matches_item(rule, item)
