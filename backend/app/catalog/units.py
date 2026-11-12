"""Unit canonicalization, two-tier normalization, and dimension conversions.

Everything here is pure. Pricing-time verification calls into this module;
the pricing agent never does. All math runs in Decimal; only the final
quotient is quantized to 6 dp before ceil, per SPEC §8.1.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Any, Literal

import pint


class Unit(StrEnum):
    """The unit enum. PC is deliberately absent — `pc`/`pcs` normalize to EA."""

    EA = "EA"
    LF = "LF"
    SF = "SF"
    SQ = "SQ"
    BF = "BF"
    MBF = "MBF"
    SHT = "SHT"
    BDL = "BDL"
    BOX = "BOX"
    ROLL = "ROLL"
    BAG = "BAG"
    LB = "LB"
    GAL = "GAL"
    HR = "HR"


# Canonicalized written form → Unit enum. Our table wins over pint; this is why
# `pc`/`pcs` resolve to EA (never parsec) and `m`/`M` alone is left to fail
# (bare metres are disallowed).
OUR_TABLE: dict[str, Unit] = {
    # EA
    "pc": Unit.EA,
    "pcs": Unit.EA,
    "piece": Unit.EA,
    "pieces": Unit.EA,
    "ea": Unit.EA,
    "each": Unit.EA,
    # SHT
    "sheet": Unit.SHT,
    "sheets": Unit.SHT,
    "shts": Unit.SHT,
    # SQ
    "sq": Unit.SQ,
    "square": Unit.SQ,
    "squares": Unit.SQ,
    # LF
    "lf": Unit.LF,
    "lin ft": Unit.LF,
    "linear feet": Unit.LF,
    "lineal feet": Unit.LF,
    # SF
    "sf": Unit.SF,
    "sqft": Unit.SF,
    "sq ft": Unit.SF,
    "square foot": Unit.SF,
    "square feet": Unit.SF,
    # BF
    "bf": Unit.BF,
    "bd ft": Unit.BF,
    # MBF
    "mbf": Unit.MBF,
    "m bf": Unit.MBF,
    # BDL
    "bdl": Unit.BDL,
    "bundle": Unit.BDL,
    "bundles": Unit.BDL,
    # ROLL
    "roll": Unit.ROLL,
    "rolls": Unit.ROLL,
    # BAG
    "bag": Unit.BAG,
    "bags": Unit.BAG,
    # BOX
    "box": Unit.BOX,
    "boxes": Unit.BOX,
    "bx": Unit.BOX,
    # LB
    "lb": Unit.LB,
    "lbs": Unit.LB,
    "pound": Unit.LB,
    "pounds": Unit.LB,
    # GAL
    "gal": Unit.GAL,
    "gallon": Unit.GAL,
    "gallons": Unit.GAL,
    # HR
    "hr": Unit.HR,
    "hrs": Unit.HR,
    "hour": Unit.HR,
    "hours": Unit.HR,
}

# Rewrite rules applied only if OUR_TABLE missed. Values are pint-parseable
# tokens (`yd**2`, `ft**2`, `m**2`). Keys cover every spelling that resolves
# to the same area/length unit AFTER canonicalize:
#   - NFKC folds `²` to `2`, so the lookup keys for superscript forms are
#     "yd2" / "ft2" / "m2" (never "yd²" — those strings never reach here).
#   - Caret forms "yd^2" / "ft^2" / "m^2" are kept as-is by canonicalize, so
#     they get their own explicit keys.
# `in` is rewritten to `inch` defensively — pint parses `in` fine but the
# alias keeps our parsing surface predictable.
_PINT_REWRITE: dict[str, str] = {
    "in": "inch",
    # written-out area phrases
    "square yard": "yd**2",
    "square yards": "yd**2",
    "sq yd": "yd**2",
    "sq yds": "yd**2",
    "square meter": "m**2",
    "square meters": "m**2",
    "square metre": "m**2",
    "square metres": "m**2",
    "sq m": "m**2",
    # post-NFKC superscript forms
    "yd2": "yd**2",
    "ft2": "ft**2",
    "m2": "m**2",
    # caret forms
    "yd^2": "yd**2",
    "ft^2": "ft**2",
    "m^2": "m**2",
}

# Allowed pint tokens after rewrite. Bare `m` is deliberately NOT here so that
# `"M"` / `"2 M"` falls through to unknown — SPEC §8.1 only accepts spelled-out
# metres. `in` is handled via _PINT_REWRITE → `inch`.
_PINT_ALLOWLIST: frozenset[str] = frozenset(
    {
        # length
        "yd",
        "yard",
        "yards",
        "meter",
        "meters",
        "metre",
        "metres",
        "cm",
        "mm",
        "inch",
        "inches",
        "ft",
        "foot",
        "feet",
        # area
        "ft**2",
        "yd**2",
        "m**2",
    }
)

# Short form used in conversion_path for display. Keeps SPEC-canonical "10 m" /
# "12 yd²" instead of the long pint-parsed forms.
_PINT_DISPLAY: dict[str, str] = {
    "yard": "yd",
    "yards": "yd",
    "meter": "m",
    "meters": "m",
    "metre": "m",
    "metres": "m",
    "foot": "ft",
    "feet": "ft",
    "inch": "in",
    "inches": "in",
    "yd**2": "yd²",
    "ft**2": "ft²",
    "m**2": "m²",
}

# Singular noun for the "(coverage N SF/sheet)" suffix style.
_SALE_UNIT_NOUN: dict[str, str] = {
    "SHT": "sheet",
    "BAG": "bag",
    "ROLL": "roll",
    "BDL": "bundle",
    "EA": "piece",
    "BOX": "box",
}


def canonicalize(raw: str) -> str:
    """NFKC-normalize, lowercase, strip periods, dashes → space, collapse ws.

    NFKC folds compatibility variants (fullwidth letters, non-breaking spaces,
    `²`/`³` superscripts → regular digits, ligatures). After that we lowercase,
    drop periods, convert ASCII hyphens plus en-dash and em-dash to spaces, and
    collapse runs of whitespace. The same function feeds unit normalization
    (e.g. "Sq. Ft." → "sq ft") and semantic-layer synonym matching (e.g.
    "fire-rated" and "fire–rated" both → "fire rated"), so synonym keys only
    need to be stored once in canonical form.
    """
    s = unicodedata.normalize("NFKC", raw).lower().replace(".", "")
    for dash in ("-", "–", "—"):  # hyphen, en dash, em dash
        s = s.replace(dash, " ")
    return " ".join(s.split())


@dataclass(frozen=True)
class NormalizedUnit:
    """Result of the two-tier normalization.

    Exactly one of (unit, pint_token) is populated, or both are None and
    `unknown` is True.
    """

    unit: Unit | None
    pint_token: str | None  # pint-parseable form, e.g. "yd**2"
    unknown: bool

    @property
    def pint_convertible(self) -> bool:
        return self.pint_token is not None


def normalize_unit(unit_raw: str | None) -> NormalizedUnit:
    """Resolve a user-written unit via (canonicalize → our table → pint)."""
    if not unit_raw or not unit_raw.strip():
        return NormalizedUnit(unit=None, pint_token=None, unknown=True)
    s = canonicalize(unit_raw)
    if s in OUR_TABLE:
        return NormalizedUnit(unit=OUR_TABLE[s], pint_token=None, unknown=False)
    pint_s = _PINT_REWRITE.get(s, s)
    if pint_s in _PINT_ALLOWLIST:
        return NormalizedUnit(unit=None, pint_token=pint_s, unknown=False)
    return NormalizedUnit(unit=None, pint_token=None, unknown=True)


# --- Pure conversion functions (one per dimension's sale-unit target) ---


def _q6(x: Decimal) -> Decimal:
    """Quantize to 6 decimal places. Applied only to the final quotient."""
    return x.quantize(Decimal("0.000001"))


def _ceil_decimal(x: Decimal) -> int:
    """Quantize-then-ceil, so a 100/3=33.333… repetend doesn't bump the result."""
    return int(_q6(x).to_integral_value(rounding=ROUND_CEILING))


