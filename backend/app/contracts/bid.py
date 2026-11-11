"""Bid-side contract shapes: Bid, BidLine, exclusions / labor / unreadable,
assumptions / contractor questions / conflicts, totals, requirements.

All per SPEC §9.1 + the §13 amendment.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import Field, StrictInt, model_validator

from .common import (
    BidKind,
    Cents,
    ConflictType,
    Contract,
    Count,
    DecimalStr,
    GeneratedBy,
    MatchStatus,
    Origin,
    ResolvedBy,
    ReviewStateT,
    SourceRef,
)
from .pricing import MatchInfo, StockInfo

# ---------------------------------------------------------------------------
# Review reasons — the closed set used by `review.reasons` (SPEC §9.3 step 3)
# ---------------------------------------------------------------------------
ReviewReason = Literal[
    # `source.verified=False` on the line. Two paths reach this (SPEC
    # §6.4, DECISIONS #17): (a) the page was read via vision so no
    # byte match was possible; (b) the page was text-read but the
    # analyst's quote didn't verify against page text. Either way the
    # LINE is kept and surfaced for review — only *requirements* and
    # *conflicts* that would rest on such a quote go to rejected_claims.
    "unverified",
    "quantity_not_in_quote",
    "unknown_unit",
    "sanity_range",  # sale quantity falls outside the §8.3 per-category sanity range
    "low_confidence",
    "no_match",
    "failed",
    "timeout",
    "shortfall",
    "substitute",
    "in_conflict",
    "not_priced",
]


# ---------------------------------------------------------------------------
# Rounding helpers (SPEC §9.3 step 4 + DECISIONS #11)
# ---------------------------------------------------------------------------


def _round_half_up_to_cents(d: Decimal) -> int:
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def extended_cents_of(sale_qty: Decimal, unit_price_cents: int) -> int:
    """`extended_cents = ROUND_HALF_UP(Decimal(sale_qty) × unit_price_cents)`.

    One canonical formula so the BidLine validator and the Phase-4
    `bid/totals.py` can't disagree.
    """

    return _round_half_up_to_cents(sale_qty * Decimal(unit_price_cents))


def tax_cents_of(subtotal_cents: int, tax_rate_ppm: int) -> int:
    """Half-up on a single subtotal (DECISIONS #11, SPEC §13)."""

    return (subtotal_cents * tax_rate_ppm + 500_000) // 1_000_000


# ---------------------------------------------------------------------------
# Requirements — attached by the orchestrator via Pass 1 gap rules
# ---------------------------------------------------------------------------


class RequirementView(Contract):
    """A structured constraint attached to an item.

    Per SPEC §13 amendment / DECISIONS #13: `{field, value | value_in,
    reason, source}`. Exactly one of `value` / `value_in` must be set;
    `value_in` is non-empty when used.

    The pricing agent reads these as additional filters on the SKU
    lookup — the constraint is deterministic code behaviour, not a
    prompt instruction.
    """

    field: str
    value: str | int | bool | None = None
    value_in: list[str | int | bool] | None = None
    reason: str
    source: SourceRef

    @model_validator(mode="after")
    def _exactly_one_value_form(self) -> RequirementView:
        has_value = self.value is not None
        has_value_in = self.value_in is not None
        if has_value == has_value_in:
            raise ValueError("requirement: exactly one of 'value' or 'value_in' must be set")
        if has_value_in and not self.value_in:
            raise ValueError("requirement: 'value_in' must be non-empty when set")
        return self


# ---------------------------------------------------------------------------
# Review state (per-line)
# ---------------------------------------------------------------------------


class ReviewState(Contract):
    """Per SPEC §9.1. `resolved_by` only meaningful when `state='resolved'`."""

    state: ReviewStateT
    reasons: list[ReviewReason]
    resolved_by: ResolvedBy | None

    @model_validator(mode="after")
    def _resolved_by_matches_state(self) -> ReviewState:
        if self.state == ReviewStateT.resolved and self.resolved_by is None:
            raise ValueError("review.state='resolved' requires resolved_by")
        if self.state != ReviewStateT.resolved and self.resolved_by is not None:
            raise ValueError(f"review.resolved_by must be None when state={self.state!r}")
        return self


# ---------------------------------------------------------------------------
# BidLine origins
# ---------------------------------------------------------------------------

OriginField = Literal["quantity", "unit_price", "sku", "description"]


class Origins(Contract):
    """Per SPEC §9.1 — provenance per editable field."""

    quantity: Origin
    unit_price: Origin
    sku: Origin
    description: Origin


# ---------------------------------------------------------------------------
# Bid children (non-line entries)
# ---------------------------------------------------------------------------


class ExclusionLine(Contract):
    """Must carry a source quote (SPEC §9.3 step 2)."""

    description: str
    source: SourceRef


class LaborLine(Contract):
    """Labor is enumerated but not priced — 'Not priced — for estimator'.

    `source` is optional: some labor lines carry a quote ("Framing
    labor, page 3"), others are implicit ("no labor included"). When
    present, SourceRef's own validator still enforces verified/text
    consistency.
    """

    description: str
    quantity_text: str | None
    unit_raw: str | None
    source: SourceRef | None = None


class UnreadableLine(Contract):
    description: str


class AssumptionView(Contract):
    """Per SPEC §9.2 — assumption that corresponds to a recorded pricing
    assumption or requirement for `item_id`. §9.2 validation is
    enforced in `bid/assemble.py` (Phase 5), not here."""

    item_id: str
    text: str


class ContractorQuestionBasis(Contract):
    kind: Literal["gap_rule", "conflict", "flagged_line"]
    ref_id: str


class ContractorQuestionView(Contract):
    """Per SPEC §9.2 — `text ≤ 300`, basis ref must exist (enforced in
    assembly, not here)."""

    text: str = Field(max_length=300)
    basis: ContractorQuestionBasis


class ConflictSide(Contract):
    """Half of a ConflictView — per SPEC §9.1, source carries page + quote
    + read_via + verified."""

    item_id: str | None
    source: SourceRef


class ConflictView(Contract):
    """Both sides must be verified (SourceRef.verified=True) — a conflict
    is a strong claim about what the document says, so both halves need
    text-matched citations. See DECISIONS #16: contradictions involving
    vision-read pages become `contractor_questions` with
    `basis.kind='flagged_line'`, not ConflictViews."""

    id: str
    type: ConflictType
    side_a: ConflictSide
    side_b: ConflictSide
    explanation: str

    @model_validator(mode="after")
    def _both_sides_verified(self) -> ConflictView:
        if not self.side_a.source.verified:
            raise ValueError("conflict side_a.source must be verified")
        if not self.side_b.source.verified:
            raise ValueError("conflict side_b.source must be verified")
        return self


# ---------------------------------------------------------------------------
# Totals + top-level fields
# ---------------------------------------------------------------------------


class BranchInfo(Contract):
    """Embedded in RunDetail + Bid. Mirrors `branches.yaml`."""

    id: str
    name: str
    tax_rate_ppm: StrictInt = Field(ge=0)


class ProjectInfo(Contract):
    name: str | None
    due_date_text: str | None


class Totals(Contract):
    """Per SPEC §9.1 / §13. Half-up tax formula lives in
    `bid/totals.py` (Phase 4); contract only enforces non-negative here —
    the full equality is checked by the Bid validator."""

    subtotal_cents: Cents
    tax_rate_ppm: StrictInt = Field(ge=0)
    tax_cents: Cents
    total_cents: Cents
    priced_lines: Count
    open_flags: Count


# ---------------------------------------------------------------------------
# BidLine + Bid
# ---------------------------------------------------------------------------


class BidLine(Contract):
    """Per SPEC §9.1 and API_CONTRACT `BidLine`.

    `line_id` is the stable identity used by edits; `item_id` is None for
    lines the user added (SPEC §9.4 `AddLine`). `extended_cents` is
    computed by code, never by the model (golden rule 4); the validator
    enforces the identity.
    """

    line_id: str
    item_id: str | None
    description: str
    source: SourceRef
    requested_quantity_text: str | None
    requested_unit_raw: str | None
    sku: str | None
    product_name: str | None
    sale_qty: DecimalStr | None
    sale_unit: str | None
    conversion_path: str | None
    unit_price_cents: Cents | None = None
    catalog_unit_price_cents: Cents | None = None
    extended_cents: Cents | None = None
    stock: StockInfo | None
    match: MatchInfo | None
    requirements: list[RequirementView]
    review: ReviewState
    origins: Origins
    override_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _invariants(self) -> BidLine:
        # extended_cents identity when all three operands are set.
        if (
            self.extended_cents is not None
            and self.sale_qty is not None
            and self.unit_price_cents is not None
        ):
            expected = extended_cents_of(self.sale_qty, self.unit_price_cents)
            if self.extended_cents != expected:
                raise ValueError(
                    f"BidLine[{self.line_id!r}]: extended_cents="
                    f"{self.extended_cents} ≠ ROUND_HALF_UP(sale_qty×unit_price_cents)"
                    f"={expected}"
                )
        # If extended_cents is set, both operands must be set too.
        if self.extended_cents is not None and (
            self.sale_qty is None or self.unit_price_cents is None
        ):
            raise ValueError(
                f"BidLine[{self.line_id!r}]: extended_cents requires both "
                f"sale_qty and unit_price_cents"
            )
        # Agent-origin price must come from the catalog. If an agent
        # line carries a price, the catalog row must exist AND the two
        # must match — otherwise the agent is effectively authoring
        # prices (golden rule 4). `user_filled` and `user_overridden`
        # are allowed to diverge from (or lack) a catalog price;
        # agent-origin with no price at all (unpriced line) is also
        # allowed.
        if self.origins.unit_price == Origin.agent and self.unit_price_cents is not None:
            if self.catalog_unit_price_cents is None:
                raise ValueError(
                    f"BidLine[{self.line_id!r}]: origins.unit_price='agent' with "
                    f"unit_price_cents={self.unit_price_cents} requires "
                    f"catalog_unit_price_cents to be set (agents cannot author prices)"
                )
            if self.unit_price_cents != self.catalog_unit_price_cents:
                raise ValueError(
                    f"BidLine[{self.line_id!r}]: origins.unit_price='agent' but "
                    f"unit_price_cents={self.unit_price_cents} ≠ "
                    f"catalog_unit_price_cents={self.catalog_unit_price_cents}"
                )
        # override_reason is bound to user_overridden: required when the
        # origin is user_overridden (non-empty); forbidden otherwise.
        if self.origins.unit_price == Origin.user_overridden:
            if not self.override_reason or not self.override_reason.strip():
                raise ValueError(
                    f"BidLine[{self.line_id!r}]: origins.unit_price="
                    f"'user_overridden' requires a non-empty override_reason"
                )
            # And there must be a catalog price to override — otherwise
            # it's a user_filled, not an override.
            if self.catalog_unit_price_cents is None:
                raise ValueError(
                    f"BidLine[{self.line_id!r}]: origins.unit_price="
                    f"'user_overridden' requires a catalog_unit_price_cents to "
                    f"override; a price with no catalog anchor is user_filled"
                )
        elif self.override_reason is not None:
            raise ValueError(
                f"BidLine[{self.line_id!r}]: override_reason is only allowed "
                f"when origins.unit_price='user_overridden' "
                f"(got origins.unit_price={self.origins.unit_price.value!r})"
            )
        # A matched / substitute line must carry a price — "matched"
        # with nothing to charge for is a contract contradiction.
        if (
            self.match is not None
            and self.match.status in (MatchStatus.matched, MatchStatus.substitute)
            and self.unit_price_cents is None
        ):
            raise ValueError(
                f"BidLine[{self.line_id!r}]: match.status="
                f"{self.match.status.value!r} requires unit_price_cents "
                f"(no-price lines must use status 'no_match', 'failed', "
                f"'timeout', or 'skipped')"
            )
        return self


class Bid(Contract):
    """Per SPEC §9.1 and the §13 amendment.

    `revision=0` is the immutable agent bid (enforced by DB trigger in
    Phase 2 step 2). Later revisions are drafts; `kind='final'` is
    produced by `POST /api/runs/{id}/bid/finalize`.

    The validator enforces the whole totals identity so a server bug
    in `bid/totals.py` or in an edit path can't ship a Bid whose
    rendered total disagrees with its own lines.
    """

    run_id: str
    kind: BidKind
    revision: Count
    branch: BranchInfo
    project: ProjectInfo
    lines: list[BidLine]
    exclusions: list[ExclusionLine]
    labor: list[LaborLine]
    unreadable: list[UnreadableLine]
    conflicts: list[ConflictView]
    assumptions: list[AssumptionView]
    contractor_questions: list[ContractorQuestionView]
    summary: str = Field(max_length=600)
    generated_by: GeneratedBy
    partial_reasons: list[str]
    totals: Totals

    @model_validator(mode="after")
    def _invariants(self) -> Bid:
        # Revision 0 is reserved for the immutable agent bid.
        if self.kind == BidKind.agent and self.revision != 0:
            raise ValueError("kind='agent' requires revision=0")
        if self.kind != BidKind.agent and self.revision == 0:
            raise ValueError("revision=0 reserved for kind='agent'")

        # Totals.tax_rate_ppm follows the branch.
        if self.totals.tax_rate_ppm != self.branch.tax_rate_ppm:
            raise ValueError(
                f"totals.tax_rate_ppm={self.totals.tax_rate_ppm} ≠ "
                f"branch.tax_rate_ppm={self.branch.tax_rate_ppm}"
            )

        # subtotal_cents = sum of priced lines' extended_cents.
        subtotal = sum(
            line.extended_cents for line in self.lines if line.extended_cents is not None
        )
        if self.totals.subtotal_cents != subtotal:
            raise ValueError(
                f"totals.subtotal_cents={self.totals.subtotal_cents} ≠ "
                f"sum(extended_cents)={subtotal}"
            )

        # tax_cents = half-up on (subtotal × ppm).
        expected_tax = tax_cents_of(subtotal, self.totals.tax_rate_ppm)
        if self.totals.tax_cents != expected_tax:
            raise ValueError(
                f"totals.tax_cents={self.totals.tax_cents} ≠ "
                f"(subtotal_cents*tax_rate_ppm + 500_000)//1_000_000={expected_tax}"
            )

        # total_cents = subtotal + tax.
        if self.totals.total_cents != self.totals.subtotal_cents + self.totals.tax_cents:
            raise ValueError(
                f"totals.total_cents={self.totals.total_cents} ≠ "
                f"subtotal+tax={self.totals.subtotal_cents + self.totals.tax_cents}"
            )

        # priced_lines = count of lines with extended_cents set.
        priced = sum(1 for line in self.lines if line.extended_cents is not None)
        if self.totals.priced_lines != priced:
            raise ValueError(
                f"totals.priced_lines={self.totals.priced_lines} ≠ "
                f"count(extended_cents is not None)={priced}"
            )

        # open_flags = count of lines with review.state='open'.
        open_flags = sum(1 for line in self.lines if line.review.state == ReviewStateT.open)
        if self.totals.open_flags != open_flags:
            raise ValueError(
                f"totals.open_flags={self.totals.open_flags} ≠ "
                f"count(review.state=='open')={open_flags}"
            )

        return self
