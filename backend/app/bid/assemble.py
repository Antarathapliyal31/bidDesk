"""Bid assembly (SPEC §9.3) — code owns every field.

Load the run's items, triage, requirements, latest price_result
per item, and conflicts, then build a validated `Bid`:
  - sections: price → `lines`, labor → `labor`,
    exclusion → `exclusions`, unreadable → `unreadable`,
    ambiguous/untriaged → `lines` with open review.
  - review is `open` per SPEC §9.3 step 3 (incl. `needs_review`
    status + `not_priced` for items with no price result).
  - totals per §13: `extended = round_half_up(sale_qty x price)`;
    tax half-up on single subtotal.
  - revision 0 = `kind='agent'`, immutable (trigger-enforced); a
    draft copy `revision=1, kind='draft'` is persisted alongside.
  - fallback path: `generated_by='fallback'`, `summary` from a
    code template, `partial_reasons` filled, run goes `partial`.
  - `no_materials` outcome: zero sections → no bid, run
    `completed` with that outcome.

No I/O from the model — every byte here is server-authored or
copied from the catalog row.
"""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.bid.totals import (
    count_open_flags,
    count_priced,
    extended_cents_of,
    subtotal_cents,
    tax_cents_of,
)
from app.contracts.bid import (
    AssumptionView,
    Bid,
    BidLine,
    BranchInfo,
    ConflictSide,
    ConflictView,
    ContractorQuestionBasis,
    ContractorQuestionView,
    ExclusionLine,
    LaborLine,
    Origins,
    ProjectInfo,
    RequirementView,
    ReviewReason,
    ReviewState,
    Totals,
    UnreadableLine,
)
from app.contracts.common import (
    BidKind,
    ConfidenceLevel,
    ConflictType,
    GeneratedBy,
    MatchStatus,
    Origin,
    ReviewStateT,
    SourceRef,
)
from app.contracts.pricing import (
    MatchAssumption,
    MatchInfo,
    NextReceipt,
    StockInfo,
    TransferOption,
    sort_transfer_options,
)

# ---------------------------------------------------------------------------
# submit_bid narrative validation (SPEC §9.2)
# ---------------------------------------------------------------------------

# Case-insensitive word-boundary — Antara's spec for Phase 5a
# plus the SPEC §9.2 base list. `firm price` / `will be
# delivered` are multi-word; `\b...\b` handles them because the
# space is a word boundary.
_FORBIDDEN_WORDS = (
    "guarantee",
    "guaranteed",
    "guarantees",
    "warranty",
    "firm price",
    "in stock",
    "will be delivered",
    "promise",
    "promises",
    "promised",
)
_FORBIDDEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in _FORBIDDEN_WORDS) + r")\b",
    re.IGNORECASE,
)
_CURRENCY_RE = re.compile(r"\$\s?\d")


class SummaryValidationError(Exception):
    """Raised by `validate_summary` when `submit_bid.summary`
    violates one of the SPEC §9.2 rules. The orchestrator catches
    this and feeds the message back to the model as a retry — a
    second failure triggers fallback assembly."""


def validate_summary(summary: str) -> None:
    """Reject empty/overlong summaries, currency amounts, and
    commitment phrasing. Mirrors SPEC §9.2 + the Phase-5a words."""

    s = (summary or "").strip()
    if not s:
        raise SummaryValidationError("summary is empty")
    if len(s) > 600:
        raise SummaryValidationError("summary exceeds 600 characters")
    if _CURRENCY_RE.search(s):
        raise SummaryValidationError(
            "summary contains a dollar amount — totals are rendered by the UI"
        )
    hit = _FORBIDDEN_RE.search(s)
    if hit:
        raise SummaryValidationError(
            f"summary contains forbidden commitment wording: {hit.group(0)!r}"
        )


