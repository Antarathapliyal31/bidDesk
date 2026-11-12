"""Least-waste stock-length picker (DECISIONS #24).

When an item comes in a LINEAR unit (LF, ft, yd, meter — anything
that normalizes to LF) and the description doesn't state a
per-piece length, the model picks the product family and code
picks the stock length. The picker runs inside `verify.py` after
unit normalization, but before the DB fetch that locks in the
submitted SKU.

Algorithm:
  For each candidate SKU returned by the agent's `query_catalog`
  calls that shares the FAMILY of the submitted SKU (same
  nominal_size / species / grade / treatment / fire_rating /
  moisture_resistant), compute:
      pieces = ceil(total_ft / length_ft)
      waste_ft = pieces * length_ft - total_ft
  Pick the minimum-waste candidate. Ties → shorter length_ft
  (fewer pricey off-cuts). The output names the chosen SKU, its
  length_ft, the piece count, and the waste.

Keeping this in code (not the prompt) means:
  - The decision is auditable and deterministic.
  - Ties always resolve the same way, every run.
  - The model can't invent a non-stock length to "fit better".

See DECISIONS #24 for the full why.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Any

# Columns that define a stock-length "family" — swapping length_ft
# within a family is a cost-not-correctness decision.
_FAMILY_FIELDS: tuple[str, ...] = (
    "nominal_size",
    "species",
    "grade",
    "treatment",
    "fire_rating",
    "moisture_resistant",
    "category",
)


@dataclass(frozen=True)
class LengthPick:
    """Result of the picker. `waste_ft` is for the conversion_path
    display; `pieces` is the ceil quantity in the stock sale unit."""

    sku: str
    length_ft: int
    pieces: int
    waste_ft: Decimal


@dataclass(frozen=True)
class NoCandidateMatch:
    """Returned when no same-family candidate has a usable
    length_ft — the caller should fall through to its existing
    behavior (use the model's chosen SKU as-is)."""

    reason: str


def _family_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(f) for f in _FAMILY_FIELDS)


def _ceil_decimal(x: Decimal) -> int:
    return int(x.quantize(Decimal("1"), rounding=ROUND_CEILING))


def pick_best_waste(
    total_ft: Decimal,
    *,
    submitted_sku: str,
    candidates: list[dict[str, Any]],
) -> LengthPick | NoCandidateMatch:
    """Pick the least-waste stock-length SKU in the submitted
    SKU's family. `total_ft` is the item's quantity already
    normalised to feet (LF). `candidates` is every row the agent
    has seen for this item via `query_catalog` (verify.py passes
    `candidates_by_item[item_id]` resolved to row dicts).

    Returns `NoCandidateMatch` when:
      - The submitted SKU isn't in `candidates` (shouldn't happen
        — verify.py's candidate-set check catches that first).
      - No same-family row carries `length_ft` (the family isn't
        length-stocked).
      - `total_ft <= 0` (defensive; verify.py gates this upstream).
    """

    if total_ft <= 0:
        return NoCandidateMatch(reason="total_ft must be > 0")

    submitted = next((c for c in candidates if c.get("sku") == submitted_sku), None)
    if submitted is None:
        return NoCandidateMatch(reason=f"submitted sku {submitted_sku!r} not in candidate set")

    family = _family_key(submitted)
    family_rows: list[dict[str, Any]] = [
        c for c in candidates if _family_key(c) == family and isinstance(c.get("length_ft"), int)
    ]
    if not family_rows:
        return NoCandidateMatch(
            reason=(
                f"no same-family candidates with length_ft "
                f"(family={dict(zip(_FAMILY_FIELDS, family, strict=True))})"
            )
        )

    best: LengthPick | None = None
    for row in family_rows:
        length_ft = int(row["length_ft"])
        pieces = _ceil_decimal(total_ft / Decimal(length_ft))
        # Guard the piece count: if length_ft is zero/negative,
        # skip — it's not a valid stock length.
        if pieces <= 0:
            continue  # pragma: no cover — length_ft > 0 by seed invariants
        waste = Decimal(pieces) * Decimal(length_ft) - total_ft
        candidate = LengthPick(
            sku=str(row["sku"]),
            length_ft=length_ft,
            pieces=pieces,
            waste_ft=waste,
        )
        if best is None:
            best = candidate
            continue
        # Pick minimum waste; ties → shorter length_ft.
        if candidate.waste_ft < best.waste_ft or (
            candidate.waste_ft == best.waste_ft and candidate.length_ft < best.length_ft
        ):
            best = candidate

    if best is None:  # pragma: no cover — handled by family_rows empty case above
        return NoCandidateMatch(reason="no viable length_ft in family")
    return best