def length_to_pieces(base_qty_lf: Decimal, length_ft: int) -> int:
    """LF (base) → EA pieces using length_ft per piece."""
    if length_ft <= 0:
        raise ValueError(f"length_ft must be > 0, got {length_ft}")
    return _ceil_decimal(base_qty_lf / Decimal(length_ft))


def area_to_coverage_units(base_qty_sf: Decimal, coverage_sqft: int) -> int:
    """SF (base) → sheets / bags / rolls using coverage_sqft per sale unit."""
    if coverage_sqft <= 0:
        raise ValueError(f"coverage_sqft must be > 0, got {coverage_sqft}")
    return _ceil_decimal(base_qty_sf / Decimal(coverage_sqft))


def squares_to_bundles(base_qty_sf: Decimal, pack_qty: int) -> int:
    """SF (base) → BDL. 1 square = 100 SF; pack_qty is bundles per square."""
    if pack_qty <= 0:
        raise ValueError(f"pack_qty must be > 0, got {pack_qty}")
    return _ceil_decimal(base_qty_sf / Decimal(100) * Decimal(pack_qty))


def board_feet_to_pieces(base_qty_bf: Decimal, nominal_size: str, length_ft: int) -> int:
    """BF (base) → EA pieces. bf_per_piece = (t_nom * w_nom * length_ft) / 12."""
    bfp = _bf_per_piece(nominal_size, length_ft)
    if bfp <= 0:
        raise ValueError(f"bf_per_piece non-positive for {nominal_size}x{length_ft}")
    return _ceil_decimal(base_qty_bf / bfp)