# ---------------------------------------------------------------------------
# Input bundle — everything assembly reads
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssemblyInput:
    """What `build_bid` consumes. The orchestrator builds this
    from repo reads after the loop ends (happy path or fallback).
    Keeping it a dataclass means we can hand assembled test
    fixtures without touching the DB."""

    run_id: str
    branch: BranchInfo
    project: ProjectInfo
    items: list[dict[str, Any]]  # repo.list_items shape
    triage_by_item: dict[str, dict[str, Any]]  # {item_id: triage row}
    requirements_by_item: dict[str, list[dict[str, Any]]]
    price_results_by_item: dict[str, dict[str, Any]]
    conflicts: list[dict[str, Any]]  # repo.list_conflicts shape
    # Narrative, validated (or defaulted on fallback):
    summary: str
    assumptions: list[dict[str, Any]]  # SubmitBidAssumptionInput dicts
    contractor_questions: list[dict[str, Any]]  # SubmitBidQuestionInput dicts
    # Routing (SPEC §9.3 step 6):
    generated_by: GeneratedBy
    partial_reasons: list[str]
    # Fired question-scope gap rules — used to validate basis.ref_id
    # for contractor_questions on the submit_bid path.
    gap_rule_ids: set[str]


@dataclass(frozen=True)
class AssemblyResult:
    """Return from `build_bid`. `agent_bid` + `draft_bid` are
    byte-identical except for `kind` + `revision`; the orchestrator
    persists both. `outcome` drives the `runs.outcome` column."""

    agent_bid: Bid | None  # None on `no_materials`
    draft_bid: Bid | None  # None on `no_materials`
    outcome: str  # "bid_ready" | "no_materials" | "invalid_input" | "budget_exhausted" | "error"
    outcome_detail: str | None
    rejected_claims: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Section routing
# ---------------------------------------------------------------------------


def _category(item: dict[str, Any], triage_by_item: dict[str, dict[str, Any]]) -> str:
    """The effective category for an item: triage if present,
    otherwise fall through to 'ambiguous' (surfaces as open review)."""

    t = triage_by_item.get(item["id"])
    if t:
        return str(t["category"])
    # Unreadable items from the analyst short-circuit to that section
    # regardless of triage.
    if item["kind"] == "unreadable":
        return "unreadable"
    return "ambiguous"


# ---------------------------------------------------------------------------
# Review reasons
# ---------------------------------------------------------------------------


def _review_reasons_for(
    item: dict[str, Any],
    price: dict[str, Any] | None,
    in_conflict: bool,
) -> list[ReviewReason]:
    """Compute review reasons for one line per SPEC §9.3 step 3.
    Returns [] when the line is clean."""

    reasons: list[ReviewReason] = []
    # Item-side flags
    notes = item.get("notes") or []
    if not item.get("verified", False) and item.get("kind") in ("material", "labor"):
        # Only flag `unverified` on priceable kinds — exclusions
        # with no quote are handled elsewhere.
        reasons.append("unverified")
    if "quantity_not_in_quote" in notes:
        reasons.append("quantity_not_in_quote")
    if "unknown_unit" in notes:
        reasons.append("unknown_unit")
    if in_conflict:
        reasons.append("in_conflict")

    if price is None:
        reasons.append("not_priced")
        return reasons

    status = price.get("status")
    if status in ("no_match", "failed", "timeout"):
        # `needs_review` is handled below as a dedicated flag.
        reasons.append(status)  # type: ignore[arg-type]
    if status == "needs_review":
        reasons.append("not_priced")
    if status == "substitute":
        reasons.append("substitute")
    if (price.get("shortfall") or 0) > 0:
        reasons.append("shortfall")
    conf = price.get("confidence")
    if conf == ConfidenceLevel.low.value:
        reasons.append("low_confidence")
    verify_notes = price.get("verify_notes") or []
    if any(n.startswith("sanity_range") for n in verify_notes):
        reasons.append("sanity_range")
    return reasons


# ---------------------------------------------------------------------------
# Bid-line builders
# ---------------------------------------------------------------------------


