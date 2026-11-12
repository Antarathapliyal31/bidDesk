"""Pure edit-op application over a `Bid`.

Zero I/O. The API endpoint hands us a `Bid` + a list of
`EditOp`s; this module returns the new `Bid` + an audit list of
the applied ops (for `bid_edits`). The server re-asserts totals
via the Pydantic validator in `contracts/bid.py`.

Policy decisions here (SPEC §9.4):
  - Ops apply in order; any invalid op raises → caller returns
    422 with per-op details and NO ops are applied.
  - `set_unit_price` is `user_overridden`, keeps
    `catalog_unit_price_cents`, requires `override_reason`.
  - `set_qty` leaves origins untouched when the value is
    already an agent price; the quantity is user-set.
  - `set_sku` re-reads the catalog row (price / name / sale_unit
    all refreshed). Done by the API layer since it needs catalog
    DB access; here we accept a `catalog_lookup` callable.
  - `delete_line` drops the line from totals.
  - `add_line` makes a new BidLine with user origins.
  - `accept_line` resolves review without touching values.
  - `apply_proposal` / `dismiss_proposal` are Phase-9 stubs
    today: they validate the line exists and accept the op into
    the audit, but don't yet consume a proposal row.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from app.bid.totals import count_open_flags, count_priced, subtotal_cents, tax_cents_of
from app.contracts.bid import (
    Bid,
    BidLine,
    Origins,
    ReviewState,
    SourceRef,
    Totals,
    extended_cents_of,
)
from app.contracts.common import Origin, ResolvedBy, ReviewStateT
from app.contracts.edits import (
    AcceptLine,
    AddLine,
    ApplyProposal,
    DeleteLine,
    DismissProposal,
    EditRequest,
    SetDescription,
    SetQty,
    SetSku,
    SetUnitPrice,
)


class EditOpError(Exception):
    """Raised when an op cannot be applied (unknown line_id,
    bad Decimal, unknown SKU, etc.). The API renders this as
    HTTP 422 with `details.ops[i]` naming the offending index."""

    def __init__(self, index: int, op_kind: str, message: str) -> None:
        self.index = index
        self.op_kind = op_kind
        self.message = message
        super().__init__(f"op[{index}] {op_kind}: {message}")


CatalogLookup = Callable[[str], dict[str, Any] | None]
"""Return a dict shaped `{sku, product_name, sale_unit,
unit_price_cents, ...}` for a given SKU, or None when the SKU
doesn't exist."""


def apply_edits(
    bid: Bid,
    request: EditRequest,
    *,
    catalog_lookup: CatalogLookup,
    now_iso: str,
) -> tuple[Bid, list[dict[str, Any]]]:
    """Apply `request.ops` over `bid` in order. Returns
    `(new_bid, audit_ops_json)` on success. Raises `EditOpError`
    on the first invalid op — NO ops are applied."""

    _ = now_iso  # reserved for audit row timestamps at the caller
    lines_by_id = {line.line_id: line for line in bid.lines}
    audit: list[dict[str, Any]] = []

    for idx, op in enumerate(request.ops):
        kind = op.op
        if isinstance(op, SetQty):
            line = _require_line(lines_by_id, idx, kind, op.line_id)
            try:
                qty = Decimal(op.sale_qty)
            except InvalidOperation as exc:
                raise EditOpError(idx, kind, f"sale_qty not a Decimal: {op.sale_qty!r}") from exc
            if qty <= 0:
                raise EditOpError(idx, kind, f"sale_qty must be > 0 (got {qty})")
            lines_by_id[op.line_id] = _with_qty(line, qty)
        elif isinstance(op, SetUnitPrice):
            line = _require_line(lines_by_id, idx, kind, op.line_id)
            # EditRequest Pydantic already enforces non-empty
            # override_reason; defense-in-depth:
            if not op.override_reason.strip():
                raise EditOpError(idx, kind, "override_reason is required")
            lines_by_id[op.line_id] = _with_unit_price(
                line, op.unit_price_cents, op.override_reason
            )
        elif isinstance(op, SetSku):
            line = _require_line(lines_by_id, idx, kind, op.line_id)
            prod = catalog_lookup(op.sku)
            if prod is None:
                raise EditOpError(idx, kind, f"sku {op.sku!r} not in catalog")
            lines_by_id[op.line_id] = _with_sku(line, op.sku, prod)
        elif isinstance(op, SetDescription):
            line = _require_line(lines_by_id, idx, kind, op.line_id)
            lines_by_id[op.line_id] = line.model_copy(
                update={
                    "description": op.description,
                    "origins": line.origins.model_copy(
                        update={"description": Origin.user_overridden}
                    ),
                    "review": _resolve_review(line.review, ResolvedBy.edit),
                }
            )
        elif isinstance(op, AcceptLine):
            line = _require_line(lines_by_id, idx, kind, op.line_id)
            lines_by_id[op.line_id] = line.model_copy(
                update={"review": _resolve_review(line.review, ResolvedBy.accept)}
            )
        elif isinstance(op, DeleteLine):
            _require_line(lines_by_id, idx, kind, op.line_id)
            del lines_by_id[op.line_id]
        elif isinstance(op, AddLine):
            try:
                qty = Decimal(op.sale_qty)
            except InvalidOperation as exc:
                raise EditOpError(idx, kind, f"sale_qty not a Decimal: {op.sale_qty!r}") from exc
            if qty <= 0:
                raise EditOpError(idx, kind, f"sale_qty must be > 0 (got {qty})")
            new_line = _new_user_line(
                description=op.description,
                sale_qty=qty,
                sale_unit=op.sale_unit,
                unit_price_cents=op.unit_price_cents,
            )
            lines_by_id[new_line.line_id] = new_line
        elif isinstance(op, (ApplyProposal, DismissProposal)):
            # Validate the line exists; proposal consumption lands
            # with the full /reprice endpoint in Phase 9.
            _require_line(lines_by_id, idx, kind, op.line_id)
        else:  # pragma: no cover — exhaustive discriminator
            raise EditOpError(idx, kind, "unknown op type")

        audit.append(op.model_dump(mode="python"))

    # Recompute totals.
    new_lines = list(lines_by_id.values())
    subtotal = subtotal_cents(new_lines)
    tax = tax_cents_of(subtotal, bid.branch.tax_rate_ppm)
    totals = Totals(
        subtotal_cents=subtotal,
        tax_rate_ppm=bid.branch.tax_rate_ppm,
        tax_cents=tax,
        total_cents=subtotal + tax,
        priced_lines=count_priced(new_lines),
        open_flags=count_open_flags(new_lines),
    )

    new_bid = bid.model_copy(
        update={
            "lines": new_lines,
            "revision": bid.revision + 1,
            "totals": totals,
        }
    )
    return new_bid, audit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_line(by_id: dict[str, BidLine], idx: int, kind: str, line_id: str) -> BidLine:
    line = by_id.get(line_id)
    if line is None:
        raise EditOpError(idx, kind, f"line_id {line_id!r} not in bid")
    return line