def pieces_to_boxes(base_qty_ea: Decimal, pack_qty: int) -> int:
    """EA (base) → BOX for fasteners: `pieces / pack_qty`.

    Example: 500 nails, pack_qty=100 → 5 BOX. Ceiled, so 501 → 6.
    Needed because item quantities land as EA while fastener SKUs
    sell as BOX (SPEC §8.1).
    """

    if pack_qty <= 0:
        raise ValueError(f"pack_qty must be > 0, got {pack_qty}")
    return _ceil_decimal(base_qty_ea / Decimal(pack_qty))


def _bf_per_piece(nominal_size: str, length_ft: int) -> Decimal:
    """Board feet of one piece. Private helper — not a top-level conversion."""
    t, _, w = nominal_size.partition("x")
    if not t or not w:
        raise ValueError(f"nominal_size must be like '2x6', got {nominal_size!r}")
    return (Decimal(t) * Decimal(w) * Decimal(length_ft)) / Decimal(12)


UNIT_CONVERSIONS: dict[str, Callable[..., int]] = {
    "length_to_pieces": length_to_pieces,
    "area_to_coverage_units": area_to_coverage_units,
    "squares_to_bundles": squares_to_bundles,
    "board_feet_to_pieces": board_feet_to_pieces,
    "pieces_to_boxes": pieces_to_boxes,
}


# --- Pint registry (Decimal-configured, allowlist-only) ---

_UREG = pint.UnitRegistry(non_int_type=Decimal)

# Base-unit target for each dimension's pint fallback.
_PINT_TARGET: dict[str, str] = {"LF": "ft", "SF": "ft**2"}


def pint_convert_to(value: Decimal, pint_token: str, target_base_unit: str) -> Decimal | None:
    """Convert `value * pint_token` to the given base unit via pint.

    Returns None if the dimensionality doesn't match or the token is unknown
    to pint. Caller must have already passed the token through the allowlist.
    """
    target = _PINT_TARGET.get(target_base_unit)
    if target is None:
        return None
    try:
        qty = value * _UREG(pint_token)
        magnitude = qty.to(target).magnitude
    except (pint.DimensionalityError, pint.UndefinedUnitError):
        return None
    # pint sometimes returns float even with non_int_type=Decimal; str() keeps
    # a human-stable representation that quantizes cleanly downstream.
    return magnitude if isinstance(magnitude, Decimal) else Decimal(str(magnitude))


# --- Dimension dataclasses (shape that semantic_layer.py loads YAML into) ---


@dataclass(frozen=True)
class SaleUnitSpec:
    function: str
    uses_fields: tuple[str, ...]


@dataclass(frozen=True)
class Dimension:
    """One conversion family loaded from conversions.yaml."""

    name: str
    base_unit: str  # e.g. "LF", "SF", "BF" — a Unit value
    units: dict[str, int]  # {"SQ": 100, "SF": 1}
    sale_units: dict[str, SaleUnitSpec]  # keyed by sale_unit value


# --- End-to-end convert ---


@dataclass(frozen=True)
class ConvertResult:
    """Outcome of attempting to turn an item quantity into a sale quantity.

    On success: sale_qty and conversion_path are set; note is None.
    On pint recovery: confidence_cap == "medium" and note == "unit_from_pint".
    On failure: sale_qty and conversion_path are None; note == "unit_conversion_unavailable".
    """

    sale_qty: int | None
    conversion_path: str | None
    confidence_cap: Literal["medium"] | None
    note: Literal["unit_from_pint", "unit_conversion_unavailable"] | None


