"""Deterministic catalog seed.

Running `python -m app.catalog.seed` builds backend/data/catalog.db from
hand-picked constant tables. There is no randomness, no wallclock access
(`price_updated_at` is a literal), and no WAL journal mode so the file
has no sidecars. The build writes to a `.tmp` neighbor file first and
then atomically renames into place, so an interrupted run never
corrupts an existing catalog.

Content shape per the step-4 plan:
  - 3 branches with real tax rates (NJ, NY, PA); run's default is NJ.
  - SPF dimensional lumber (untreated #2) + SYP PT lumber in both
    above-ground and ground-contact variants. No SPF PT (East Coast
    realism, DECISIONS #7).
  - Trim, sheathing, drywall, insulation, housewrap, roofing, fasteners,
    connectors — synthetic prices, generic names (no brand names).
  - Stock table is dense: one row per (product, branch), defaulting to a
    realistic baseline per branch. Overrides realise the demo scenarios:
    full-cover transfer, partial "N of M", zero-stock everywhere, and
    no-run-branch-but-others-have-it.
  - A few active=0 SKUs so the query-builder's always-`active=1`
    injection has something to filter out.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_BACKEND_ROOT = _HERE.parent.parent
_SCHEMA_PATH = _HERE / "schema.sql"
_DEFAULT_DB_PATH = _BACKEND_ROOT / "data" / "catalog.db"

# Fixed clock. No wallclock access anywhere in this module — the
# test_seed TestNoWallclock scan enforces that at CI time.
PRICE_UPDATED_AT = "2026-01-01T00:00:00Z"


# -------------- Branches --------------

BRANCHES: list[dict[str, Any]] = [
    {
        "id": "BR-NJ-01",
        "name": "BidDesk North Jersey",
        "city": "Edison",  # Newark is a UEZ with a reduced rate; Edison is state-standard.
        "state": "NJ",
        "tax_rate_ppm": 66250,  # 6.625% — NJ state sales tax
    },
    {
        "id": "BR-NY-01",
        "name": "BidDesk Long Island",
        "city": "Islip",
        "state": "NY",
        "tax_rate_ppm": 86250,  # 8.625% — Suffolk County combined
    },
    {
        "id": "BR-PA-01",
        "name": "BidDesk Lehigh Valley",
        "city": "Allentown",
        "state": "PA",
        "tax_rate_ppm": 60000,  # 6.000% — PA state sales tax
    },
]

# The branch a run defaults to when the UI doesn't yet choose one, and
# the "home" branch for transfer-exclusion tests.
RUN_BRANCH_ID = "BR-NJ-01"


# -------------- Dimensional lumber --------------

_SPF_PER_FT_CENTS: dict[str, int] = {
    "2x4": 50,
    "2x6": 80,
    "2x8": 110,
    "2x10": 150,
    "2x12": 190,
    "4x4": 180,
}

_LUMBER_LENGTHS: list[int] = [8, 10, 12, 14, 16, 18]  # no 17 (forces substitute)
_FOUR_BY_LENGTHS: list[int] = [8, 10, 12]


def _grade_code(grade: str) -> str:
    return grade.replace("#", "")


def _lumber_price_cents(nominal: str, length: int, species: str, treatment: str) -> int:
    price = Decimal(_SPF_PER_FT_CENTS[nominal]) * Decimal(length)
    if species == "SYP":
        price *= Decimal("1.15")
    if treatment == "pressure_treated_above_ground":
        price *= Decimal("1.25")
    elif treatment == "pressure_treated_ground_contact":
        price *= Decimal("1.40")
    return int(price.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _lumber_row(
    nominal: str,
    length: int,
    species: str,
    grade: str,
    treatment: str,
    treat_code: str,
    treat_label: str,
) -> dict[str, Any]:
    base_sku = f"LUM-{nominal.upper()}-{length}-{species}-{_grade_code(grade)}"
    sku = f"{base_sku}-{treat_code}" if treat_code else base_sku
    display = f"{nominal} × {length}' {species} {grade}"
    if treat_label:
        display = f"{display} {treat_label}"
    return {
        "sku": sku,
        "category": "dimensional_lumber",
        "nominal_size": nominal,
        "length_ft": length,
        "thickness_in": None,
        "width_in": None,
        "height_in": None,
        "grade": grade,
        "species": species,
        "treatment": treatment,
        "fire_rating": "none",
        "moisture_resistant": 0,
        "r_value": None,
        "sale_unit": "EA",
        "pack_qty": None,
        "coverage_sqft": None,
        "unit_price_cents": _lumber_price_cents(nominal, length, species, treatment),
        "price_updated_at": PRICE_UPDATED_AT,
        "active": 1,
        "name": display,
        "description": f"Deterministic seed row for {display}. Synthetic price.",
    }


def _lumber_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for nominal in ("2x4", "2x6", "2x8", "2x10", "2x12"):
        for length in _LUMBER_LENGTHS:
            rows.append(_lumber_row(nominal, length, "SPF", "#2", "none", "", ""))
            rows.append(
                _lumber_row(
                    nominal,
                    length,
                    "SYP",
                    "#2",
                    "pressure_treated_above_ground",
                    "PTAG",
                    "PT above-ground",
                )
            )
            rows.append(
                _lumber_row(
                    nominal,
                    length,
                    "SYP",
                    "#2",
                    "pressure_treated_ground_contact",
                    "PTGC",
                    "PT ground-contact",
                )
            )
    for length in _FOUR_BY_LENGTHS:
        rows.append(_lumber_row("4x4", length, "SPF", "#2", "none", "", ""))
        rows.append(
            _lumber_row(
                "4x4",
                length,
                "SYP",
                "#2",
                "pressure_treated_ground_contact",
                "PTGC",
                "PT ground-contact",
            )
        )
    return rows


# -------------- Trim --------------


def _trim_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base_per_ft = {"1x4": 40, "1x6": 60, "1x8": 85}
    for nominal in ("1x4", "1x6", "1x8"):
        for length in (8, 10):
            price = base_per_ft[nominal] * length
            sku = f"TRM-{nominal.upper()}-{length}-SPF-2"
            display = f"{nominal} × {length}' SPF #2 trim"
            rows.append(
                {
                    "sku": sku,
                    "category": "trim",
                    "nominal_size": nominal,
                    "length_ft": length,
                    "thickness_in": None,
                    "width_in": None,
                    "height_in": None,
                    "grade": "#2",
                    "species": "SPF",
                    "treatment": "none",
                    "fire_rating": "none",
                    "moisture_resistant": 0,
                    "r_value": None,
                    "sale_unit": "EA",
                    "pack_qty": None,
                    "coverage_sqft": None,
                    "unit_price_cents": price,
                    "price_updated_at": PRICE_UPDATED_AT,
                    "active": 1,
                    "name": display,
                    "description": f"Deterministic seed row for {display}. Synthetic price.",
                }
            )
    return rows


# -------------- Sheathing (SHT, coverage_sqft = 32 on 48×96 sheets) --------------


def _sheathing_rows() -> list[dict[str, Any]]:
    specs = [
        ("OSB", "7/16", 1900),
        ("OSB", "1/2", 2200),
        ("OSB", "23/32", 3100),
        ("CDX", "1/2", 3400),
        ("CDX", "3/4", 4800),
    ]
    rows = []
    for material, thickness, price in specs:
        thickness_code = thickness.replace("/", "_")
        sku = f"SHE-{material}-{thickness_code}-48X96"
        display = f'{thickness}" {material} sheathing 48×96'
        rows.append(
            {
                "sku": sku,
                "category": "sheathing",
                "nominal_size": None,
                "length_ft": None,
                "thickness_in": thickness,
                "width_in": 48,
                "height_in": 96,
                "grade": None,
                "species": None,
                "treatment": "none",
                "fire_rating": "none",
                "moisture_resistant": 0,
                "r_value": None,
                "sale_unit": "SHT",
                "pack_qty": None,
                "coverage_sqft": 32,
                "unit_price_cents": price,
                "price_updated_at": PRICE_UPDATED_AT,
                "active": 1,
                "name": display,
                "description": f"Deterministic seed row for {display}. Synthetic price.",
            }
        )
    return rows


# -------------- Drywall (SHT, coverage_sqft = 32) --------------


def _drywall_rows() -> list[dict[str, Any]]:
    rows = []
    specs = [
        ("1/2", 0, "none", 1100, "regular"),
        ("1/2", 1, "none", 1450, "moisture-resistant"),
        ("5/8", 0, "type_x", 1550, "Type X"),
    ]
    for thickness, mr, fire, price, label in specs:
        thickness_code = thickness.replace("/", "_")
        mr_suffix = "-MR" if mr else ""
        fire_suffix = "-TYPEX" if fire == "type_x" else ""
        sku = f"DRY-{thickness_code}-48X96{mr_suffix}{fire_suffix}"
        display = f'{thickness}" {label} drywall 48×96'
        rows.append(
            {
                "sku": sku,
                "category": "drywall",
                "nominal_size": None,
                "length_ft": None,
                "thickness_in": thickness,
                "width_in": 48,
                "height_in": 96,
                "grade": None,
                "species": None,
                "treatment": "none",
                "fire_rating": fire,
                "moisture_resistant": mr,
                "r_value": None,
                "sale_unit": "SHT",
                "pack_qty": None,
                "coverage_sqft": 32,
                "unit_price_cents": price,
                "price_updated_at": PRICE_UPDATED_AT,
                "active": 1,
                "name": display,
                "description": f"Deterministic seed row for {display}. Synthetic price.",
            }
        )
    return rows


# -------------- Insulation (BAG, coverage_sqft per R-value) --------------


def _insulation_rows() -> list[dict[str, Any]]:
    specs = [(13, 88, 3400), (19, 49, 4200), (21, 42, 4800), (30, 32, 5600)]
    rows = []
    for r, coverage, price in specs:
        sku = f"INS-R{r}-BAG"
        display = f"R-{r} fiberglass insulation bag"
        rows.append(
            {
                "sku": sku,
                "category": "insulation",
                "nominal_size": None,
                "length_ft": None,
                "thickness_in": None,
                "width_in": None,
                "height_in": None,
                "grade": None,
                "species": None,
                "treatment": "none",
                "fire_rating": "none",
                "moisture_resistant": 0,
                "r_value": r,
                "sale_unit": "BAG",
                "pack_qty": None,
                "coverage_sqft": coverage,
                "unit_price_cents": price,
                "price_updated_at": PRICE_UPDATED_AT,
                "active": 1,
                "name": display,
                "description": f"Deterministic seed row for {display}. Synthetic price.",
            }
        )
    return rows


# -------------- Housewrap (ROLL, coverage_sqft = 1000) --------------


def _housewrap_rows() -> list[dict[str, Any]]:
    return [
        {
            "sku": "WRAP-9X111-ROLL",
            "category": "housewrap",
            "nominal_size": None,
            "length_ft": None,  # length_ft is lumber length; housewrap uses coverage_sqft
            "thickness_in": None,
            "width_in": None,
            "height_in": None,
            "grade": None,
            "species": None,
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "r_value": None,
            "sale_unit": "ROLL",
            "pack_qty": None,
            "coverage_sqft": 1000,
            "unit_price_cents": 11500,
            "price_updated_at": PRICE_UPDATED_AT,
            "active": 1,
            "name": "9' × 111' housewrap roll",
            "description": "Deterministic seed row. Generic housewrap, synthetic price.",
        }
    ]


# -------------- Roofing (BDL with pack_qty + ROLL underlayment) --------------


def _roofing_rows() -> list[dict[str, Any]]:
    return [
        {
            "sku": "ROOF-SHINGLE-BDL",
            "category": "roofing",
            "nominal_size": None,
            "length_ft": None,
            "thickness_in": None,
            "width_in": None,
            "height_in": None,
            "grade": None,
            "species": None,
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "r_value": None,
            "sale_unit": "BDL",
            "pack_qty": 3,  # 3 bundles per square — used by squares_to_bundles
            "coverage_sqft": None,
            "unit_price_cents": 3800,
            "price_updated_at": PRICE_UPDATED_AT,
            "active": 1,
            "name": "Asphalt shingle bundle (3-tab)",
            "description": "Deterministic seed row. Generic asphalt shingles, synthetic price.",
        },
        {
            "sku": "ROOF-UNDERLAY-ROLL",
            "category": "roofing",
            "nominal_size": None,
            "length_ft": None,
            "thickness_in": None,
            "width_in": None,
            "height_in": None,
            "grade": None,
            "species": None,
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "r_value": None,
            "sale_unit": "ROLL",
            "pack_qty": None,
            "coverage_sqft": 400,
            "unit_price_cents": 9500,
            "price_updated_at": PRICE_UPDATED_AT,
            "active": 1,
            "name": "Roofing underlayment roll",
            "description": "Deterministic seed row. Generic synthetic underlayment, synthetic price.",
        },
    ]


# -------------- Fasteners (BOX, pack_qty = pieces per box) --------------


def _fasteners_rows() -> list[dict[str, Any]]:
    # (code, label, pieces_per_box, price_cents)
    specs = [
        ("NAIL-COMMON-8D", "common nail 8d", 500, 1800),
        ("NAIL-COMMON-16D", "common nail 16d", 300, 2200),
        ("NAIL-ROOF-1_25", "roofing nail 1-1/4 in", 1000, 2400),
        ("NAIL-FIN-6D", "finish nail 6d", 1000, 1900),
        ("SCR-DRYWALL-1_25", "drywall screw 1-1/4 in", 200, 1500),
        ("SCR-DECK-3IN", "deck screw 3 in", 100, 2800),
    ]
    rows = []
    for code, label, pieces_per_box, price in specs:
        sku = f"FAST-{code}-BOX"
        display = f"{label} (box)"
        rows.append(
            {
                "sku": sku,
                "category": "fasteners",
                "nominal_size": None,
                "length_ft": None,
                "thickness_in": None,
                "width_in": None,
                "height_in": None,
                "grade": None,
                "species": None,
                "treatment": "none",
                "fire_rating": "none",
                "moisture_resistant": 0,
                "r_value": None,
                "sale_unit": "BOX",
                "pack_qty": pieces_per_box,
                "coverage_sqft": None,
                "unit_price_cents": price,
                "price_updated_at": PRICE_UPDATED_AT,
                "active": 1,
                "name": display,
                "description": f"Deterministic seed row for {display}. Synthetic price.",
            }
        )
    return rows


# -------------- Connectors (EA joist hangers + hurricane tie) --------------


def _connectors_rows() -> list[dict[str, Any]]:
    specs = [
        ("JHANG", "2x6", 320),
        ("JHANG", "2x8", 360),
        ("JHANG", "2x10", 420),
        ("JHANG", "2x12", 480),
        ("HURRICANE-TIE", "H2.5A", 180),
    ]
    rows = []
    for family, size, price in specs:
        if family == "JHANG":
            sku = f"CON-JHANG-{size.upper()}"
            display = f"Joist hanger for {size}"
            nominal = size
        else:
            sku = f"CON-{family}-{size}"
            display = f"Hurricane tie {size}"
            nominal = None
        rows.append(
            {
                "sku": sku,
                "category": "connectors",
                "nominal_size": nominal,
                "length_ft": None,
                "thickness_in": None,
                "width_in": None,
                "height_in": None,
                "grade": None,
                "species": None,
                "treatment": "none",
                "fire_rating": "none",
                "moisture_resistant": 0,
                "r_value": None,
                "sale_unit": "EA",
                "pack_qty": None,
                "coverage_sqft": None,
                "unit_price_cents": price,
                "price_updated_at": PRICE_UPDATED_AT,
                "active": 1,
                "name": display,
                "description": f"Deterministic seed row for {display}. Synthetic price.",
            }
        )
    return rows


# -------------- Inactive rows --------------


def _inactive_rows() -> list[dict[str, Any]]:
    """A handful of active=0 SKUs so the query-builder's always-`active=1`
    injection has something to filter out. These rows are structurally
    valid; only the `active` flag is 0. No fake dimensions — the inactive
    trim is a real 1x4 with an -OLD suffix (replaced by TRM-1X4-8-SPF-2).
    """
    return [
        {
            "sku": "FAST-NAIL-OBSOLETE-BOX",
            "category": "fasteners",
            "nominal_size": None,
            "length_ft": None,
            "thickness_in": None,
            "width_in": None,
            "height_in": None,
            "grade": None,
            "species": None,
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "r_value": None,
            "sale_unit": "BOX",
            "pack_qty": 100,
            "coverage_sqft": None,
            "unit_price_cents": 1200,
            "price_updated_at": PRICE_UPDATED_AT,
            "active": 0,
            "name": "Obsolete nail (discontinued)",
            "description": "Discontinued SKU — kept for history. Filtered out by active=1.",
        },
        {
            "sku": "TRM-1X4-8-SPF-2-OLD",
            "category": "trim",
            "nominal_size": "1x4",
            "length_ft": 8,
            "thickness_in": None,
            "width_in": None,
            "height_in": None,
            "grade": "#2",
            "species": "SPF",
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "r_value": None,
            "sale_unit": "EA",
            "pack_qty": None,
            "coverage_sqft": None,
            "unit_price_cents": 300,
            "price_updated_at": PRICE_UPDATED_AT,
            "active": 0,
            "name": "1×4 × 8' SPF #2 trim (previous SKU, replaced)",
            "description": "Replaced by TRM-1X4-8-SPF-2. Filtered out by active=1.",
        },
    ]


def build_products() -> list[dict[str, Any]]:
    """Return the full product table in a deterministic order (sorted by sku)."""
    rows: list[dict[str, Any]] = []
    rows.extend(_lumber_rows())
    rows.extend(_trim_rows())
    rows.extend(_sheathing_rows())
    rows.extend(_drywall_rows())
    rows.extend(_insulation_rows())
    rows.extend(_housewrap_rows())
    rows.extend(_roofing_rows())
    rows.extend(_fasteners_rows())
    rows.extend(_connectors_rows())
    rows.extend(_inactive_rows())
    rows.sort(key=lambda r: r["sku"])
    return rows


# -------------- Stock baseline + overrides --------------

# Baseline on_hand per (category, branch) for any (product, branch) pair
# not named in _STOCK_OVERRIDES. Varies by category so the UI doesn't show
# identical stock on every line. Every product.category must appear here;
# build_stock() raises ValueError on a missing entry so a new category
# can't silently fall back to a default.
_BASELINE_STOCK_BY_CATEGORY: dict[str, dict[str, int]] = {
    "dimensional_lumber": {"BR-NJ-01": 120, "BR-NY-01": 80, "BR-PA-01": 50},
    "trim": {"BR-NJ-01": 60, "BR-NY-01": 40, "BR-PA-01": 25},
    "sheathing": {"BR-NJ-01": 100, "BR-NY-01": 70, "BR-PA-01": 40},
    "drywall": {"BR-NJ-01": 90, "BR-NY-01": 60, "BR-PA-01": 35},
    "insulation": {"BR-NJ-01": 50, "BR-NY-01": 35, "BR-PA-01": 20},
    "housewrap": {"BR-NJ-01": 15, "BR-NY-01": 10, "BR-PA-01": 5},
    "roofing": {"BR-NJ-01": 80, "BR-NY-01": 55, "BR-PA-01": 30},
    "fasteners": {"BR-NJ-01": 40, "BR-NY-01": 30, "BR-PA-01": 20},
    "connectors": {"BR-NJ-01": 40, "BR-NY-01": 30, "BR-PA-01": 20},
}


def _mk_stock(
    on_hand: int, next_date: str | None = None, next_qty: int | None = None
) -> dict[str, Any]:
    return {
        "on_hand": on_hand,
        "next_receipt_date": next_date,
        "next_receipt_qty": next_qty,
    }


# Overrides win over the baseline. All next_receipt_date values are AFTER
# 2026-12-15.
_STOCK_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    # Full-cover transfer scenario: NJ 20 < demand (40). Both NY + PA can
    # cover the shortfall alone.
    ("LUM-2X6-16-SYP-2-PTAG", "BR-NJ-01"): _mk_stock(20, "2026-12-20", 50),
    ("LUM-2X6-16-SYP-2-PTAG", "BR-NY-01"): _mk_stock(80),
    ("LUM-2X6-16-SYP-2-PTAG", "BR-PA-01"): _mk_stock(60),
    # Partial "N of M" scenario: NJ 5, demand 20. Shortfall 15; neither
    # NY (12) nor PA (8) can cover alone.
    ("LUM-2X10-18-SPF-2", "BR-NJ-01"): _mk_stock(5),
    ("LUM-2X10-18-SPF-2", "BR-NY-01"): _mk_stock(12, "2027-01-15", 25),
    ("LUM-2X10-18-SPF-2", "BR-PA-01"): _mk_stock(8),
    # Zero stock everywhere.
    ("ROOF-UNDERLAY-ROLL", "BR-NJ-01"): _mk_stock(0, "2027-01-10", 20),
    ("ROOF-UNDERLAY-ROLL", "BR-NY-01"): _mk_stock(0),
    ("ROOF-UNDERLAY-ROLL", "BR-PA-01"): _mk_stock(0),
    # No run-branch stock, others have it (trim fascia path).
    ("TRM-1X6-10-SPF-2", "BR-NJ-01"): _mk_stock(0),
    ("TRM-1X6-10-SPF-2", "BR-NY-01"): _mk_stock(30),
    ("TRM-1X6-10-SPF-2", "BR-PA-01"): _mk_stock(20),
}


def _validate_stock_overrides(products: list[dict[str, Any]]) -> None:
    """Fail loudly if any override key references a nonexistent SKU or branch.

    Raises ValueError (not AssertionError) so `python -O` can't strip the
    check and let a typo silently seed unreachable rows.
    """
    valid_skus = {p["sku"] for p in products}
    valid_branches = {b["id"] for b in BRANCHES}
    for sku, branch_id in _STOCK_OVERRIDES:
        if sku not in valid_skus:
            raise ValueError(f"stock override references unknown SKU {sku!r}")
        if branch_id not in valid_branches:
            raise ValueError(f"stock override references unknown branch {branch_id!r}")


def build_stock(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a dense stock table: one row per (product, branch).

    Default on_hand comes from `_BASELINE_STOCK_BY_CATEGORY[category][branch]`.
    Any `(sku, branch_id)` named in `_STOCK_OVERRIDES` wins over the
    baseline (including when the override sets on_hand to 0). Raises
    ValueError if a product uses a category that has no baseline entry —
    forcing an explicit decision instead of a silent fallback.
    """
    _validate_stock_overrides(products)
    rows: list[dict[str, Any]] = []
    for p in products:
        category = p["category"]
        if category not in _BASELINE_STOCK_BY_CATEGORY:
            raise ValueError(
                f"no baseline stock declared for category {category!r} "
                f"(product {p['sku']!r}); add it to _BASELINE_STOCK_BY_CATEGORY"
            )
        baseline = _BASELINE_STOCK_BY_CATEGORY[category]
        for b in BRANCHES:
            key = (p["sku"], b["id"])
            override = _STOCK_OVERRIDES.get(key)
            if override is not None:
                rows.append(
                    {
                        "sku": p["sku"],
                        "branch_id": b["id"],
                        "on_hand": override["on_hand"],
                        "next_receipt_date": override["next_receipt_date"],
                        "next_receipt_qty": override["next_receipt_qty"],
                    }
                )
            else:
                rows.append(
                    {
                        "sku": p["sku"],
                        "branch_id": b["id"],
                        "on_hand": baseline[b["id"]],
                        "next_receipt_date": None,
                        "next_receipt_qty": None,
                    }
                )
    rows.sort(key=lambda r: (r["sku"], r["branch_id"]))
    return rows


