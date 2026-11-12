"""`bid/assemble.py` tests — section routing, review reasons,
link validation, summary rules, fallback path.

Fixtures are built as plain dicts matching `repo.list_*` shapes
so the tests don't touch a real DB."""

from __future__ import annotations

from typing import Any

import pytest

from app.bid.assemble import (
    AssemblyInput,
    SummaryValidationError,
    build_bid,
    validate_summary,
)
from app.contracts.bid import BranchInfo, ProjectInfo
from app.contracts.common import GeneratedBy, ReviewStateT


def _branch(ppm: int = 70_000) -> BranchInfo:
    return BranchInfo(id="BR-NJ-01", name="NJ Branch", tax_rate_ppm=ppm)


def _project() -> ProjectInfo:
    return ProjectInfo(name="Test project", due_date_text=None)


def _item(
    iid: str,
    *,
    kind: str = "material",
    description: str = "2x4 x 8 SPF",
    quantity_text: str = "50",
    unit_raw: str = "pcs",
    page: int = 1,
    quote: str = "test quote",
    verified: bool = True,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": iid,
        "kind": kind,
        "description": description,
        "quantity_text": quantity_text,
        "unit_raw": unit_raw,
        "source_page": page,
        "source_quote": quote,
        "read_via": "text",
        "verified": verified,
        "notes": notes or [],
        "specs": {},
    }


def _price_row(
    *,
    status: str = "matched",
    sku: str = "LUM-2X4-8-SPF-2",
    sale_qty: str = "50",
    sale_unit: str = "EA",
    unit_price_cents: int = 400,
    confidence: str = "high",
    verify_notes: list[str] | None = None,
    shortfall: int | None = 0,
    is_substitute: bool = False,
) -> dict[str, Any]:
    return {
        "status": status,
        "sku": sku,
        "product_name": "SPF 2x4x8",
        "sale_unit": sale_unit,
        "sale_qty": sale_qty,
        "conversion_path": None,
        "unit_price_cents": unit_price_cents,
        "branch_on_hand": 100,
        "shortfall": shortfall,
        "transfer_options": [],
        "assumptions": [],
        "attribute_mismatches": [],
        "verify_notes": verify_notes or [],
        "is_substitute": is_substitute,
        "substitute_reason": None,
        "confidence": confidence,
        "confidence_reasons": [],
    }


def _input(
    *,
    items: list[dict[str, Any]],
    triage: dict[str, str] | None = None,
    requirements: dict[str, list[dict[str, Any]]] | None = None,
    prices: dict[str, dict[str, Any]] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
    summary: str = "Test bid summary covers extracted material only.",
    assumptions: list[dict[str, Any]] | None = None,
    contractor_questions: list[dict[str, Any]] | None = None,
    generated_by: GeneratedBy = GeneratedBy.orchestrator,
    partial_reasons: list[str] | None = None,
    gap_rule_ids: set[str] | None = None,
) -> AssemblyInput:
    triage_by_item = {
        iid: {
            "item_id": iid,
            "category": cat,
            "reason": "test",
            "created_at": "2026-11-12T00:00:00Z",
        }
        for iid, cat in (triage or {}).items()
    }
    return AssemblyInput(
        run_id="run_test",
        branch=_branch(),
        project=_project(),
        items=items,
        triage_by_item=triage_by_item,
        requirements_by_item=requirements or {},
        price_results_by_item=prices or {},
        conflicts=conflicts or [],
        summary=summary,
        assumptions=assumptions or [],
        contractor_questions=contractor_questions or [],
        generated_by=generated_by,
        partial_reasons=partial_reasons or [],
        gap_rule_ids=gap_rule_ids or set(),
    )


# ---------------------------------------------------------------------------
# Summary validation
# ---------------------------------------------------------------------------


class TestSummaryValidation:
    def test_empty_rejected(self) -> None:
        with pytest.raises(SummaryValidationError, match="empty"):
            validate_summary("")

    def test_overlong_rejected(self) -> None:
        with pytest.raises(SummaryValidationError, match="600"):
            validate_summary("a" * 601)

    def test_currency_rejected(self) -> None:
        with pytest.raises(SummaryValidationError, match="dollar amount"):
            validate_summary("Materials total $1200 before tax.")

    @pytest.mark.parametrize(
        "bad",
        [
            "We guarantee delivery.",
            "Comes with a warranty.",
            "Firm price for 30 days.",
            "In stock at the branch.",
            "Material will be delivered next Tuesday.",
            "I promise this works.",
        ],
    )
    def test_commitment_words_rejected(self, bad: str) -> None:
        with pytest.raises(SummaryValidationError, match="forbidden commitment"):
            validate_summary(bad)

    def test_clean_summary_passes(self) -> None:
        validate_summary(
            "15 material lines extracted; 2 exclusions. Several PT "
            "variant assumptions recorded for review."
        )