def _requirement_view(req_row: dict[str, Any]) -> RequirementView:
    """Rehydrate a `RequirementView` from a repo row. `value_json`
    is either a scalar or a list — the shape discriminates."""

    parsed = json.loads(req_row["value_json"])
    source = SourceRef(
        page=req_row.get("source_page"),
        quote=req_row.get("source_quote"),
        read_via=req_row.get("read_via"),
        verified=bool(req_row.get("quote_verified")),
    )
    if isinstance(parsed, list):
        return RequirementView(
            field=req_row["field"],
            value_in=parsed,
            reason=req_row["reason"],
            source=source,
        )
    return RequirementView(
        field=req_row["field"],
        value=parsed,
        reason=req_row["reason"],
        source=source,
    )


def _source_ref_from_item(item: dict[str, Any]) -> SourceRef:
    return SourceRef(
        page=item.get("source_page"),
        quote=item.get("source_quote"),
        read_via=item.get("read_via"),
        verified=bool(item.get("verified", False)),
    )


def _stock_info(price: dict[str, Any]) -> StockInfo | None:
    on_hand = price.get("branch_on_hand")
    shortfall = price.get("shortfall")
    if on_hand is None and shortfall is None:
        return None
    transfers_raw = price.get("transfer_options") or []
    options = []
    for t in transfers_raw:
        if not isinstance(t, dict):
            continue
        # Each stock-level row from pricing/transfers.py uses
        # `on_hand` + `cover` (None=full, "N of M"=partial). Convert
        # into the API contract's TransferOption shape.
        avail = int(t.get("on_hand") or 0)
        if avail <= 0:
            continue
        cover = t.get("cover")
        covers = cover is None  # None → full-cover marker
        options.append(
            TransferOption(
                branch_id=str(t["branch_id"]),
                branch_name=str(t.get("branch_name") or ""),
                available_qty=avail,
                covers_shortfall=covers,
            )
        )
    options = sort_transfer_options(options)
    shortfall_int = int(shortfall or 0)
    # StockInfo contract: shortfall == 0 ⇒ transfer_options == [].
    if shortfall_int == 0:
        options = []
    next_receipt: NextReceipt | None = None
    nr = price.get("next_receipt")
    if isinstance(nr, dict) and nr.get("date") and nr.get("qty"):
        next_receipt = NextReceipt(
            date=date.fromisoformat(str(nr["date"])),
            qty=int(nr["qty"]),
        )
    return StockInfo(
        on_hand=int(on_hand or 0),
        shortfall=shortfall_int,
        next_receipt=next_receipt,
        transfer_options=options,
    )


def _match_info(price: dict[str, Any]) -> MatchInfo:
    status_str = str(price.get("status") or "failed")
    status = MatchStatus(status_str)
    confidence: ConfidenceLevel | None = None
    conf_raw = price.get("confidence")
    if conf_raw:
        confidence = ConfidenceLevel(conf_raw)
    assumptions = [
        MatchAssumption(**a) for a in (price.get("assumptions") or []) if isinstance(a, dict)
    ]
    return MatchInfo(
        status=status,
        confidence=confidence,
        confidence_reasons=list(price.get("confidence_reasons") or []),
        assumptions=assumptions,
        is_substitute=bool(price.get("is_substitute")),
        substitute_reason=price.get("substitute_reason"),
        verify_notes=list(price.get("verify_notes") or []),
    )


