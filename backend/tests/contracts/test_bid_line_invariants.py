"""BidLine logical invariants (SPEC §9.1 + the §13 amendment).

  - extended_cents = ROUND_HALF_UP(sale_qty × unit_price_cents)
  - origins.unit_price='agent' with price set ⇒ catalog price set AND equal
    (agents cannot author prices — golden rule 4)
  - override_reason: required when origins.unit_price='user_overridden',
    forbidden otherwise

Each test builds a valid baseline, asserts it validates, overrides
exactly one field, and asserts the ValidationError message names the
specific rule that fired — so a test can't silently pass on an
unrelated failure.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import (
    BidLine,
    MatchInfo,
    MatchStatus,
    Origin,
    Origins,
    ReviewState,
    ReviewStateT,
    SourceRef,
    StockInfo,
    extended_cents_of,
)


def _baseline_kwargs() -> dict[str, Any]:
    qty = Decimal("40")
    cents = 1575
    return {
        "line_id": "line_01",
        "item_id": "item_12",
        "description": "2x6x16 PT",
        "source": SourceRef(page=3, quote="(40) 2x6x16 PT", read_via="text", verified=True),
        "requested_quantity_text": "40",
        "requested_unit_raw": "EA",
        "sku": "LUM-2X6-16-SYP-2-PTAG",
        "product_name": "SYP PT",
        "sale_qty": qty,
        "sale_unit": "EA",
        "conversion_path": "40 EA → 40 EA (same unit)",
        "unit_price_cents": cents,
        "catalog_unit_price_cents": cents,
        "extended_cents": extended_cents_of(qty, cents),
        "stock": StockInfo(on_hand=120, shortfall=0, next_receipt=None, transfer_options=[]),
        "match": MatchInfo(
            status=MatchStatus.matched,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=False,
            substitute_reason=None,
            verify_notes=[],
        ),
        "requirements": [],
        "review": ReviewState(state=ReviewStateT.none, reasons=[], resolved_by=None),
        "origins": Origins(
            quantity=Origin.agent,
            unit_price=Origin.agent,
            sku=Origin.agent,
            description=Origin.agent,
        ),
        "override_reason": None,
    }


def test_baseline_bidline_validates() -> None:
    BidLine(**_baseline_kwargs())


# ---------------------------------------------------------------------------
# extended_cents identity
# ---------------------------------------------------------------------------


class TestExtendedCentsIdentity:
    def test_mismatch_rejected(self) -> None:
        kwargs = {**_baseline_kwargs(), "extended_cents": 62999}
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any("ROUND_HALF_UP(sale_qty" in e["msg"] for e in exc.value.errors()), (
            exc.value.errors()
        )

    def test_extended_set_without_price_rejected(self) -> None:
        kwargs = {
            **_baseline_kwargs(),
            "unit_price_cents": None,
            "catalog_unit_price_cents": None,
            "extended_cents": 63000,
        }
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any("extended_cents requires both" in e["msg"] for e in exc.value.errors()), (
            exc.value.errors()
        )


# ---------------------------------------------------------------------------
# origins.unit_price='agent' ⇒ catalog price set AND equal
# ---------------------------------------------------------------------------


class TestAgentOriginMatchesCatalog:
    def test_matching_price_accepted(self) -> None:
        BidLine(**_baseline_kwargs())

    def test_mismatched_price_rejected(self) -> None:
        kwargs = _baseline_kwargs()
        kwargs["unit_price_cents"] = 1600  # ≠ catalog 1575
        kwargs["extended_cents"] = extended_cents_of(Decimal("40"), 1600)
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any("origins.unit_price='agent' but" in e["msg"] for e in exc.value.errors()), (
            exc.value.errors()
        )

    def test_agent_price_without_catalog_rejected(self) -> None:
        # Closes the loophole: an agent-origin price with no catalog
        # anchor is effectively an agent-authored price. SPEC §4.1
        # forbids that.
        kwargs = _baseline_kwargs()
        kwargs["catalog_unit_price_cents"] = None
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any(
            "requires catalog_unit_price_cents to be set" in e["msg"] for e in exc.value.errors()
        ), exc.value.errors()

    def test_agent_origin_unpriced_line_accepted(self) -> None:
        # A line with no price at all is fine when the pricing agent
        # reported no_match / failed / timeout. The match.status must
        # reflect that — a 'matched' line without a price is a
        # separate invariant failure (see TestMatchedRequiresPrice).
        kwargs = _baseline_kwargs()
        kwargs["unit_price_cents"] = None
        kwargs["catalog_unit_price_cents"] = None
        kwargs["extended_cents"] = None
        kwargs["sku"] = None
        kwargs["product_name"] = None
        kwargs["match"] = MatchInfo(
            status=MatchStatus.no_match,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=False,
            substitute_reason=None,
            verify_notes=[],
        )
        BidLine(**kwargs)

    def test_user_filled_price_with_no_catalog_accepted(self) -> None:
        # A user filling in a price for a SKU with no catalog row is
        # fine — the override isn't anchored to a catalog number.
        kwargs = _baseline_kwargs()
        kwargs["unit_price_cents"] = 1800
        kwargs["catalog_unit_price_cents"] = None
        kwargs["extended_cents"] = extended_cents_of(Decimal("40"), 1800)
        kwargs["origins"] = Origins(
            quantity=Origin.agent,
            unit_price=Origin.user_filled,
            sku=Origin.agent,
            description=Origin.agent,
        )
        BidLine(**kwargs)

    def test_mismatch_allowed_when_user_overridden(self) -> None:
        kwargs = _baseline_kwargs()
        kwargs["unit_price_cents"] = 1600
        kwargs["extended_cents"] = extended_cents_of(Decimal("40"), 1600)
        kwargs["origins"] = Origins(
            quantity=Origin.agent,
            unit_price=Origin.user_overridden,
            sku=Origin.agent,
            description=Origin.agent,
        )
        kwargs["override_reason"] = "Customer accepted the higher spec."
        BidLine(**kwargs)


# ---------------------------------------------------------------------------
# override_reason is bound to user_overridden
# ---------------------------------------------------------------------------


def _with_override_price(reason: str | None, origin: Origin) -> dict[str, Any]:
    kwargs = _baseline_kwargs()
    kwargs["origins"] = Origins(
        quantity=Origin.agent,
        unit_price=origin,
        sku=Origin.agent,
        description=Origin.agent,
    )
    kwargs["override_reason"] = reason
    if origin == Origin.user_overridden:
        kwargs["unit_price_cents"] = 1600
        kwargs["extended_cents"] = extended_cents_of(Decimal("40"), 1600)
    return kwargs


class TestOverrideReasonBinding:
    def test_user_overridden_without_reason_rejected(self) -> None:
        kwargs = _with_override_price(None, Origin.user_overridden)
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any(
            "requires a non-empty override_reason" in e["msg"] for e in exc.value.errors()
        ), exc.value.errors()

    def test_user_overridden_blank_reason_rejected(self) -> None:
        kwargs = _with_override_price("   ", Origin.user_overridden)
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any(
            "requires a non-empty override_reason" in e["msg"] for e in exc.value.errors()
        ), exc.value.errors()

    def test_user_overridden_with_reason_accepted(self) -> None:
        kwargs = _with_override_price("Customer upgraded spec.", Origin.user_overridden)
        BidLine(**kwargs)

    def test_agent_origin_with_reason_rejected(self) -> None:
        kwargs = _with_override_price("sneaky", Origin.agent)
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any(
            "override_reason is only allowed when origins.unit_price='user_overridden'" in e["msg"]
            for e in exc.value.errors()
        ), exc.value.errors()

    def test_user_filled_origin_with_reason_rejected(self) -> None:
        kwargs = _with_override_price("note", Origin.user_filled)
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any(
            "override_reason is only allowed when origins.unit_price='user_overridden'" in e["msg"]
            for e in exc.value.errors()
        ), exc.value.errors()


# ---------------------------------------------------------------------------
# user_overridden ⇒ catalog price must exist (otherwise it's user_filled)
# ---------------------------------------------------------------------------


class TestUserOverriddenRequiresCatalog:
    def test_user_overridden_without_catalog_price_rejected(self) -> None:
        # A price with no catalog anchor isn't an override — it's a
        # fill. Forcing the shape prevents the UI from showing an
        # audit-trail line like "overridden from (none) → $17.50".
        kwargs = _baseline_kwargs()
        kwargs["catalog_unit_price_cents"] = None
        kwargs["unit_price_cents"] = 1750
        kwargs["extended_cents"] = extended_cents_of(Decimal("40"), 1750)
        kwargs["origins"] = Origins(
            quantity=Origin.agent,
            unit_price=Origin.user_overridden,
            sku=Origin.agent,
            description=Origin.agent,
        )
        kwargs["override_reason"] = "Customer agreed higher spec."
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any(
            "requires a catalog_unit_price_cents to override" in e["msg"]
            for e in exc.value.errors()
        ), exc.value.errors()


# ---------------------------------------------------------------------------
# match.status ∈ {matched, substitute} ⇒ unit_price_cents set
# ---------------------------------------------------------------------------


def _unpriced_line_kwargs() -> dict[str, Any]:
    """A line with no price at all — used to toggle match.status in tests."""

    kwargs = _baseline_kwargs()
    kwargs["unit_price_cents"] = None
    kwargs["catalog_unit_price_cents"] = None
    kwargs["extended_cents"] = None
    kwargs["sku"] = None
    kwargs["product_name"] = None
    return kwargs


class TestMatchedRequiresPrice:
    def test_matched_without_price_rejected(self) -> None:
        kwargs = _unpriced_line_kwargs()
        kwargs["match"] = MatchInfo(
            status=MatchStatus.matched,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=False,
            substitute_reason=None,
            verify_notes=[],
        )
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any("requires unit_price_cents" in e["msg"] for e in exc.value.errors()), (
            exc.value.errors()
        )

    def test_substitute_without_price_rejected(self) -> None:
        kwargs = _unpriced_line_kwargs()
        kwargs["match"] = MatchInfo(
            status=MatchStatus.substitute,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=True,
            substitute_reason="Closest in-stock option.",
            verify_notes=[],
        )
        with pytest.raises(ValidationError) as exc:
            BidLine(**kwargs)
        assert any("requires unit_price_cents" in e["msg"] for e in exc.value.errors()), (
            exc.value.errors()
        )

    def test_no_match_without_price_accepted(self) -> None:
        kwargs = _unpriced_line_kwargs()
        kwargs["match"] = MatchInfo(
            status=MatchStatus.no_match,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=False,
            substitute_reason=None,
            verify_notes=[],
        )
        BidLine(**kwargs)
