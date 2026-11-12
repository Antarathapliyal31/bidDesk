"""Spec inference tests — the regexes that feed gap-rule
`applies_to` / `triggered_when` clauses when the analyst hasn't
tagged specs yet. Guardrails against drift that would cause
real-API runs to silently miss the rules the expected JSON
pins."""

from __future__ import annotations

from typing import Any

from app.agents.orchestrator.tools import (
    _infer_specs,
    _item_dict_for_matcher,
)


def _item(**over: Any) -> dict[str, Any]:
    base = {
        "id": "item_1",
        "kind": "material",
        "description": "",
        "source_quote": "",
        "specs": {},
    }
    base.update(over)
    return base


class TestCategory:
    def test_insulation_from_description(self) -> None:
        r = _infer_specs(_item(description="R-19 insulation"))
        assert r["category"] == "insulation"

    def test_dimensional_lumber_from_nominal_size(self) -> None:
        r = _infer_specs(_item(description="2x6 x 16' SPF"))
        assert r["category"] == "dimensional_lumber"

    def test_drywall_from_keyword(self) -> None:
        r = _infer_specs(_item(description="5/8 Type X drywall"))
        assert r["category"] == "drywall"


class TestRValue:
    """Rfp_messy had `R-19 insulation`, `R-30 insulation for attic`,
    `R-13 insulation for 2x4 walls`. The inference must pull the
    number out, otherwise `insulation_r_value_missing` fires even
    though the R-value is RIGHT THERE."""

    def test_r_19(self) -> None:
        assert _infer_specs(_item(description="R-19 insulation"))["r_value"] == 19

    def test_r_30_with_trailing_text(self) -> None:
        assert _infer_specs(_item(description="R-30 insulation for attic"))["r_value"] == 30

    def test_r_13_with_context(self) -> None:
        assert _infer_specs(_item(description="R-13 insulation for 2x4 walls"))["r_value"] == 13

    def test_source_quote_dot_leader(self) -> None:
        """Real PDF line leaders use dots. Make sure they don't
        break the match."""

        quote = "14. R-19 insulation ............................... 1200 sq ft"
        r = _infer_specs(_item(description="insulation", source_quote=quote))
        assert r["r_value"] == 19


class TestNominalSize:
    def test_2x10(self) -> None:
        assert _infer_specs(_item(description="2x10 x 17' SPF"))["nominal_size"] == "2x10"

    def test_4x4_ground_contact(self) -> None:
        # The rfp_messy item that previously missed pt_posts_in_ground.
        r = _infer_specs(_item(description="4x4 x 8' SPF #2 (ground contact)"))
        assert r["nominal_size"] == "4x4"
        assert r["length_ft"] == 8


class TestFireRating:
    def test_type_x_detected(self) -> None:
        assert _infer_specs(_item(description="5/8 Type X drywall"))["fire_rating"] == "type_x"

    def test_regular_drywall_not_type_x(self) -> None:
        assert _infer_specs(_item(description="1/2 drywall for bedrooms"))["fire_rating"] is None


class TestItemViewComposes:
    """`_item_dict_for_matcher` layers inferred specs over the
    item's `specs` dict. Smoke-test the shape the matcher sees."""

    def test_insulation_r_value_shows_up(self) -> None:
        view = _item_dict_for_matcher(
            _item(description="R-19 insulation", source_quote="14. R-19 insulation ... 1200 sq ft"),
            None,
        )
        # `insulation_r_value_missing` rule's `missing_condition`
        # is `r_value is_null`; a populated r_value here prevents
        # the rule from firing.
        assert view["category"] == "insulation"
        assert view["r_value"] == 19

    def test_explicit_specs_win_over_inference(self) -> None:
        item = _item(description="R-19 insulation", specs={"r_value": 25})
        view = _item_dict_for_matcher(item, None)
        assert view["r_value"] == 25
