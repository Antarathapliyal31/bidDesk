"""Pricing verify (SPEC §8.3) — code decides, model proposes.

Runs after the pricing tool loop. For each submitted proposal:
  1. `item_id` must be in the request; missing → status=`failed`.
     Extras logged + ignored. `max_steps_exceeded` items (in
     `partial_item_ids`) fail with that verify_note.
  2. **Candidate-set check** (Antara's addition #2): the submitted
     SKU must be one that `query_catalog` returned for THIS item
     during THIS loop. If not, raise `CandidateSetViolation` →
     converted to ValidationError upstream so the model gets one
     structured-feedback retry. (Different kind of failure than
     "SKU didn't exist in the DB" — that one's a quiet
     `hallucinated_sku` no_match, per SPEC §8.3 step 2.)
  3. SKU must exist and be active; else `no_match` +
     `hallucinated_sku`.
  4. **Price, sale_unit, name, on_hand** copied from the DB row.
  5. Unit conversion via `catalog/units.convert_item_to_sale_qty`.
  6. Sanity range check on `sale_qty` — out of range adds
     `sanity_range` to verify_notes (never auto-changes the value).
  7. Attribute mismatches: each `RequirementSpec` is compared to
     the product column; mismatches recorded.
  8. Shortfall + transfers (via `transfers.transfer_options_for`).
  9. Confidence via `confidence.compute_confidence`.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.agents.pricing.confidence import ConfidenceInputs, compute_confidence
from app.agents.pricing.extraction import PricingLoopOutcome
from app.agents.pricing.length_picker import LengthPick, pick_best_waste
from app.agents.pricing.transfers import TransferRow, transfer_options_for
from app.catalog.semantic_layer import SemanticBundle
from app.catalog.units import (
    ConvertResult,
    Unit,
    convert_item_to_sale_qty,
    normalize_unit,
    pint_convert_to,
)
from app.contracts.common import ConfidenceLevel, Count, MatchStatus
from app.contracts.pricing import MatchAssumption, PricedLineView
from app.contracts.pricing_agent import (
    MatchProposal,
    PricingItemRequest,
    PricingLineResult,
    RequirementSpec,
)


class CandidateSetViolation(Exception):
    """Raised on the FIRST attempt when any proposal submits a SKU
    that `query_catalog` didn't return for its item. Converted to
    ValidationError upstream so delegate retries with feedback.

    On the RETRY attempt (`is_retry=True`), verify does NOT raise;
    the offending items are soft-failed and surfaced in
    `PricingResult.item_errors`, with `status='partial'`.
    """

    def __init__(self, item_id: str, submitted_sku: str, candidates: set[str]) -> None:
        self.item_id = item_id
        self.submitted_sku = submitted_sku
        self.candidates = sorted(candidates)
        super().__init__(
            f"item_id={item_id}: submitted sku={submitted_sku!r} was not returned by "
            f"query_catalog in this run (candidates: {self.candidates[:8]})"
        )


class MissingQueryViolation(Exception):
    """Raised on the FIRST attempt when the model submits a
    MatchProposal (with sku=None OR a sku) for an item it never
    queried. `sku=None` without first searching is a protocol
    violation, not a legitimate `no_match` — the agent is
    supposed to search before answering. Message tells the model
    exactly which items to re-query.

    On the RETRY attempt, the offending items are soft-failed
    with status='failed' + verify_note='no_query' and surfaced
    as `ItemError` so the whole batch isn't lost.
    """

    def __init__(self, item_ids: list[str]) -> None:
        self.item_ids = list(item_ids)
        joined = ", ".join(repr(i) for i in self.item_ids)
        super().__init__(
            f"submitted a match without first calling query_catalog "
            f"for these item_ids: {joined}. Search for each listed "
            f"item_id first, then submit a SKU from its results."
        )


# ---------------------------------------------------------------------------
# Quantity parsing (local to verify — analyst's parser uses float)
# ---------------------------------------------------------------------------


_FRAC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _item_total_ft(
    *,
    item_qty: Decimal | None,
    unit_raw: str | None,
    pint_convertible: bool,
) -> Decimal | None:
    """Return the item quantity normalised to LF (feet) when the
    unit resolves to the length dimension. None when the item
    isn't in linear units — the length picker skips it.

    LF: direct passthrough.
    yd / ft / meter / etc. via pint allowlist: converted to ft.
    Everything else (SF, SQ, SHT, BOX, etc.): None.
    """

    if item_qty is None or item_qty <= 0 or not unit_raw:
        return None
    normalized = normalize_unit(unit_raw)
    if normalized.unit == Unit.LF:
        return item_qty
    if normalized.unit is not None:
        return None  # resolved to a non-length unit
    if pint_convertible and normalized.pint_token is not None:
        converted = pint_convert_to(item_qty, normalized.pint_token, "LF")
        return converted
    return None


def _fmt_ft(x: Decimal) -> str:
    """Compact Decimal for display in conversion_path / reasons."""

    s = str(x.quantize(Decimal("0.01")))
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _parse_qty_decimal(text: str | None) -> Decimal | None:
    """Parse '1,200', '3.5', '1 1/2' → Decimal. None if unparsable."""

    if text is None:
        return None
    stripped = text.replace(",", "").strip()
    if not stripped:
        return None
    if " " in stripped and "/" in stripped:
        whole, _, frac = stripped.partition(" ")
        m = _FRAC_RE.fullmatch(frac.strip())
        if m is None:
            return None
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return None
        try:
            return Decimal(whole) + Decimal(num) / Decimal(den)
        except InvalidOperation:
            return None
    m = _FRAC_RE.fullmatch(stripped)
    if m is not None:
        num, den = int(m.group(1)), int(m.group(2))
        if den == 0:
            return None
        return Decimal(num) / Decimal(den)
    try:
        return Decimal(stripped)
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _fetch_product(sku: str, *, catalog_db_path: Path, branch_id: str) -> dict[str, Any] | None:
    """Read one product row + its on_hand at `branch_id`. Returns
    None if the SKU is missing or inactive."""

    conn = sqlite3.connect(f"file:{catalog_db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT p.*, s.on_hand "
            "FROM products p LEFT JOIN stock s "
            "  ON s.sku = p.sku AND s.branch_id = ? "
            "WHERE p.sku = ? AND p.active = 1",
            [branch_id, sku],
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Attribute matching
# ---------------------------------------------------------------------------


def _requirement_mismatches(
    requirements: list[RequirementSpec], product: dict[str, Any]
) -> list[str]:
    """Return a list of human-readable mismatch strings. A
    requirement whose value differs from the catalog column
    produces one entry. `moisture_resistant` stored as 0/1 is
    normalised to bool for the comparison."""

    out: list[str] = []
    for r in requirements:
        if r.field not in product:
            out.append(f"{r.field}: requirement not a product column")
            continue
        catalog_value: Any = product[r.field]
        req_value: Any = r.value
        if isinstance(req_value, bool):
            catalog_value = bool(catalog_value) if catalog_value is not None else None
        if catalog_value != req_value:
            out.append(f"{r.field}: requested {req_value!r}, catalog has {catalog_value!r}")
    return out


# ---------------------------------------------------------------------------
# Sanity check
# ---------------------------------------------------------------------------


def _sanity_ok(
    bundle: SemanticBundle, product: dict[str, Any], sale_qty: int | None
) -> tuple[bool, int | None]:
    """Return (ok, max_qty). `ok=True` when the line passes or the
    range isn't configured; `max_qty` is populated when a range
    exists so the caller can message with the exact limit."""

    if sale_qty is None:
        return True, None
    category = product.get("category")
    sale_unit = product.get("sale_unit")
    if not category or not sale_unit:
        return True, None
    entries = bundle.sanity_ranges.get(category, [])
    for entry in entries:
        if entry.unit == sale_unit:
            return sale_qty <= entry.max_qty, entry.max_qty
    return True, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyOutcome:
    """Result of `verify_batch`. `results` is one entry per
    requested item; `candidate_set_failures` carries the
    `(item_id, submitted_sku)` pairs we soft-failed on the retry
    pass. `handler.py` folds those into `PricingResult.item_errors`.
    """

    results: list[PricingLineResult]
    candidate_set_failures: list[tuple[str, str]]


def verify_batch(
    outcome: PricingLoopOutcome,
    *,
    request_items: list[PricingItemRequest],
    bundle: SemanticBundle,
    catalog_db_path: Path,
    branch_id: str,
    is_retry: bool = False,
) -> VerifyOutcome:
    """Build a `PricingLineResult` per requested item.

    On the FIRST attempt (`is_retry=False`), raises
    `CandidateSetViolation` the moment any proposal submits a SKU
    not in its item's candidate set — delegate converts this to a
    retry with feedback.

    On the retry pass (`is_retry=True`), does NOT raise; offending
    items are soft-failed with status='failed' + verify_note
    'candidate_set', and the failed `(item_id, sku)` pairs are
    returned so the handler can build `PricingResult.item_errors`.
    """

    items_by_id = {req.item.id: req for req in request_items}
    proposals_by_id: dict[str, MatchProposal] = {}
    if outcome.submit is not None:
        proposals_by_id = {p.item_id: p for p in outcome.submit.results}

    # ---- Guard: did the model query for every item it answered? ----
    # A proposal (any SKU — including sku=None) for an item the
    # model never queried for is a protocol violation. "Submit
    # sku=None without searching" would otherwise silently become
    # a legit-looking no_match; we want the retry path to fire so
    # the model actually does the work. The whole set of missed
    # items goes into ONE error so the retry feedback can list
    # them together.
    no_query_item_ids: list[str] = []
    for prop in proposals_by_id.values():
        if prop.item_id not in items_by_id:
            continue
        if prop.item_id in outcome.partial_item_ids:
            # Not the model's fault — the loop capped before this
            # item got a chance to be queried.
            continue
        if prop.item_id not in outcome.queried_item_ids:
            no_query_item_ids.append(prop.item_id)
    if no_query_item_ids:
        if not is_retry:
            raise MissingQueryViolation(no_query_item_ids)
        # On the retry pass we soft-fail the offending items so
        # the rest of the batch still prices.
        bad_item_ids_no_query = set(no_query_item_ids)
    else:
        bad_item_ids_no_query = set()

    # ---- Guard: candidate-set for non-null submissions ----
    candidate_failures: list[tuple[str, str]] = []
    bad_item_ids: set[str] = set()
    for prop in proposals_by_id.values():
        if prop.sku is None:
            continue
        if prop.item_id not in items_by_id:
            continue
        if prop.item_id in bad_item_ids_no_query:
            # Already soft-failed for a stronger reason.
            continue
        candidates = outcome.candidates_by_item.get(prop.item_id, set())
        if prop.sku not in candidates:
            if not is_retry:
                # First attempt: raise so delegate retries with feedback.
                raise CandidateSetViolation(
                    item_id=prop.item_id,
                    submitted_sku=prop.sku,
                    candidates=candidates,
                )
            # Retry pass: record and keep going.
            candidate_failures.append((prop.item_id, prop.sku))
            bad_item_ids.add(prop.item_id)

    matchable_fields: frozenset[str] = frozenset(
        name for name, fdef in bundle.entities["products"].fields.items() if fdef.matchable
    )
    dimensions = bundle.dimensions

    results: list[PricingLineResult] = []
    for item_id, req in items_by_id.items():
        if item_id in outcome.partial_item_ids:
            results.append(_partial_result(item_id))
            continue
        if item_id in bad_item_ids_no_query:
            results.append(_no_query_result(item_id))
            continue
        if item_id in bad_item_ids:
            results.append(_candidate_set_failed(item_id))
            continue
        prop = proposals_by_id.get(item_id)
        if prop is None:
            results.append(_missing_proposal_result(item_id))
            continue
        results.append(
            _verify_one(
                request=req,
                proposal=prop,
                candidates=outcome.candidate_rows_by_item.get(item_id, []),
                bundle=bundle,
                matchable_fields=matchable_fields,
                dimensions=dimensions,
                catalog_db_path=catalog_db_path,
                branch_id=branch_id,
            )
        )
    return VerifyOutcome(results=results, candidate_set_failures=candidate_failures)


# ---------------------------------------------------------------------------
# Per-item verify
# ---------------------------------------------------------------------------


def _partial_result(item_id: str) -> PricingLineResult:
    """Result for an item the loop never reached (max_steps hit).
    SPEC §8.3 step 1: unpriced items are failed; we keep that
    status but add `max_steps_exceeded` so the trace knows why."""

    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.failed,
        sku=None,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=None,
        confidence_reasons=[],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="failed",
        priced=priced,
        stock=None,
        attribute_mismatches=[],
        assumptions=[],
        verify_notes=["max_steps_exceeded"],
        is_substitute=False,
        substitute_reason=None,
    )


_NOMINAL_LUMBER_RE = re.compile(r"\b\d+x\d+(?:x\d+)?\b")
# Char class covers ASCII apostrophe AND U+2019 right-single-
# quote (PDF extractors emit both for foot marks). Spelled via
# chr(0x2019) so the source file stays ascii-only (ruff RUF001).
_FOOT_MARK = "[" + "'" + chr(0x2019) + "]"
_LENGTH_IN_DESC_RE = re.compile(
    r"\b\d+x\d+\s*x\s*\d+"  # 2x6x16
    + rf"|\bx\s*\d+(?:{_FOOT_MARK}|\s*ft\b|\s*')"  # x 16' or x 16 ft
    + rf"|\b\d+(?:{_FOOT_MARK}|\s*ft\b)\b"  # 16' or 16 ft anywhere
)

# Keywords that mark an item as FRAMING LUMBER (length-required).
# Must match at a word boundary; case-insensitive on the caller side.
_LUMBER_KEYWORDS: tuple[str, ...] = (
    "stud",
    "joist",
    "rafter",
    "ledger",
    "beam",
    "header",
    "sill",
    "blocking",
    "cripple",
    "truss",
    "rim",
    "band",
    "plate",  # "sill plate", "top plate"
    "post",
    "lumber",
    "framing",
)
_LUMBER_KEYWORD_RE = re.compile(r"\b(?:" + "|".join(_LUMBER_KEYWORDS) + r")s?\b")

# Keywords that DISQUALIFY an item from the length-missing guard —
# they're hardware/fasteners that happen to carry a nominal_size
# in their name (e.g. "Joist hangers 2x8", "Hurricane ties 2x6").
_HARDWARE_KEYWORDS: tuple[str, ...] = (
    "hanger",
    "nail",
    "screw",
    "bolt",
    "bracket",
    "anchor",
    "strap",
    "connector",
    "fastener",
    "tie",
    "clip",
)
_HARDWARE_KEYWORD_RE = re.compile(r"\b(?:" + "|".join(_HARDWARE_KEYWORDS) + r")s?\b")


def needs_length_but_missing(item: Any, requirements: list[RequirementSpec]) -> bool:
    """True when the item is piece-counted framing lumber (nominal
    size + a lumber keyword like 'ledger' / 'joist' / 'stud' in
    the description, unit resolves to EA) but no length is stated
    in the description OR requirements. Code skips such items
    so the model never sees them — the estimator fills in the
    length as a question.

    Hardware with a nominal_size in its name ("Joist hangers 2x8",
    "Hurricane ties 2x6") is disqualified via `_HARDWARE_KEYWORDS`.
    """

    if not item.unit_raw:
        return False
    normalized = normalize_unit(item.unit_raw)
    if normalized.unit != Unit.EA:
        return False
    desc = (item.description or "").lower()
    if not _NOMINAL_LUMBER_RE.search(desc):
        return False
    if _LENGTH_IN_DESC_RE.search(desc):
        return False
    if _HARDWARE_KEYWORD_RE.search(desc):
        return False
    if not _LUMBER_KEYWORD_RE.search(desc):
        # Nominal size but no lumber or hardware keyword — leave
        # the decision to the agent + its query.
        return False
    return all(r.field != "length_ft" for r in requirements)


def skipped_length_missing_result(item_id: str) -> PricingLineResult:
    """Status='skipped' with verify_note 'length_missing'. Mirrors
    `skipped_no_quantity_result` — same shape, different reason."""

    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.skipped,
        sku=None,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=None,
        confidence_reasons=[],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="skipped",
        priced=priced,
        verify_notes=["length_missing"],
    )


def skipped_no_quantity_result(item_id: str) -> PricingLineResult:
    """Public-ish helper used by the handler: item has no parsed
    quantity, so we never asked the model. Status='skipped'
    (SPEC's distinct terminal) + verify_note 'quantity_missing'.
    The bid assembly path (Phase 5) flags it for the estimator
    to fill in a quantity."""

    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.skipped,
        sku=None,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=None,
        confidence_reasons=[],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="skipped",
        priced=priced,
        verify_notes=["quantity_missing"],
    )


def _no_query_result(item_id: str) -> PricingLineResult:
    """Soft-fail: model submitted a match for this item without
    ever calling query_catalog for its item_id. Only emitted on
    the retry pass — first-attempt violations raise and get a
    feedback retry."""

    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.failed,
        sku=None,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=None,
        confidence_reasons=[],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="failed",
        priced=priced,
        verify_notes=["no_query"],
    )


def _candidate_set_failed(item_id: str) -> PricingLineResult:
    """Soft-fail result for an item whose SKU was never returned
    by query_catalog even after the retry attempt. Status='failed'
    with the reason in `verify_notes` so the trace is complete."""

    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.failed,
        sku=None,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=None,
        confidence_reasons=[],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="failed",
        priced=priced,
        verify_notes=["candidate_set"],
    )


def _missing_proposal_result(item_id: str) -> PricingLineResult:
    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.failed,
        sku=None,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=None,
        confidence_reasons=[],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="failed",
        priced=priced,
    )


def _hallucinated_sku_result(
    item_id: str,
    *,
    submitted_sku: str,
    assumptions: list[MatchAssumption],
) -> PricingLineResult:
    """SKU wasn't in the catalog at fetch time. Status=failed +
    verify_note `hallucinated_sku`; the handler folds this into
    `PricingResult.item_errors` as `kind='hallucinated_sku'` so
    the batch surfaces as partial (never `no_match`)."""

    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.failed,
        sku=submitted_sku,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=ConfidenceLevel.low,
        confidence_reasons=["hallucinated_sku"],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="failed",
        priced=priced,
        assumptions=assumptions,
        verify_notes=["hallucinated_sku"],
    )


def _no_match_result(
    item_id: str,
    *,
    searched_sku: str | None,
    note: str | None,
    assumptions: list[MatchAssumption],
) -> PricingLineResult:
    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.no_match,
        sku=searched_sku,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=ConfidenceLevel.low,
        confidence_reasons=[note] if note else [],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="no_match",
        priced=priced,
        assumptions=assumptions,
        verify_notes=[note] if note else [],
    )


def _needs_review_result(
    item_id: str,
    *,
    candidates: list[dict[str, Any]],
    assumptions: list[MatchAssumption],
) -> PricingLineResult:
    """Model submitted sku=None but query_catalog returned
    candidates for this item. Promote to `needs_review` — the
    estimator sees the question + the candidate SKUs so they can
    pick. Never `no_match` when the catalog actually had options.
    """

    sample = [str(c.get("sku")) for c in candidates if c.get("sku")][:5]
    reason = (
        f"candidates returned by query_catalog ({len(candidates)}): "
        f"{sample}; agent did not pick one"
    )
    priced = PricedLineView(
        item_id=item_id,
        status=MatchStatus.needs_review,
        sku=None,
        product_name=None,
        sale_qty=None,
        sale_unit=None,
        unit_price_cents=None,
        extended_cents=None,
        conversion_path=None,
        confidence=ConfidenceLevel.low,
        confidence_reasons=[reason],
        shortfall=None,
    )
    return PricingLineResult(
        item_id=item_id,
        status="needs_review",
        priced=priced,
        assumptions=assumptions,
        verify_notes=["candidates_available"],
    )


def _verify_one(
    *,
    request: PricingItemRequest,
    proposal: MatchProposal,
    candidates: list[dict[str, Any]],
    bundle: SemanticBundle,
    matchable_fields: frozenset[str],
    dimensions: list[Any],
    catalog_db_path: Path,
    branch_id: str,
) -> PricingLineResult:
    item = request.item
    assumptions = list(proposal.assumptions)

    # ---- No match (sku=None) ----
    # If the catalog had candidates for this item, promote to
    # `needs_review` — the model is saying "I don't know which
    # one" but there ARE options. Only genuine "catalog had
    # nothing" cases stay as `no_match`.
    if proposal.sku is None:
        if candidates:
            return _needs_review_result(
                item_id=item.id, candidates=candidates, assumptions=assumptions
            )
        return _no_match_result(
            item_id=item.id, searched_sku=None, note=None, assumptions=assumptions
        )

    # ---- Length-picker pre-step (DECISIONS #24) ----
    # When the item unit resolves to LF (direct or via pint),
    # code picks the best-waste stock length from the candidate
    # set within the submitted SKU's family. The model picks the
    # family; code picks the length.
    picked_sku = proposal.sku
    length_pick_result: LengthPick | None = None
    item_qty = _parse_qty_decimal(item.quantity_text)
    unit_pint_convertible = "unit_pint_convertible" in item.notes
    total_ft = _item_total_ft(
        item_qty=item_qty,
        unit_raw=item.unit_raw,
        pint_convertible=unit_pint_convertible,
    )
    if total_ft is not None and candidates:
        result = pick_best_waste(total_ft, submitted_sku=proposal.sku, candidates=candidates)
        if isinstance(result, LengthPick):
            length_pick_result = result
            if result.sku != proposal.sku:
                picked_sku = result.sku
                # Record the code-side assumption transparently.
                assumptions.append(
                    MatchAssumption(
                        field="length_ft",
                        value=result.length_ft,
                        reason=(
                            f"code picked stock length={result.length_ft} ft for "
                            f"total {_fmt_ft(total_ft)} LF "
                            f"(pieces={result.pieces}, waste={_fmt_ft(result.waste_ft)} ft)"
                        ),
                    )
                )

    product = _fetch_product(picked_sku, catalog_db_path=catalog_db_path, branch_id=branch_id)
    if product is None:
        return _hallucinated_sku_result(
            item_id=item.id,
            submitted_sku=picked_sku,
            assumptions=assumptions,
        )

    # ---- Attribute mismatches (SPEC §8.3 step 6) ----
    attr_mismatches = _requirement_mismatches(request.requirements, product)

    # ---- Unit conversion (SPEC §8.3 step 4) ----
    read_via = "text"  # ItemView.source carries read_via, default text
    if hasattr(item.source, "read_via") and item.source.read_via is not None:
        read_via = str(item.source.read_via)

    convert: ConvertResult | None
    if item_qty is None or item_qty <= 0:
        convert = ConvertResult(
            sale_qty=None,
            conversion_path=None,
            confidence_cap=None,
            note="unit_conversion_unavailable",
        )
    else:
        norm = normalize_unit(item.unit_raw)
        item_unit: Unit | None = norm.unit
        convert = convert_item_to_sale_qty(
            item_qty=item_qty,
            item_unit=item_unit,
            unit_raw=item.unit_raw,
            pint_convertible=unit_pint_convertible,
            product=product,
            dimensions=dimensions,
        )

    verify_notes: list[str] = []
    if convert.note == "unit_conversion_unavailable":
        verify_notes.append("unit_conversion_unavailable")
    if convert.note == "assumed_ea_from_countless_qty":
        # DECISIONS F14: analyst dropped the unit on a parenthesised-
        # count quote; code treated the extracted number as a piece
        # count. Record a MatchAssumption so the estimator sees it's
        # inferred, not measured. The medium-confidence cap in
        # `convert.confidence_cap` keeps the line out of "high" but
        # — per the decision — this note is NOT in the open-flag
        # review-reason set (SPEC §9.3 bullet 3), so the line stays
        # closed unless something else flags it.
        verify_notes.append("assumed_ea_from_countless_qty")
        assumptions.append(
            MatchAssumption(
                field="sale_unit",
                value="EA",
                reason=(
                    f"source unit missing; treated quantity {convert.sale_qty} "
                    f"as a piece count because the SKU is sold by the piece"
                ),
            )
        )
    if length_pick_result is not None and length_pick_result.sku != proposal.sku:
        # The model's family stood; code picked the length. The
        # conversion_path from `convert_item_to_sale_qty` already
        # shows "LF → N EA (length_ft = X)"; this note tells the
        # bid-assembly layer that the length was chosen by code.
        verify_notes.append("code_chose_length")

    # ---- Sanity range (SPEC §8.3 step 5) ----
    sanity_ok, max_qty = _sanity_ok(bundle, product, convert.sale_qty)
    if not sanity_ok:
        verify_notes.append(f"sanity_range:{max_qty}")

    # ---- Prices + stock (SPEC §8.3 step 3) ----
    unit_price_cents = int(product["unit_price_cents"])
    sale_unit = str(product["sale_unit"])
    on_hand_raw = product.get("on_hand")
    on_hand = int(on_hand_raw) if on_hand_raw is not None else 0

    sale_qty = convert.sale_qty
    extended_cents = unit_price_cents * sale_qty if sale_qty is not None else None
    shortfall = None if sale_qty is None else max(sale_qty - on_hand, 0)

    # ---- Transfers (SPEC §8.3 step 7) ----
    # Always run against the picked SKU (may differ from the
    # model's submission when the length picker swapped the
    # length_ft within the same family).
    transfers: list[TransferRow] = []
    if shortfall is not None and shortfall > 0:
        transfers = transfer_options_for(
            picked_sku,
            shortfall=shortfall,
            home_branch_id=branch_id,
            catalog_db_path=catalog_db_path,
        )

    # ---- Confidence (SPEC §8.3 step 8) ----
    unit_via_pint = convert.confidence_cap == "medium" and convert.note == "unit_from_pint"
    unit_assumed_ea = convert.note == "assumed_ea_from_countless_qty"
    is_ambiguous = bool(proposal.ambiguous and proposal.ambiguous.is_ambiguous)
    if is_ambiguous and proposal.ambiguous is not None:
        verify_notes.append("ambiguous_pick")
    confidence_result = compute_confidence(
        ConfidenceInputs(
            is_substitute=proposal.is_substitute,
            attribute_mismatches=attr_mismatches,
            assumptions=assumptions,
            matchable_fields=matchable_fields,
            item_verified=item.source.verified,
            read_via=read_via,
            unit_via_pint=unit_via_pint,
            unit_assumed_ea_from_countless_qty=unit_assumed_ea,
            is_ambiguous=is_ambiguous,
            ambiguous_reason=(proposal.ambiguous.reason if proposal.ambiguous else None),
        )
    )

    status_str: str
    match_status: MatchStatus
    if proposal.is_substitute:
        status_str = "substitute"
        match_status = MatchStatus.substitute
    else:
        status_str = "matched"
        match_status = MatchStatus.matched

    priced = PricedLineView(
        item_id=item.id,
        status=match_status,
        sku=picked_sku,
        product_name=str(product.get("name") or ""),
        sale_qty=(str(sale_qty) if sale_qty is not None else None),
        sale_unit=sale_unit,
        unit_price_cents=unit_price_cents,
        extended_cents=extended_cents,
        conversion_path=convert.conversion_path,
        confidence=confidence_result.level,
        confidence_reasons=confidence_result.reasons,
        shortfall=_as_count(shortfall),
    )

    stock = _build_stock_info_dict(on_hand, shortfall, transfers)

    return PricingLineResult(
        item_id=item.id,
        status=status_str,  # type: ignore[arg-type]
        priced=priced,
        stock=stock,
        attribute_mismatches=attr_mismatches,
        assumptions=assumptions,
        verify_notes=verify_notes,
        is_substitute=proposal.is_substitute,
        substitute_reason=proposal.substitute_reason,
    )


def _as_count(v: int | None) -> Count | None:
    """`Count` is an Annotated[int, ...] alias; a plain int is a
    runtime-compatible value. Wraps None passthrough for symmetry."""

    return v  # type: ignore[return-value]


def _build_stock_info_dict(
    on_hand: int, shortfall: int | None, transfers: list[TransferRow]
) -> Any:
    """Return a StockInfo (when shortfall is known) or None.

    Shape kept loose for Phase 4 — the Bid-side `StockInfo`
    contract has invariants (sorted transfer_options, cover flag)
    that we assert in `transfers.py`'s sort. The orchestrator
    (Phase 5) adapts this into the typed contract when it
    assembles the Bid.
    """

    if shortfall is None:
        return None
    return {
        "on_hand": on_hand,
        "shortfall": shortfall,
        "transfer_options": [
            {
                "branch_id": t.branch_id,
                "branch_name": t.branch_name,
                "on_hand": t.on_hand,
                "cover": t.cover,
            }
            for t in transfers
        ],
    }