def _line_from(
    item: dict[str, Any],
    requirements: list[dict[str, Any]],
    price: dict[str, Any] | None,
    in_conflict: bool,
) -> BidLine:
    """Build a BidLine. Pricing-side fields come straight from
    the price_results row (which was authored by code, not the
    model). The BidLine validator re-asserts the identities."""

    line_id = f"line_{item['id']}"
    reasons = _review_reasons_for(item, price, in_conflict)
    review = ReviewState(
        state=ReviewStateT.open if reasons else ReviewStateT.none,
        reasons=reasons,
        resolved_by=None,
    )

    requirements_view = [_requirement_view(r) for r in requirements]

    if price is None:
        # No price row → unpriced line.
        return BidLine(
            line_id=line_id,
            item_id=item["id"],
            description=item["description"],
            source=_source_ref_from_item(item),
            requested_quantity_text=item.get("quantity_text"),
            requested_unit_raw=item.get("unit_raw"),
            sku=None,
            product_name=None,
            sale_qty=None,
            sale_unit=None,
            conversion_path=None,
            unit_price_cents=None,
            catalog_unit_price_cents=None,
            extended_cents=None,
            stock=None,
            match=None,
            requirements=requirements_view,
            review=review,
            origins=Origins(
                quantity=Origin.agent,
                unit_price=Origin.agent,
                sku=Origin.agent,
                description=Origin.agent,
            ),
        )

    sku = price.get("sku")
    unit_price_cents = price.get("unit_price_cents")
    sale_qty_str = price.get("sale_qty")
    sale_qty_dec: Decimal | None = None
    extended = None
    if sale_qty_str is not None and unit_price_cents is not None:
        sale_qty_dec = Decimal(str(sale_qty_str))
        extended = extended_cents_of(sale_qty_dec, int(unit_price_cents))

    return BidLine(
        line_id=line_id,
        item_id=item["id"],
        description=item["description"],
        source=_source_ref_from_item(item),
        requested_quantity_text=item.get("quantity_text"),
        requested_unit_raw=item.get("unit_raw"),
        sku=sku,
        product_name=price.get("product_name"),
        sale_qty=sale_qty_dec,
        sale_unit=price.get("sale_unit"),
        conversion_path=price.get("conversion_path"),
        unit_price_cents=unit_price_cents,
        catalog_unit_price_cents=unit_price_cents,  # agent-origin ⇒ same
        extended_cents=extended,
        stock=_stock_info(price),
        match=_match_info(price),
        requirements=requirements_view,
        review=review,
        origins=Origins(
            quantity=Origin.agent,
            unit_price=Origin.agent,
            sku=Origin.agent,
            description=Origin.agent,
        ),
    )


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------


def _conflict_views(conflicts: list[dict[str, Any]]) -> list[ConflictView]:
    out: list[ConflictView] = []
    for c in conflicts:
        sa_src = c["side_a"].get("source") or c["side_a"]
        sb_src = c["side_b"].get("source") or c["side_b"]
        side_a = ConflictSide(
            item_id=c["side_a"].get("item_id"),
            source=SourceRef(
                **{k: sa_src.get(k) for k in ("page", "quote", "read_via", "verified")}
            ),
        )
        side_b = ConflictSide(
            item_id=c["side_b"].get("item_id"),
            source=SourceRef(
                **{k: sb_src.get(k) for k in ("page", "quote", "read_via", "verified")}
            ),
        )
        out.append(
            ConflictView(
                id=c["id"],
                type=ConflictType(c["type"]),
                side_a=side_a,
                side_b=side_b,
                explanation=c["explanation"],
            )
        )
    return out


# ---------------------------------------------------------------------------
# Assumption + contractor_question link validation
# ---------------------------------------------------------------------------


def _valid_assumptions(
    inputs: list[dict[str, Any]],
    items_by_id: dict[str, dict[str, Any]],
    price_results_by_item: dict[str, dict[str, Any]],
    requirements_by_item: dict[str, list[dict[str, Any]]],
) -> tuple[list[AssumptionView], list[dict[str, Any]]]:
    """Return (valid, rejected). An assumption is valid iff:
      - its item exists, AND
      - it links to a recorded pricing assumption OR a requirement
        on that item (SPEC §9.2).
    Everything else drops to `rejected_claims`."""

    valid: list[AssumptionView] = []
    rejected: list[dict[str, Any]] = []
    for a in inputs:
        iid = a.get("item_id")
        text = a.get("text") or ""
        if not isinstance(iid, str) or iid not in items_by_id:
            rejected.append({"kind": "assumption", "payload": a, "reason": "unknown_item_id"})
            continue
        price = price_results_by_item.get(iid)
        pricing_assumptions = (price or {}).get("assumptions") or []
        requirements = requirements_by_item.get(iid) or []
        if not pricing_assumptions and not requirements:
            rejected.append(
                {
                    "kind": "assumption",
                    "payload": a,
                    "reason": "no_recorded_assumption_or_requirement",
                }
            )
            continue
        try:
            valid.append(AssumptionView(item_id=iid, text=text))
        except Exception as exc:
            rejected.append({"kind": "assumption", "payload": a, "reason": f"validation: {exc}"})
    return valid, rejected