def _with_qty(line: BidLine, qty: Decimal) -> BidLine:
    extended = (
        extended_cents_of(qty, line.unit_price_cents) if line.unit_price_cents is not None else None
    )
    return line.model_copy(
        update={
            "sale_qty": qty,
            "extended_cents": extended,
            "origins": line.origins.model_copy(update={"quantity": Origin.user_overridden}),
            "review": _resolve_review(line.review, ResolvedBy.edit),
        }
    )


def _with_unit_price(line: BidLine, cents: int, reason: str) -> BidLine:
    # user_overridden requires catalog_unit_price_cents to exist.
    # If it doesn't, flip to user_filled instead (BidLine
    # validator enforces that).
    origin = (
        Origin.user_overridden if line.catalog_unit_price_cents is not None else Origin.user_filled
    )
    extended = extended_cents_of(line.sale_qty, cents) if line.sale_qty is not None else None
    update: dict[str, Any] = {
        "unit_price_cents": cents,
        "extended_cents": extended,
        "origins": line.origins.model_copy(update={"unit_price": origin}),
        "review": _resolve_review(line.review, ResolvedBy.edit),
    }
    if origin == Origin.user_overridden:
        update["override_reason"] = reason
    else:
        # user_filled: no catalog anchor → override_reason is
        # forbidden by the BidLine validator.
        update["override_reason"] = None
    return line.model_copy(update=update)


def _with_sku(line: BidLine, new_sku: str, prod: dict[str, Any]) -> BidLine:
    # Re-price from the catalog row. The new SKU's price becomes
    # BOTH unit_price_cents AND catalog_unit_price_cents
    # (origin='user_overridden' since the user chose the sku).
    cents = int(prod["unit_price_cents"])
    extended = extended_cents_of(line.sale_qty, cents) if line.sale_qty is not None else None
    return line.model_copy(
        update={
            "sku": new_sku,
            "product_name": str(prod.get("product_name") or prod.get("name") or ""),
            "sale_unit": str(prod.get("sale_unit") or line.sale_unit or ""),
            "unit_price_cents": cents,
            "catalog_unit_price_cents": cents,
            "extended_cents": extended,
            "origins": line.origins.model_copy(update={"sku": Origin.user_overridden}),
            "review": _resolve_review(line.review, ResolvedBy.edit),
        }
    )


def _new_user_line(
    *, description: str, sale_qty: Decimal, sale_unit: str, unit_price_cents: int
) -> BidLine:
    """User-added line (SPEC §9.4 `AddLine`). item_id=None, every
    origin is `user_filled`, no catalog anchor."""

    import secrets as _secrets

    line_id = f"line_user_{_secrets.token_hex(4)}"
    return BidLine(
        line_id=line_id,
        item_id=None,
        description=description,
        source=SourceRef(page=None, quote=None, read_via=None, verified=False),
        requested_quantity_text=None,
        requested_unit_raw=None,
        sku=None,
        product_name=None,
        sale_qty=sale_qty,
        sale_unit=sale_unit,
        conversion_path=None,
        unit_price_cents=unit_price_cents,
        catalog_unit_price_cents=None,  # user-added, no catalog anchor
        extended_cents=extended_cents_of(sale_qty, unit_price_cents),
        stock=None,
        match=None,
        requirements=[],
        review=ReviewState(state=ReviewStateT.none, reasons=[], resolved_by=None),
        origins=Origins(
            quantity=Origin.user_filled,
            unit_price=Origin.user_filled,
            sku=Origin.user_filled,
            description=Origin.user_filled,
        ),
    )


def _resolve_review(current: ReviewState, resolved_by: ResolvedBy) -> ReviewState:
    """Edit resolves an open review. If review was already none
    or resolved, leave it alone."""

    if current.state != ReviewStateT.open:
        return current
    return ReviewState(
        state=ReviewStateT.resolved, reasons=current.reasons, resolved_by=resolved_by
    )