# -------------- Insert helpers --------------


_BRANCH_COLS = ("id", "name", "city", "state", "tax_rate_ppm")
_PRODUCT_COLS = (
    "sku",
    "category",
    "nominal_size",
    "length_ft",
    "thickness_in",
    "width_in",
    "height_in",
    "grade",
    "species",
    "treatment",
    "fire_rating",
    "moisture_resistant",
    "r_value",
    "sale_unit",
    "pack_qty",
    "coverage_sqft",
    "unit_price_cents",
    "price_updated_at",
    "active",
    "name",
    "description",
)
_STOCK_COLS = ("sku", "branch_id", "on_hand", "next_receipt_date", "next_receipt_qty")


def _insert(conn: sqlite3.Connection, table: str, cols: tuple[str, ...], rows: list[dict]) -> None:
    placeholders = ", ".join(["?"] * len(cols))
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])


# -------------- Atomic build --------------


def main(db_path: Path = _DEFAULT_DB_PATH) -> Path:
    """Build catalog.db at `db_path`. Returns the final path.

    Writes to `<db>.tmp` first, then os.replace() to the final path. If any
    step fails, the connection is closed, the `.tmp` is removed, and the
    original `db_path` (if any) is untouched.
    """
    db_path = Path(db_path)
    tmp_path = db_path.with_suffix(db_path.suffix + ".tmp")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # Remove a stale .tmp from a previous crashed run before starting.
    if tmp_path.exists():
        tmp_path.unlink()

    conn = sqlite3.connect(tmp_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        _insert(conn, "branches", _BRANCH_COLS, BRANCHES)
        products = build_products()
        _insert(conn, "products", _PRODUCT_COLS, products)
        _insert(conn, "stock", _STOCK_COLS, build_stock(products))
        conn.commit()
    except BaseException:
        conn.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    conn.close()
    os.replace(tmp_path, db_path)
    return db_path


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description="Build backend/data/catalog.db deterministically from hand-picked tables."
    )
    parser.add_argument("--db", type=Path, default=_DEFAULT_DB_PATH, help="Output catalog DB path.")
    args = parser.parse_args()
    out = main(args.db)
    print(f"seeded catalog at {out}")


if __name__ == "__main__":
    _cli()
