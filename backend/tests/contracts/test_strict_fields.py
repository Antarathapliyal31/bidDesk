"""Targeted-strictness tests for Cents / Count / StrictBool fields.

The global model config is lax (`extra="forbid"` only) so SSE replay
and `model_dump(mode='json')` round-trips work. But numeric and
boolean fields that encode real invariants (money, counts,
verification) are per-field `StrictInt` / `StrictBool`, so a string
or an accidental bool-for-int doesn't silently coerce.

Each rejection test builds a VALID baseline (asserts it validates),
then overrides a single field and asserts the ValidationError is
attributable to that exact field via `exc.value.errors()[*]["loc"]`.
This keeps the tests from passing on an unrelated validation failure.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.contracts import (
    Bid,
    BidKind,
    BidLine,
    BranchInfo,
    ContractorQuestionBasis,
    ContractorQuestionView,
    ExclusionLine,
    GeneratedBy,
    MatchInfo,
    MatchStatus,
    Origin,
    Origins,
    PricedLineView,
    ProjectInfo,
    ReviewState,
    ReviewStateT,
    RunEventAdapter,
    SourceRef,
    StockInfo,
    Totals,
    TransferOption,
    extended_cents_of,
)


def _locs(exc: ValidationError) -> list[tuple[Any, ...]]:
    return [tuple(e["loc"]) for e in exc.errors()]


# ---------------------------------------------------------------------------
# Baseline kwargs — each test overrides exactly one field
# ---------------------------------------------------------------------------


def _priced_line_kwargs() -> dict[str, Any]:
    return {
        "item_id": "i1",
        "status": MatchStatus.matched,
        "sku": "S1",
        "product_name": "x",
        "sale_qty": Decimal("1"),
        "sale_unit": "EA",
        "unit_price_cents": 325,
        "extended_cents": 325,
        "conversion_path": None,
        "confidence": None,
        "confidence_reasons": [],
        "shortfall": 0,
    }


def _source_ref_kwargs() -> dict[str, Any]:
    return {"page": 1, "quote": "x", "read_via": "text", "verified": True}


def _transfer_option_kwargs() -> dict[str, Any]:
    return {
        "branch_id": "BR-NY-01",
        "branch_name": "LI",
        "available_qty": 50,
        "covers_shortfall": True,
    }


def _stock_info_kwargs() -> dict[str, Any]:
    return {
        "on_hand": 10,
        "shortfall": 0,
        "next_receipt": None,
        "transfer_options": [],
    }


def test_baselines_validate() -> None:
    PricedLineView(**_priced_line_kwargs())
    SourceRef(**_source_ref_kwargs())
    TransferOption(**_transfer_option_kwargs())
    StockInfo(**_stock_info_kwargs())


# ---------------------------------------------------------------------------
# Cents (StrictInt + ge=0)
# ---------------------------------------------------------------------------


class TestCentsField:
    def test_string_rejected_for_unit_price(self) -> None:
        kwargs = {**_priced_line_kwargs(), "unit_price_cents": "325"}
        with pytest.raises(ValidationError) as exc:
            PricedLineView(**kwargs)
        assert ("unit_price_cents",) in _locs(exc.value)

    def test_bool_rejected_for_unit_price(self) -> None:
        kwargs = {**_priced_line_kwargs(), "unit_price_cents": True}
        with pytest.raises(ValidationError) as exc:
            PricedLineView(**kwargs)
        assert ("unit_price_cents",) in _locs(exc.value)

    def test_float_rejected_for_unit_price(self) -> None:
        kwargs = {**_priced_line_kwargs(), "unit_price_cents": 325.0}
        with pytest.raises(ValidationError) as exc:
            PricedLineView(**kwargs)
        assert ("unit_price_cents",) in _locs(exc.value)

    def test_negative_rejected_for_unit_price(self) -> None:
        kwargs = {**_priced_line_kwargs(), "unit_price_cents": -1, "extended_cents": None}
        with pytest.raises(ValidationError) as exc:
            PricedLineView(**kwargs)
        assert ("unit_price_cents",) in _locs(exc.value)


# ---------------------------------------------------------------------------
# StrictBool
# ---------------------------------------------------------------------------


class TestStrictBoolField:
    def test_source_ref_verified_rejects_string(self) -> None:
        kwargs = {**_source_ref_kwargs(), "verified": "true"}
        with pytest.raises(ValidationError) as exc:
            SourceRef(**kwargs)
        assert ("verified",) in _locs(exc.value)

    def test_source_ref_verified_rejects_int(self) -> None:
        kwargs = {**_source_ref_kwargs(), "verified": 1}
        with pytest.raises(ValidationError) as exc:
            SourceRef(**kwargs)
        assert ("verified",) in _locs(exc.value)

    def test_transfer_option_covers_shortfall_rejects_string(self) -> None:
        kwargs = {**_transfer_option_kwargs(), "covers_shortfall": "true"}
        with pytest.raises(ValidationError) as exc:
            TransferOption(**kwargs)
        assert ("covers_shortfall",) in _locs(exc.value)


# ---------------------------------------------------------------------------
# SourceRef — verified semantics + page ge=1
# ---------------------------------------------------------------------------


class TestSourceRefSemantics:
    def test_empty_quote_rejected_when_verified(self) -> None:
        kwargs = {**_source_ref_kwargs(), "quote": ""}
        with pytest.raises(ValidationError) as exc:
            SourceRef(**kwargs)
        # Model-wide validator; loc is the model root.
        assert any("verified=True" in e["msg"] for e in exc.value.errors())

    def test_whitespace_quote_rejected_when_verified(self) -> None:
        kwargs = {**_source_ref_kwargs(), "quote": "   "}
        with pytest.raises(ValidationError) as exc:
            SourceRef(**kwargs)
        assert any("verified=True" in e["msg"] for e in exc.value.errors())

    def test_empty_quote_fine_when_unverified(self) -> None:
        # A vision-read or absent quote is allowed as long as verified=False.
        SourceRef(page=None, quote=None, read_via=None, verified=False)
        SourceRef(page=1, quote="", read_via="vision", verified=False)

    def test_page_must_be_ge_1(self) -> None:
        kwargs = {**_source_ref_kwargs(), "page": 0}
        with pytest.raises(ValidationError) as exc:
            SourceRef(**kwargs)
        assert ("page",) in _locs(exc.value)


# ---------------------------------------------------------------------------
# Count (StrictInt + ge=0)
# ---------------------------------------------------------------------------


class TestCountField:
    def test_stock_info_on_hand_rejects_string(self) -> None:
        kwargs = {**_stock_info_kwargs(), "on_hand": "10"}
        with pytest.raises(ValidationError) as exc:
            StockInfo(**kwargs)
        assert ("on_hand",) in _locs(exc.value)

    def test_stock_info_on_hand_rejects_bool(self) -> None:
        kwargs = {**_stock_info_kwargs(), "on_hand": True}
        with pytest.raises(ValidationError) as exc:
            StockInfo(**kwargs)
        assert ("on_hand",) in _locs(exc.value)

    def test_stock_info_shortfall_rejects_negative(self) -> None:
        kwargs = {**_stock_info_kwargs(), "shortfall": -1}
        with pytest.raises(ValidationError) as exc:
            StockInfo(**kwargs)
        assert ("shortfall",) in _locs(exc.value)


# ---------------------------------------------------------------------------
# JSON round-trip (full Bid + RunEvent) — proves SSE replay unaffected
# ---------------------------------------------------------------------------


def _verified_source(page: int, quote: str) -> SourceRef:
    return SourceRef(page=page, quote=quote, read_via="text", verified=True)


def _priced_line(line_id: str, qty: str, cents: int) -> BidLine:
    sale_qty = Decimal(qty)
    extended = extended_cents_of(sale_qty, cents)
    return BidLine(
        line_id=line_id,
        item_id=f"item_{line_id}",
        description="2x6x16 PT",
        source=_verified_source(3, "(40) 2x6x16 PT"),
        requested_quantity_text=qty,
        requested_unit_raw="EA",
        sku="LUM-2X6-16-SYP-2-PTAG",
        product_name="SYP PT",
        sale_qty=sale_qty,
        sale_unit="EA",
        conversion_path=f"{qty} EA → {qty} EA (same unit)",
        unit_price_cents=cents,
        catalog_unit_price_cents=cents,
        extended_cents=extended,
        stock=StockInfo(on_hand=120, shortfall=0, next_receipt=None, transfer_options=[]),
        match=MatchInfo(
            status=MatchStatus.matched,
            confidence=None,
            confidence_reasons=[],
            assumptions=[],
            is_substitute=False,
            substitute_reason=None,
            verify_notes=[],
        ),
        requirements=[],
        review=ReviewState(state=ReviewStateT.none, reasons=[], resolved_by=None),
        origins=Origins(
            quantity=Origin.agent,
            unit_price=Origin.agent,
            sku=Origin.agent,
            description=Origin.agent,
        ),
        override_reason=None,
    )


def _full_bid() -> Bid:
    line = _priced_line("line_01", "40", 1575)
    subtotal = line.extended_cents or 0
    tax_rate_ppm = 66250
    tax = (subtotal * tax_rate_ppm + 500_000) // 1_000_000
    return Bid(
        run_id="run_abc",
        kind=BidKind.agent,
        revision=0,
        branch=BranchInfo(id="BR-NJ-01", name="BidDesk North Jersey", tax_rate_ppm=66250),
        project=ProjectInfo(name="Maple St", due_date_text=None),
        lines=[line],
        exclusions=[
            ExclusionLine(
                description="No labor",
                source=_verified_source(2, "No labor included"),
            )
        ],
        labor=[],
        unreadable=[],
        conflicts=[],
        assumptions=[],
        contractor_questions=[
            ContractorQuestionView(
                text="Confirm species.",
                basis=ContractorQuestionBasis(kind="flagged_line", ref_id="item_line_01"),
            )
        ],
        summary="Short summary.",
        generated_by=GeneratedBy.orchestrator,
        partial_reasons=[],
        totals=Totals(
            subtotal_cents=subtotal,
            tax_rate_ppm=tax_rate_ppm,
            tax_cents=tax,
            total_cents=subtotal + tax,
            priced_lines=1,
            open_flags=0,
        ),
    )


class TestJsonRoundTrips:
    def test_full_bid_roundtrips_via_validate_json(self) -> None:
        bid = _full_bid()
        payload = bid.model_dump_json()
        revived = Bid.model_validate_json(payload)
        assert revived == bid

    def test_full_bid_roundtrips_via_validate_dict(self) -> None:
        bid = _full_bid()
        payload = bid.model_dump(mode="json")
        revived = Bid.model_validate(payload)
        assert revived == bid

    def test_run_event_roundtrips_via_validate_json(self) -> None:
        side_a = _verified_source(4, "all plates on concrete to be PT")
        side_b = _verified_source(7, "2x6 SPF, 40 pcs")
        event = {
            "id": 42,
            "run_id": "run_abc",
            "ts": "2026-11-10T13:02:11.123Z",
            "agent": "orchestrator",
            "span_id": "span-7",
            "parent_span_id": "span-1",
            "type": "conflict.flagged",
            "payload": {
                "conflict": {
                    "id": "conf_1",
                    "type": "spec_vs_list",
                    "side_a": {
                        "item_id": "item_5",
                        "source": side_a.model_dump(mode="json"),
                    },
                    "side_b": {
                        "item_id": None,
                        "source": side_b.model_dump(mode="json"),
                    },
                    "explanation": "Spec says PT; list says SPF.",
                }
            },
        }
        revived = RunEventAdapter.validate_python(event)
        assert revived.type == "conflict.flagged"
        revived_json = RunEventAdapter.validate_json(json.dumps(event))
        assert revived_json == revived
