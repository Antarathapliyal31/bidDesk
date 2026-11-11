"""Pricing-side contract shapes: PricedLineView (SSE), MatchInfo / StockInfo
sub-shapes used by the Bid, TransferOption (with the SPEC §13 sort), and
RepriceProposal.

`PricedLineView` is the payload on the live stream (`item.priced`). The
richer `MatchInfo` + `StockInfo` sit inside `BidLine` (SPEC §9.1). They
are kept separate so the SSE payload stays small and the bid view stays
complete.
"""

from __future__ import annotations

from datetime import date

from pydantic import Field, StrictBool, StrictInt, model_validator

from .common import Cents, ConfidenceLevel, Contract, Count, DecimalStr, MatchStatus


class PricedLineView(Contract):
    """Payload of `item.priced` on the SSE stream (SPEC §10.3).

    Field names match `BidLine` so the live-view code and the bid-view
    code can share formatters.
    """

    item_id: str
    status: MatchStatus
    sku: str | None
    product_name: str | None
    sale_qty: DecimalStr | None
    sale_unit: str | None
    unit_price_cents: Cents | None
    extended_cents: Cents | None
    conversion_path: str | None
    confidence: ConfidenceLevel | None
    confidence_reasons: list[str]
    shortfall: Count | None


class MatchAssumption(Contract):
    """One structured assumption the pricing agent recorded while matching.

    Per `assumption_discipline` playbook (DECISIONS #7), an assumption is
    only recorded when the agent chose between real catalog alternatives.
    Shape: `{field, value, reason}`. `value` allows str / int / bool so
    numeric catalog fields (`length_ft=8`) and flags (`treated=True`)
    don't have to be stringified at the boundary.
    """

    field: str
    value: str | int | bool
    reason: str


class MatchInfo(Contract):
    """Sub-shape of BidLine — pricing-agent's decision record for the line.

    Invariant: `status == 'substitute' ⇔ is_substitute == True`.
    Otherwise the UI badge (which keys off `is_substitute`) can disagree
    with the status.
    """

    status: MatchStatus
    confidence: ConfidenceLevel | None
    confidence_reasons: list[str]
    assumptions: list[MatchAssumption]
    is_substitute: StrictBool
    substitute_reason: str | None
    # Verifier flags from `agents/pricing/verify.py` (SPEC §8.3). The set
    # is open-ended so this stays a plain string list; contracts don't
    # enumerate values here, the verifier code is the source of truth.
    verify_notes: list[str]

    @model_validator(mode="after")
    def _substitute_status_matches_flag(self) -> MatchInfo:
        if self.status == MatchStatus.substitute and not self.is_substitute:
            raise ValueError("MatchInfo: status='substitute' requires is_substitute=True")
        if self.status != MatchStatus.substitute and self.is_substitute:
            raise ValueError(
                f"MatchInfo: is_substitute=True requires status='substitute' "
                f"(got status={self.status.value!r})"
            )
        return self


class NextReceipt(Contract):
    date: date
    qty: StrictInt = Field(gt=0)


class TransferOption(Contract):
    """One cross-branch option when the home branch is short of stock.

    Per SPEC §8.3 `transfer_options_json`. `available_qty > 0`: a branch
    with zero stock shouldn't appear as an option. The server sorts the
    list before returning it (SPEC §13) — the frontend never sorts.
    """

    branch_id: str
    branch_name: str
    available_qty: StrictInt = Field(gt=0)
    covers_shortfall: StrictBool


def _sort_key(t: TransferOption) -> tuple[bool, int, str]:
    """SPEC §13 sort key: covers_shortfall desc → available_qty desc → branch_id asc."""

    return (not t.covers_shortfall, -t.available_qty, t.branch_id)


def sort_transfer_options(options: list[TransferOption]) -> list[TransferOption]:
    """Return `options` sorted per SPEC §13. Pure; the StockInfo validator
    enforces that callers use this (or produce the same ordering)."""

    return sorted(options, key=_sort_key)


class StockInfo(Contract):
    """Sub-shape of BidLine — the stock + transfer picture at the home branch.

    Invariants (SPEC §8.3, §13):
      - on_hand >= 0, shortfall >= 0.
      - shortfall == 0 ⇒ transfer_options == [] (no shortfall, nothing
        to transfer; the agent has no business proposing moves).
      - each transfer option: covers_shortfall == (available_qty >= shortfall).
      - transfer_options sorted per `sort_transfer_options`.
    """

    on_hand: Count
    shortfall: Count
    next_receipt: NextReceipt | None
    transfer_options: list[TransferOption]

    @model_validator(mode="after")
    def _invariants(self) -> StockInfo:
        if self.shortfall == 0 and self.transfer_options:
            raise ValueError("shortfall=0 forbids transfer_options; nothing to cover")
        for opt in self.transfer_options:
            expected = opt.available_qty >= self.shortfall
            if opt.covers_shortfall != expected:
                raise ValueError(
                    f"transfer_options[{opt.branch_id!r}].covers_shortfall="
                    f"{opt.covers_shortfall} contradicts available_qty="
                    f"{opt.available_qty} vs shortfall={self.shortfall}"
                )
        for i in range(1, len(self.transfer_options)):
            prev_key = _sort_key(self.transfer_options[i - 1])
            curr_key = _sort_key(self.transfer_options[i])
            if prev_key > curr_key:
                raise ValueError(
                    "transfer_options must be sorted per SPEC §13 "
                    "(covers_shortfall desc → available_qty desc → branch_id asc); "
                    "pipe the list through pricing.sort_transfer_options first"
                )
        return self


class RepriceProposal(Contract):
    """Response from `POST /api/runs/{id}/bid/lines/{line_id}/reprice`.

    Per SPEC §9.5 / §13. The proposal is NOT applied until an
    `apply_proposal` op lands in a PATCH; `dismiss_proposal` is the
    no-op alternative.
    """

    proposal_id: str
    before: PricedLineView
    after: PricedLineView
