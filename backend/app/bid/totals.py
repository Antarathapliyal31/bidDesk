"""Bid totals — code-only math (SPEC §9.3 step 4, §13).

Thin wrapper that re-exports the canonical formulas living in
`contracts/bid.py`. The Bid Pydantic validator enforces the same
identities at assembly time, so a bug here can't ship through —
but keeping the names in a predictable module makes the assembly
code read like English:

    totals = Totals(
        subtotal_cents=subtotal_cents(lines),
        tax_rate_ppm=branch.tax_rate_ppm,
        tax_cents=tax_cents_of(subtotal, branch.tax_rate_ppm),
        total_cents=subtotal + tax,
        priced_lines=count_priced(lines),
        open_flags=count_open_flags(lines),
    )

Tax is half-up on a SINGLE subtotal (DECISIONS #11), not
per-line — matches SPEC §13.
"""

from __future__ import annotations

from app.contracts.bid import (
    BidLine,
    extended_cents_of,
    tax_cents_of,
)
from app.contracts.common import ReviewStateT

__all__ = [
    "count_open_flags",
    "count_priced",
    "extended_cents_of",
    "subtotal_cents",
    "tax_cents_of",
]


def subtotal_cents(lines: list[BidLine]) -> int:
    """`sum(extended_cents)` over priced lines."""

    return sum(line.extended_cents for line in lines if line.extended_cents is not None)


def count_priced(lines: list[BidLine]) -> int:
    """Count of lines that carry a computed `extended_cents`."""

    return sum(1 for line in lines if line.extended_cents is not None)


def count_open_flags(lines: list[BidLine]) -> int:
    """Count of lines whose review state is `open`."""

    return sum(1 for line in lines if line.review.state == ReviewStateT.open)