def convert_item_to_sale_qty(
    item_qty: Decimal,
    item_unit: Unit | None,
    unit_raw: str | None,
    pint_convertible: bool,
    product: dict[str, Any],
    dimensions: list[Dimension],
) -> ConvertResult:
    """Run the SPEC §8.1 selection algorithm against one product row.

    `item_unit` is set when our table matched. When it's None but
    `pint_convertible` is True, the pint fallback runs using `unit_raw`.
    Raises ValueError on non-positive item_qty — analyst (§6.4 step 4) must
    already have dropped those to `unreadable`; reaching pricing with qty≤0
    is an invariant break worth failing loudly.
    """
    if item_qty <= 0:
        raise ValueError(f"item_qty must be > 0, got {item_qty}")

    sale_unit_str = str(product.get("sale_unit") or "")
    if not sale_unit_str:
        return ConvertResult(None, None, None, "unit_conversion_unavailable")

    # Identity passthrough (SPEC §8.1 step 1).
    if item_unit is not None and item_unit.value == sale_unit_str:
        return ConvertResult(
            sale_qty=_ceil_decimal(item_qty),
            conversion_path=f"{_fmt(item_qty)} {item_unit.value} → "
            f"{_ceil_decimal(item_qty)} {item_unit.value} (same unit)",
            confidence_cap=None,
            note=None,
        )

    # Dimension lookup (SPEC §8.1 step 2).
    if item_unit is not None:
        for dim in dimensions:
            if item_unit.value not in dim.units:
                continue
            if sale_unit_str not in dim.sale_units:
                continue
            entry = dim.sale_units[sale_unit_str]
            if not _fields_present(product, entry.uses_fields):
                continue
            base_qty = item_qty * Decimal(dim.units[item_unit.value])
            kwargs = {f: product[f] for f in entry.uses_fields}
            sale_qty = UNIT_CONVERSIONS[entry.function](base_qty, **kwargs)
            path = _build_path(
                from_qty=item_qty,
                from_label=item_unit.value,
                base_qty=base_qty,
                base_unit=dim.base_unit,
                sale_qty=sale_qty,
                sale_unit=sale_unit_str,
                function=entry.function,
                kwargs=kwargs,
            )
            return ConvertResult(sale_qty, path, None, None)

    # Pint fallback (SPEC §8.1 step 3).
    if item_unit is None and pint_convertible and unit_raw:
        normalized = normalize_unit(unit_raw)
        if normalized.pint_token is not None:
            for dim in dimensions:
                if sale_unit_str not in dim.sale_units:
                    continue
                entry = dim.sale_units[sale_unit_str]
                if not _fields_present(product, entry.uses_fields):
                    continue
                base_qty = pint_convert_to(item_qty, normalized.pint_token, dim.base_unit)
                if base_qty is None:
                    continue
                kwargs = {f: product[f] for f in entry.uses_fields}
                sale_qty = UNIT_CONVERSIONS[entry.function](base_qty, **kwargs)
                pint_label = _PINT_DISPLAY.get(normalized.pint_token, normalized.pint_token)
                path = _build_path(
                    from_qty=item_qty,
                    from_label=pint_label,
                    base_qty=base_qty,
                    base_unit=dim.base_unit,
                    sale_qty=sale_qty,
                    sale_unit=sale_unit_str,
                    function=entry.function,
                    kwargs=kwargs,
                )
                return ConvertResult(
                    sale_qty=sale_qty,
                    conversion_path=path,
                    confidence_cap="medium",
                    note="unit_from_pint",
                )

    return ConvertResult(None, None, None, "unit_conversion_unavailable")


def _fields_present(product: dict[str, Any], names: tuple[str, ...]) -> bool:
    return all(product.get(n) is not None for n in names)


def _fmt(x: Decimal) -> str:
    """Format a Decimal for conversion_path display: no trailing zeros, no bare dot."""
    s = str(x)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _fmt_base(x: Decimal) -> str:
    """Format an intermediate base-unit quantity: up to 4 decimals, trim zeros."""
    s = str(x.quantize(Decimal("0.0001")))
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def _build_path(
    *,
    from_qty: Decimal,
    from_label: str,
    base_qty: Decimal,
    base_unit: str,
    sale_qty: int,
    sale_unit: str,
    function: str,
    kwargs: dict[str, Any],
) -> str:
    """Assemble the human-readable conversion trail.

    No intermediate step is shown when from_label already equals base_unit
    (the LF→EA case). Pint recoveries and SQ/MBF normalizations show the
    base-unit intermediate so the reader can audit the math.
    """
    suffix = _path_suffix(function, sale_unit, kwargs)
    if from_label == base_unit:
        return f"{_fmt(from_qty)} {from_label} → {sale_qty} {sale_unit} {suffix}".rstrip()
    return (
        f"{_fmt(from_qty)} {from_label} → {_fmt_base(base_qty)} {base_unit} "
        f"→ {sale_qty} {sale_unit} {suffix}"
    ).rstrip()


def _path_suffix(function: str, sale_unit: str, kwargs: dict[str, Any]) -> str:
    if function == "length_to_pieces":
        return f"(length_ft = {kwargs['length_ft']})"
    if function == "area_to_coverage_units":
        noun = _SALE_UNIT_NOUN.get(sale_unit, sale_unit.lower())
        return f"(coverage {kwargs['coverage_sqft']} SF/{noun})"
    if function == "squares_to_bundles":
        return f"(pack_qty = {kwargs['pack_qty']})"
    if function == "board_feet_to_pieces":
        bfp = _bf_per_piece(kwargs["nominal_size"], kwargs["length_ft"])
        return f"({kwargs['nominal_size']}x{kwargs['length_ft']} = {_fmt(bfp)} BF/piece)"
    if function == "pieces_to_boxes":
        return f"(pack_qty = {kwargs['pack_qty']})"
    return ""