# ---------------------------------------------------------------------------
# Section routing + review flags
# ---------------------------------------------------------------------------


class TestSectionRouting:
    def test_price_goes_to_lines(self) -> None:
        items = [_item("it_1")]
        inp = _input(
            items=items,
            triage={"it_1": "price"},
            prices={"it_1": _price_row()},
        )
        result = build_bid(inp)
        assert result.outcome == "bid_ready"
        assert result.agent_bid is not None
        assert len(result.agent_bid.lines) == 1
        assert result.agent_bid.lines[0].review.state == ReviewStateT.none

    def test_exclusion_routes_to_exclusions(self) -> None:
        items = [_item("it_x", kind="exclusion", description="Excludes gutters.")]
        inp = _input(items=items, triage={"it_x": "exclusion"})
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert len(result.agent_bid.exclusions) == 1
        assert result.agent_bid.exclusions[0].description == "Excludes gutters."

    def test_labor_routes_to_labor(self) -> None:
        items = [_item("it_l", kind="labor", description="Framing crew")]
        inp = _input(items=items, triage={"it_l": "labor"})
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert len(result.agent_bid.labor) == 1

    def test_unreadable_routes_to_unreadable(self) -> None:
        items = [_item("it_u", kind="unreadable", verified=False, quote="")]
        inp = _input(items=items)
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert len(result.agent_bid.unreadable) == 1

    def test_ambiguous_becomes_line_with_open_review(self) -> None:
        """Items with no triage (or 'ambiguous') land in lines
        with an open review flag + 'not_priced' reason."""

        items = [_item("it_a")]
        inp = _input(items=items)  # no triage
        result = build_bid(inp)
        assert result.agent_bid is not None
        line = result.agent_bid.lines[0]
        assert line.review.state == ReviewStateT.open
        assert "not_priced" in line.review.reasons


class TestReviewReasons:
    def test_needs_review_adds_not_priced(self) -> None:
        items = [_item("it_1")]
        price = _price_row(status="needs_review", sku=None, sale_qty=None, unit_price_cents=None)
        inp = _input(items=items, triage={"it_1": "price"}, prices={"it_1": price})
        result = build_bid(inp)
        line = result.agent_bid.lines[0]  # type: ignore[union-attr]
        assert "not_priced" in line.review.reasons
        assert line.review.state == ReviewStateT.open

    def test_substitute_and_shortfall(self) -> None:
        items = [_item("it_1")]
        price = _price_row(status="substitute", is_substitute=True, shortfall=5)
        inp = _input(items=items, triage={"it_1": "price"}, prices={"it_1": price})
        result = build_bid(inp)
        line = result.agent_bid.lines[0]  # type: ignore[union-attr]
        reasons = set(line.review.reasons)
        assert "substitute" in reasons
        assert "shortfall" in reasons
        assert line.review.state == ReviewStateT.open

    def test_in_conflict_flag(self) -> None:
        items = [_item("it_1"), _item("it_2", description="2x6")]
        conflicts = [
            {
                "id": "conflict_1",
                "type": "size_mismatch",
                "side_a": {
                    "item_id": "it_1",
                    "source": {"page": 1, "quote": "a", "read_via": "text", "verified": True},
                },
                "side_b": {
                    "item_id": "it_2",
                    "source": {"page": 2, "quote": "b", "read_via": "text", "verified": True},
                },
                "explanation": "mismatch",
                "created_at": "2026-11-12T00:00:00Z",
            }
        ]
        inp = _input(
            items=items,
            triage={"it_1": "price", "it_2": "price"},
            prices={"it_1": _price_row(), "it_2": _price_row(sku="LUM-2X6-8-SPF-2")},
            conflicts=conflicts,
        )
        result = build_bid(inp)
        assert result.agent_bid is not None
        for line in result.agent_bid.lines:
            assert "in_conflict" in line.review.reasons


# ---------------------------------------------------------------------------
# Link validation (assumptions + contractor_questions)
# ---------------------------------------------------------------------------