def _valid_questions(
    inputs: list[dict[str, Any]],
    *,
    gap_rule_ids: set[str],
    conflict_ids: set[str],
    flagged_line_ids: set[str],
) -> tuple[list[ContractorQuestionView], list[dict[str, Any]]]:
    """Return (valid, rejected). `basis.ref_id` must exist in the
    appropriate set for `basis.kind`."""

    valid: list[ContractorQuestionView] = []
    rejected: list[dict[str, Any]] = []
    for q in inputs:
        basis = q.get("basis") or {}
        kind = basis.get("kind")
        ref_id = basis.get("ref_id")
        ok = False
        if (
            (kind == "gap_rule" and ref_id in gap_rule_ids)
            or (kind == "conflict" and ref_id in conflict_ids)
            or (kind == "flagged_line" and ref_id in flagged_line_ids)
        ):
            ok = True
        if not ok:
            rejected.append(
                {"kind": "contractor_question", "payload": q, "reason": "unknown_basis_ref_id"}
            )
            continue
        try:
            valid.append(
                ContractorQuestionView(
                    text=q["text"],
                    basis=ContractorQuestionBasis(kind=kind, ref_id=ref_id),  # type: ignore[arg-type]
                )
            )
        except Exception as exc:
            rejected.append(
                {"kind": "contractor_question", "payload": q, "reason": f"validation: {exc}"}
            )
    return valid, rejected


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_bid(input_: AssemblyInput) -> AssemblyResult:
    """Assemble the Bid (SPEC §9.3). Pure: no I/O. The caller
    persists `agent_bid` + `draft_bid` via `repo.insert_bid`
    inside one `db.write()` transaction."""

    items = list(input_.items)
    items_by_id = {it["id"]: it for it in items}

    # Section routing.
    lines_input: list[dict[str, Any]] = []
    exclusion_items: list[dict[str, Any]] = []
    labor_items: list[dict[str, Any]] = []
    unreadable_items: list[dict[str, Any]] = []
    for it in items:
        cat = _category(it, input_.triage_by_item)
        if cat == "exclusion":
            exclusion_items.append(it)
        elif cat == "labor":
            labor_items.append(it)
        elif cat == "unreadable":
            unreadable_items.append(it)
        elif cat == "ignore_note":
            continue  # intentionally dropped — notes aren't bid lines
        else:
            # price OR ambiguous OR untriaged → lines (ambiguous
            # and untriaged are surfaced open-review).
            lines_input.append(it)

    # No-materials outcome (SPEC §9.3 step 7).
    if not lines_input and not exclusion_items and not labor_items and not unreadable_items:
        return AssemblyResult(
            agent_bid=None,
            draft_bid=None,
            outcome="no_materials",
            outcome_detail="no classifiable items extracted",
            rejected_claims=[],
        )

    # Items flagged by a conflict.
    conflicted_item_ids: set[str] = set()
    for c in input_.conflicts:
        for side in ("side_a", "side_b"):
            iid = c[side].get("item_id")
            if isinstance(iid, str):
                conflicted_item_ids.add(iid)

    # Build BidLines.
    bid_lines: list[BidLine] = []
    for it in lines_input:
        iid = it["id"]
        requirements = input_.requirements_by_item.get(iid) or []
        price = input_.price_results_by_item.get(iid)
        bid_lines.append(
            _line_from(
                it,
                requirements,
                price,
                in_conflict=iid in conflicted_item_ids,
            )
        )

    exclusions: list[ExclusionLine] = []
    for it in exclusion_items:
        exclusions.append(
            ExclusionLine(description=it["description"], source=_source_ref_from_item(it))
        )

    labor: list[LaborLine] = []
    for it in labor_items:
        src = _source_ref_from_item(it)
        # SourceRef with no page/quote is a valid "note" source
        # for labor — leave it attached regardless.
        labor.append(
            LaborLine(
                description=it["description"],
                quantity_text=it.get("quantity_text"),
                unit_raw=it.get("unit_raw"),
                source=src,
            )
        )

    unreadable_lines = [UnreadableLine(description=it["description"]) for it in unreadable_items]

    # Conflicts + rejected_claims.
    conflicts = _conflict_views(input_.conflicts)
    rejected: list[dict[str, Any]] = []

    # Assumption + question links (SPEC §9.2).
    flagged_line_ids = {
        line.line_id for line in bid_lines if line.review.state == ReviewStateT.open
    }
    conflict_ids = {c.id for c in conflicts}
    valid_assumptions, rej_a = _valid_assumptions(
        input_.assumptions,
        items_by_id,
        input_.price_results_by_item,
        input_.requirements_by_item,
    )
    valid_questions, rej_q = _valid_questions(
        input_.contractor_questions,
        gap_rule_ids=input_.gap_rule_ids,
        conflict_ids=conflict_ids,
        flagged_line_ids=flagged_line_ids,
    )
    rejected.extend(rej_a)
    rejected.extend(rej_q)

    # Totals (SPEC §13).
    subtotal = subtotal_cents(bid_lines)
    tax = tax_cents_of(subtotal, input_.branch.tax_rate_ppm)
    totals = Totals(
        subtotal_cents=subtotal,
        tax_rate_ppm=input_.branch.tax_rate_ppm,
        tax_cents=tax,
        total_cents=subtotal + tax,
        priced_lines=count_priced(bid_lines),
        open_flags=count_open_flags(bid_lines),
    )

    summary = (input_.summary or "").strip()
    # Fallback still needs a non-empty summary.
    if not summary:
        summary = _fallback_summary(input_.partial_reasons)

    agent_bid = Bid(
        run_id=input_.run_id,
        kind=BidKind.agent,
        revision=0,
        branch=input_.branch,
        project=input_.project,
        lines=bid_lines,
        exclusions=exclusions,
        labor=labor,
        unreadable=unreadable_lines,
        conflicts=conflicts,
        assumptions=valid_assumptions,
        contractor_questions=valid_questions,
        summary=summary,
        generated_by=input_.generated_by,
        partial_reasons=list(input_.partial_reasons),
        totals=totals,
    )
    # Draft is byte-equal except for kind + revision.
    draft_bid = agent_bid.model_copy(update={"kind": BidKind.draft, "revision": 1})

    return AssemblyResult(
        agent_bid=agent_bid,
        draft_bid=draft_bid,
        outcome="bid_ready",
        outcome_detail=None,
        rejected_claims=rejected,
    )