class TestLinkValidation:
    def test_assumption_without_link_dropped(self) -> None:
        items = [_item("it_1")]
        inp = _input(
            items=items,
            triage={"it_1": "price"},
            prices={"it_1": _price_row()},  # no recorded assumptions
            assumptions=[{"item_id": "it_1", "text": "assumed above-ground PT"}],
        )
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert result.agent_bid.assumptions == []
        assert any(
            rc.get("kind") == "assumption"
            and rc.get("reason") == "no_recorded_assumption_or_requirement"
            for rc in result.rejected_claims
        )

    def test_assumption_with_pricing_assumption_kept(self) -> None:
        items = [_item("it_1")]
        price = _price_row()
        price["assumptions"] = [
            {"field": "treatment", "value": "pressure_treated_above_ground", "reason": "default"}
        ]
        inp = _input(
            items=items,
            triage={"it_1": "price"},
            prices={"it_1": price},
            assumptions=[{"item_id": "it_1", "text": "defaulted PT variant"}],
        )
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert len(result.agent_bid.assumptions) == 1

    def test_question_unknown_basis_dropped(self) -> None:
        items = [_item("it_1")]
        inp = _input(
            items=items,
            triage={"it_1": "price"},
            prices={"it_1": _price_row()},
            contractor_questions=[
                {"text": "Q?", "basis": {"kind": "gap_rule", "ref_id": "not_fired"}}
            ],
        )
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert result.agent_bid.contractor_questions == []
        assert any(rc.get("kind") == "contractor_question" for rc in result.rejected_claims)

    def test_question_known_gap_rule_kept(self) -> None:
        items = [_item("it_1")]
        inp = _input(
            items=items,
            triage={"it_1": "price"},
            prices={"it_1": _price_row()},
            gap_rule_ids={"pt_variant_above_vs_ground_contact"},
            contractor_questions=[
                {
                    "text": "Above- or ground-contact?",
                    "basis": {
                        "kind": "gap_rule",
                        "ref_id": "pt_variant_above_vs_ground_contact",
                    },
                }
            ],
        )
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert len(result.agent_bid.contractor_questions) == 1


# ---------------------------------------------------------------------------
# no_materials outcome + fallback path
# ---------------------------------------------------------------------------


class TestEdgeOutcomes:
    def test_no_materials(self) -> None:
        inp = _input(items=[], summary="dummy")
        result = build_bid(inp)
        assert result.outcome == "no_materials"
        assert result.agent_bid is None
        assert result.draft_bid is None

    def test_fallback_has_code_summary(self) -> None:
        items = [_item("it_1")]
        inp = _input(
            items=items,
            triage={"it_1": "price"},
            prices={"it_1": _price_row()},
            summary="",  # model didn't submit
            generated_by=GeneratedBy.fallback,
            partial_reasons=["budget_exhausted(turns)"],
        )
        result = build_bid(inp)
        assert result.agent_bid is not None
        assert result.agent_bid.generated_by == GeneratedBy.fallback
        assert "Assembled automatically" in result.agent_bid.summary
        assert "budget_exhausted(turns)" in result.agent_bid.summary


# ---------------------------------------------------------------------------
# Totals identity + agent/draft pair
# ---------------------------------------------------------------------------


class TestTotalsAndPair:
    def test_totals_identity(self) -> None:
        items = [_item("it_1"), _item("it_2", description="2x6 x 16 SPF", quantity_text="30")]
        inp = _input(
            items=items,
            triage={"it_1": "price", "it_2": "price"},
            prices={
                "it_1": _price_row(unit_price_cents=400, sale_qty="50"),  # 20000
                "it_2": _price_row(
                    sku="LUM-2X6-16-SPF-2", unit_price_cents=1280, sale_qty="30"
                ),  # 38400
            },
        )
        result = build_bid(inp)
        assert result.agent_bid is not None
        bid = result.agent_bid
        # 20_000 + 38_400 = 58_400 subtotal.
        assert bid.totals.subtotal_cents == 58_400
        # NJ 7% ppm: tax = (58_400 * 70_000 + 500_000) // 1_000_000 = 4088 cents.
        assert bid.totals.tax_cents == 4088
        assert bid.totals.total_cents == 58_400 + 4088
        assert bid.totals.priced_lines == 2
        assert bid.totals.open_flags == 0

    def test_draft_mirrors_agent(self) -> None:
        items = [_item("it_1")]
        inp = _input(
            items=items,
            triage={"it_1": "price"},
            prices={"it_1": _price_row()},
        )
        result = build_bid(inp)
        assert result.agent_bid is not None and result.draft_bid is not None
        # Identical except kind + revision.
        agent = result.agent_bid.model_dump(mode="python")
        draft = result.draft_bid.model_dump(mode="python")
        assert agent["kind"] == "agent" and agent["revision"] == 0
        assert draft["kind"] == "draft" and draft["revision"] == 1
        agent.pop("kind")
        agent.pop("revision")
        draft.pop("kind")
        draft.pop("revision")
        assert agent == draft