def _fallback_summary(partial_reasons: list[str]) -> str:
    """Short, deterministic text for the fallback path — SPEC §9.3
    step 6. No currency, no commitments; just states *why* the
    orchestrator didn't submit."""

    if not partial_reasons:
        return (
            "Assembled automatically: the orchestrator did not call "
            "submit_bid. Lines below reflect what was extracted and "
            "priced before assembly took over."
        )
    joined = "; ".join(partial_reasons)
    return (
        f"Assembled automatically: orchestrator did not complete. "
        f"Reasons: {joined}. Review each open flag before finalizing."
    )


# ---------------------------------------------------------------------------
# Persistence helper (used by orchestrator + fallback paths)
# ---------------------------------------------------------------------------


def new_bid_id(kind: BidKind, revision: int, run_id: str | None = None) -> str:
    """Globally-unique bid id for `bids.id` (PRIMARY KEY). The DB
    additionally has `UNIQUE(run_id, kind, revision)`.

    Shape: `bid_<run>_<kind>_<rev>_<nonce>`. The nonce guarantees
    uniqueness across any retry path; the embedded `run_id` keeps
    collisions impossible across concurrent runs that happen to
    pick the same revision number.
    """

    run_part = f"_{run_id}" if run_id else ""
    return f"bid{run_part}_{kind.value}_{revision}_{secrets.token_hex(4)}"


def now_iso() -> str:
    """ISO-8601 UTC string. Separate helper so tests can monkey-
    patch; today's callers pass an explicit `created_at` instead."""

    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
