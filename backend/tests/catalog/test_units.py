"""units.py tests — normalization, dimensions, pint fallback, identity, failure.

Matches every assertion in SPEC §12.1's Units + Pricing-verify bullets.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.catalog.units import (
    OUR_TABLE,
    UNIT_CONVERSIONS,
    ConvertResult,
    Dimension,
    NormalizedUnit,
    SaleUnitSpec,
    Unit,
    area_to_coverage_units,
    board_feet_to_pieces,
    canonicalize,
    convert_item_to_sale_qty,
    length_to_pieces,
    normalize_unit,
    pint_convert_to,
    squares_to_bundles,
)

# ---------- canonicalize ----------


class TestCanonicalize:
    def test_strips_periods(self) -> None:
        assert canonicalize("Sq. Ft.") == "sq ft"

    def test_collapses_whitespace(self) -> None:
        assert canonicalize("  Sq.  Ft.  ") == "sq ft"

    def test_lowercases(self) -> None:
        assert canonicalize("HOURS") == "hours"

    def test_nfkc_folds_superscripts(self) -> None:
        # ² (U+00B2) decomposes to ASCII 2 via NFKC, so "yd²" canonicalizes to "yd2".
        assert canonicalize("yd²") == "yd2"
        assert canonicalize("YD²") == "yd2"
        assert canonicalize("m²") == "m2"

    def test_nfkc_folds_fullwidth_and_nbsp(self) -> None:
        # Fullwidth letters and non-breaking spaces fold under NFKC.
        assert canonicalize("Ｓｑ. Ｆｔ.") == "sq ft"

    def test_hyphen_becomes_space(self) -> None:
        assert canonicalize("Type-X") == "type x"
        assert canonicalize("Lin-Ft") == "lin ft"

    def test_en_dash_becomes_space(self) -> None:
        assert canonicalize("spruce–pine–fir") == "spruce pine fir"

    def test_em_dash_becomes_space(self) -> None:
        assert canonicalize("fire—retardant") == "fire retardant"

    def test_inch_double_quote_preserved(self) -> None:
        # The semantic layer's thickness_in synonyms use inch-mark keys like
        # '1/2"'. canonicalize must not touch " (it also means feet/inches
        # elsewhere), so this input lands on the YAML key verbatim.
        assert canonicalize('1/2"') == '1/2"'
        assert canonicalize('5/8"') == '5/8"'

    def test_period_strip_without_inserted_space(self) -> None:
        # "No.2" has no space around the period, so strip must not insert one;
        # the semantic layer stores the canonical key as "no2".
        assert canonicalize("No.2") == "no2"
        assert canonicalize("No. 2") == "no 2"  # space variant → "no 2"


# ---------- our table ----------


class TestOurTableHits:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # SPEC §12.1 explicit examples
            ("Sq. Ft.", Unit.SF),
            ("Lin. Ft.", Unit.LF),
            ("pc", Unit.EA),
            ("PCS", Unit.EA),
            ("pieces", Unit.EA),
            ("sheets", Unit.SHT),
            (["40", "hours"][-1], Unit.HR),  # just "hours"
            ("pounds", Unit.LB),
            ("gallons", Unit.GAL),
            # Full table spot-checks
            ("each", Unit.EA),
            ("square", Unit.SQ),
            ("squares", Unit.SQ),
            ("lin ft", Unit.LF),
            ("linear feet", Unit.LF),
            ("lineal feet", Unit.LF),
            ("sqft", Unit.SF),
            ("square foot", Unit.SF),
            ("bd ft", Unit.BF),
            ("m bf", Unit.MBF),
            ("bundle", Unit.BDL),
            ("bdl", Unit.BDL),
            ("rolls", Unit.ROLL),
            ("bag", Unit.BAG),
            ("boxes", Unit.BOX),
            ("lb", Unit.LB),
            ("lbs", Unit.LB),
            ("pound", Unit.LB),
            ("gal", Unit.GAL),
            ("gallon", Unit.GAL),
            ("hr", Unit.HR),
            ("hrs", Unit.HR),
            ("hour", Unit.HR),
        ],
    )
    def test_table_match(self, raw: str, expected: Unit) -> None:
        r = normalize_unit(raw)
        assert r.unit is expected
        assert r.pint_token is None
        assert r.unknown is False

    def test_pc_never_parsec(self) -> None:
        for form in ("pc", "PC", "pcs", "PCS", "piece", "pieces"):
            r = normalize_unit(form)
            assert r.unit is Unit.EA, f"{form!r} must resolve to EA, not pint parsec"

    def test_pc_not_in_unit_enum(self) -> None:
        # Guard against anyone adding PC back; SPEC §6.4.3 removed it.
        assert "PC" not in {u.value for u in Unit}


# ---------- pint allowlist ----------


class TestPintAllowlist:
    def test_bare_m_rejected(self) -> None:
        # SPEC §12.1: "2 M" → flagged, never parsed as metres.
        for form in ("m", "M"):
            r = normalize_unit(form)
            assert r.unit is None
            assert r.pint_token is None
            assert r.unknown is True

    @pytest.mark.parametrize("form", ["meter", "meters", "metre", "metres"])
    def test_spelled_metres_recognized(self, form: str) -> None:
        r = normalize_unit(form)
        assert r.unit is None
        assert r.pint_token is not None
        assert r.unknown is False

    @pytest.mark.parametrize("form", ["square yards", "sq yds", "square yard", "sq yd"])
    def test_square_yards_rewritten_to_yd2(self, form: str) -> None:
        r = normalize_unit(form)
        assert r.pint_token == "yd**2"

    @pytest.mark.parametrize("form", ["yd²", "ft²", "m²"])
    def test_unicode_superscripts(self, form: str) -> None:
        r = normalize_unit(form)
        assert r.pint_token is not None
        assert r.unknown is False

    @pytest.mark.parametrize(
        ("form", "expected_token"),
        [
            ("ft^2", "ft**2"),
            ("yd^2", "yd**2"),
            ("m^2", "m**2"),
            ("m²", "m**2"),
            ("sq m", "m**2"),
            ("square meter", "m**2"),
            ("square meters", "m**2"),
            ("square metre", "m**2"),
            ("square metres", "m**2"),
        ],
    )
    def test_caret_and_written_area_forms(self, form: str, expected_token: str) -> None:
        r = normalize_unit(form)
        assert r.pint_token == expected_token

    def test_type_dash_x_canonicalizes_through_normalize(self) -> None:
        # Not a pint unit — just confirming canonicalize path produces "type x".
        # Used by the semantic-layer synonym table (fire_rating: "type x" → type_x).
        assert canonicalize("Type-X") == "type x"

    def test_lin_dash_ft_resolves_to_LF(self) -> None:
        # Hyphenated form must hit OUR_TABLE["lin ft"] after canonicalize.
        r = normalize_unit("Lin-Ft")
        assert r.unit is Unit.LF

    def test_inch_abbreviation_rewritten(self) -> None:
        # `in` is defensively mapped to `inch` before pint parses.
        r = normalize_unit("in")
        assert r.pint_token == "inch"

    def test_inches_direct(self) -> None:
        r = normalize_unit("inches")
        assert r.pint_token == "inches"

    @pytest.mark.parametrize("form", ["skein", "furlong", "foobar", ""])
    def test_unknown_units(self, form: str) -> None:
        r = normalize_unit(form)
        assert r.unit is None
        assert r.pint_token is None
        assert r.unknown is True


# ---------- pint convert ----------


class TestPintConvert:
    def test_10_meters_to_lf(self) -> None:
        # SPEC: 10 meters → 32.8084 LF (full Decimal; display rounds to 4 dp)
        result = pint_convert_to(Decimal("10"), "meter", "LF")
        assert result is not None
        assert abs(result - Decimal("32.8084")) < Decimal("0.0001")

    def test_12_yd2_to_sf(self) -> None:
        # SPEC: 12 yd² → 108 SF (exact)
        result = pint_convert_to(Decimal("12"), "yd**2", "SF")
        assert result == Decimal("108")

    def test_12_inches_to_lf(self) -> None:
        # 12 in → 1 ft, after pint parses `inch`.
        result = pint_convert_to(Decimal("12"), "inch", "LF")
        assert result == Decimal("1")

    def test_12_inches_long_form(self) -> None:
        result = pint_convert_to(Decimal("12"), "inches", "LF")
        assert result == Decimal("1")

    def test_length_to_area_dimensionality_mismatch(self) -> None:
        assert pint_convert_to(Decimal("10"), "meter", "SF") is None

    def test_area_to_length_dimensionality_mismatch(self) -> None:
        assert pint_convert_to(Decimal("12"), "yd**2", "LF") is None


# ---------- pure conversion functions ----------


class TestConversionFunctions:
    # SPEC §12.1 documented answers
    def test_length_100lf_16ft_is_7(self) -> None:
        assert length_to_pieces(Decimal("100"), length_ft=16) == 7

    def test_area_32sf_coverage32_is_1(self) -> None:
        assert area_to_coverage_units(Decimal("32"), coverage_sqft=32) == 1

    def test_area_49sf_coverage49_is_1(self) -> None:
        assert area_to_coverage_units(Decimal("49"), coverage_sqft=49) == 1

    def test_area_50sf_coverage49_is_2(self) -> None:
        assert area_to_coverage_units(Decimal("50"), coverage_sqft=49) == 2

    def test_squares_to_bundles_12sq_pack3_is_36(self) -> None:
        # 12 SQ = 1200 SF, pack_qty=3 → ceil(1200/100 * 3) = 36
        assert squares_to_bundles(Decimal("1200"), pack_qty=3) == 36

    def test_squares_to_bundles_exact_round(self) -> None:
        # 1 SQ = 100 SF; pack_qty=3 → 100/100 * 3 = 3 exactly. Without
        # the 6-dp quantize, float-ish Decimal multiplies can leave a
        # trailing "3.000000...1" that ceiling would push to 4. The
        # late-quantize-then-ceil path pins this to 3.
        assert squares_to_bundles(Decimal("100"), pack_qty=3) == 3

    def test_board_feet_1000bf_2x6x16_is_63(self) -> None:
        # bf_per_piece = 16; 1000/16 = 62.5 → 63
        assert board_feet_to_pieces(Decimal("1000"), nominal_size="2x6", length_ft=16) == 63

    def test_board_feet_2000bf_2x6x16_is_125(self) -> None:
        # 2 MBF = 2000 BF; 2000/16 = 125 exact
        assert board_feet_to_pieces(Decimal("2000"), nominal_size="2x6", length_ft=16) == 125

    def test_board_feet_16bf_2x4x8_is_3(self) -> None:
        # bf_per_piece(2x4x8) = (2 * 4 * 8) / 12 = 16/3 ≈ 5.333…
        # 16 / (16/3) = 3 exactly in rationals, but naive Decimal division
        # yields 3.000000000…1 and a straight ceil pushes it to 4. The
        # 6-dp quantize before ceil is what pins this to 3.
        assert board_feet_to_pieces(Decimal("16"), nominal_size="2x4", length_ft=8) == 3

    def test_zero_divisor_raises(self) -> None:
        with pytest.raises(ValueError, match="length_ft must be > 0"):
            length_to_pieces(Decimal("100"), length_ft=0)
        with pytest.raises(ValueError, match="coverage_sqft must be > 0"):
            area_to_coverage_units(Decimal("100"), coverage_sqft=0)
        with pytest.raises(ValueError, match="pack_qty must be > 0"):
            squares_to_bundles(Decimal("100"), pack_qty=0)


# ---------- registry ----------


class TestRegistry:
    def test_four_functions_registered(self) -> None:
        # SPEC §8.1 names four conversion functions; we register exactly four.
        assert set(UNIT_CONVERSIONS.keys()) == {
            "length_to_pieces",
            "area_to_coverage_units",
            "squares_to_bundles",
            "board_feet_to_pieces",
        }

    def test_registered_match_module(self) -> None:
        assert UNIT_CONVERSIONS["length_to_pieces"] is length_to_pieces
        assert UNIT_CONVERSIONS["area_to_coverage_units"] is area_to_coverage_units
        assert UNIT_CONVERSIONS["squares_to_bundles"] is squares_to_bundles
        assert UNIT_CONVERSIONS["board_feet_to_pieces"] is board_feet_to_pieces

    def test_our_table_only_uses_defined_enum(self) -> None:
        for v in OUR_TABLE.values():
            assert isinstance(v, Unit)


# ---------- NormalizedUnit shape ----------


class TestNormalizedUnitShape:
    def test_table_hit_shape(self) -> None:
        r = normalize_unit("sq ft")
        assert r == NormalizedUnit(unit=Unit.SF, pint_token=None, unknown=False)
        assert r.pint_convertible is False

    def test_pint_hit_shape(self) -> None:
        r = normalize_unit("meters")
        assert r.unit is None
        assert r.pint_token is not None
        assert r.unknown is False
        assert r.pint_convertible is True

    def test_unknown_shape(self) -> None:
        r = normalize_unit("skein")
        assert r == NormalizedUnit(unit=None, pint_token=None, unknown=True)
        assert r.pint_convertible is False


# ---------- convert_item_to_sale_qty ----------


def _dims() -> list[Dimension]:
    """The three SPEC §8.1 dimensions, built by hand to keep this test file
    independent of semantic_layer.py."""
    return [
        Dimension(
            name="length",
            base_unit="LF",
            units={"LF": 1},
            sale_units={"EA": SaleUnitSpec("length_to_pieces", ("length_ft",))},
        ),
        Dimension(
            name="area",
            base_unit="SF",
            units={"SF": 1, "SQ": 100},
            sale_units={
                "SHT": SaleUnitSpec("area_to_coverage_units", ("coverage_sqft",)),
                "BDL": SaleUnitSpec("squares_to_bundles", ("pack_qty",)),
                "BAG": SaleUnitSpec("area_to_coverage_units", ("coverage_sqft",)),
                "ROLL": SaleUnitSpec("area_to_coverage_units", ("coverage_sqft",)),
            },
        ),
        Dimension(
            name="board_measure",
            base_unit="BF",
            units={"BF": 1, "MBF": 1000},
            sale_units={
                "EA": SaleUnitSpec("board_feet_to_pieces", ("nominal_size", "length_ft")),
            },
        ),
    ]


class TestConvertIdentity:
    def test_ea_to_ea_identity(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("40"),
            item_unit=Unit.EA,
            unit_raw="EA",
            pint_convertible=False,
            product={"sale_unit": "EA"},
            dimensions=_dims(),
        )
        # SPEC: identity path format
        assert r == ConvertResult(
            sale_qty=40,
            conversion_path="40 EA → 40 EA (same unit)",
            confidence_cap=None,
            note=None,
        )

    def test_bag_to_bag_identity(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("12"),
            item_unit=Unit.BAG,
            unit_raw="bags",
            pint_convertible=False,
            product={"sale_unit": "BAG", "coverage_sqft": 49},
            dimensions=_dims(),
        )
        assert r.conversion_path == "12 BAG → 12 BAG (same unit)"
        assert r.sale_qty == 12
        assert r.confidence_cap is None


class TestConvertDimensionPath:
    def test_length_lf_to_ea(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("100"),
            item_unit=Unit.LF,
            unit_raw="LF",
            pint_convertible=False,
            product={"sale_unit": "EA", "length_ft": 16},
            dimensions=_dims(),
        )
        assert r.sale_qty == 7
        assert r.conversion_path == "100 LF → 7 EA (length_ft = 16)"
        assert r.note is None

    def test_area_sq_to_bdl(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("12"),
            item_unit=Unit.SQ,
            unit_raw="SQ",
            pint_convertible=False,
            product={"sale_unit": "BDL", "pack_qty": 3},
            dimensions=_dims(),
        )
        assert r.sale_qty == 36
        # Intermediate SF shown because item_unit (SQ) != base_unit (SF).
        assert r.conversion_path == "12 SQ → 1200 SF → 36 BDL (pack_qty = 3)"

    def test_area_sf_to_sht(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("32"),
            item_unit=Unit.SF,
            unit_raw="SF",
            pint_convertible=False,
            product={"sale_unit": "SHT", "coverage_sqft": 32},
            dimensions=_dims(),
        )
        assert r.sale_qty == 1
        assert r.conversion_path == "32 SF → 1 SHT (coverage 32 SF/sheet)"

    def test_area_sf_to_bag_49(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("49"),
            item_unit=Unit.SF,
            unit_raw="SF",
            pint_convertible=False,
            product={"sale_unit": "BAG", "coverage_sqft": 49},
            dimensions=_dims(),
        )
        assert r.sale_qty == 1

    def test_area_sf_to_bag_50(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("50"),
            item_unit=Unit.SF,
            unit_raw="SF",
            pint_convertible=False,
            product={"sale_unit": "BAG", "coverage_sqft": 49},
            dimensions=_dims(),
        )
        assert r.sale_qty == 2

    def test_board_measure_mbf_to_ea(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("2"),
            item_unit=Unit.MBF,
            unit_raw="MBF",
            pint_convertible=False,
            product={"sale_unit": "EA", "nominal_size": "2x6", "length_ft": 16},
            dimensions=_dims(),
        )
        assert r.sale_qty == 125
        assert r.conversion_path == "2 MBF → 2000 BF → 125 EA (2x6x16 = 16 BF/piece)"


class TestConvertPintFallback:
    def test_exact_10_meters_against_8ft_board(self) -> None:
        # SPEC §12.1 exact assertion.
        r = convert_item_to_sale_qty(
            item_qty=Decimal("10"),
            item_unit=None,
            unit_raw="meters",
            pint_convertible=True,
            product={"sale_unit": "EA", "length_ft": 8},
            dimensions=_dims(),
        )
        assert r == ConvertResult(
            sale_qty=5,
            conversion_path="10 m → 32.8084 LF → 5 EA (length_ft = 8)",
            confidence_cap="medium",
            note="unit_from_pint",
        )

    def test_12_yd2_against_coverage_32(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("12"),
            item_unit=None,
            unit_raw="yd²",
            pint_convertible=True,
            product={"sale_unit": "SHT", "coverage_sqft": 32},
            dimensions=_dims(),
        )
        assert r.sale_qty == 4
        # Display still uses "yd²" even though canonicalize folds it internally.
        assert r.conversion_path == "12 yd² → 108 SF → 4 SHT (coverage 32 SF/sheet)"
        assert r.confidence_cap == "medium"
        assert r.note == "unit_from_pint"

    def test_40_m2_unicode_against_coverage_32(self) -> None:
        # 40 m² ≈ 430.5564 ft²; coverage=32 → ceil(430.5564/32) = ceil(13.455) = 14 SHT.
        r = convert_item_to_sale_qty(
            item_qty=Decimal("40"),
            item_unit=None,
            unit_raw="m²",
            pint_convertible=True,
            product={"sale_unit": "SHT", "coverage_sqft": 32},
            dimensions=_dims(),
        )
        assert r.sale_qty == 14
        assert r.note == "unit_from_pint"
        assert r.confidence_cap == "medium"

    def test_40_m_caret_2_against_coverage_32(self) -> None:
        # Caret form must produce the same result as the unicode form.
        r = convert_item_to_sale_qty(
            item_qty=Decimal("40"),
            item_unit=None,
            unit_raw="m^2",
            pint_convertible=True,
            product={"sale_unit": "SHT", "coverage_sqft": 32},
            dimensions=_dims(),
        )
        assert r.sale_qty == 14
        assert r.note == "unit_from_pint"

    def test_bare_M_remains_unknown_end_to_end(self) -> None:
        # Normalizer leaves "M" unknown, so pricing with pint_convertible=False
        # (the realistic path) flags the line.
        assert normalize_unit("M").unknown is True
        r = convert_item_to_sale_qty(
            item_qty=Decimal("2"),
            item_unit=None,
            unit_raw="M",
            pint_convertible=False,
            product={"sale_unit": "EA", "length_ft": 8},
            dimensions=_dims(),
        )
        assert r.sale_qty is None
        assert r.note == "unit_conversion_unavailable"

    def test_bare_M_with_buggy_pint_convertible_still_fails(self) -> None:
        # Adversarial: even if an upstream bug stamps unit_pint_convertible on
        # an item whose unit_raw isn't actually pint-recognized, convert()
        # re-normalizes and finds no token. The product field availability
        # doesn't matter — the pint lookup never produces a base_qty.
        r = convert_item_to_sale_qty(
            item_qty=Decimal("2"),
            item_unit=None,
            unit_raw="M",
            pint_convertible=True,  # lie about convertibility
            product={"sale_unit": "EA", "length_ft": 8},
            dimensions=_dims(),
        )
        assert r.sale_qty is None
        assert r.conversion_path is None
        assert r.confidence_cap is None
        assert r.note == "unit_conversion_unavailable"

    def test_24_inches_against_1ft_board(self) -> None:
        # 24 in → 2 ft; length_ft=1 → 2 pieces.
        r = convert_item_to_sale_qty(
            item_qty=Decimal("24"),
            item_unit=None,
            unit_raw="inches",
            pint_convertible=True,
            product={"sale_unit": "EA", "length_ft": 1},
            dimensions=_dims(),
        )
        assert r.sale_qty == 2
        assert r.confidence_cap == "medium"
        assert r.note == "unit_from_pint"


class TestConvertFailure:
    def test_non_convertible_pair_flags(self) -> None:
        # LB item against BOX product — no dimension matches.
        r = convert_item_to_sale_qty(
            item_qty=Decimal("5"),
            item_unit=Unit.LB,
            unit_raw="lb",
            pint_convertible=False,
            product={"sale_unit": "BOX"},
            dimensions=_dims(),
        )
        assert r == ConvertResult(None, None, None, "unit_conversion_unavailable")

    def test_missing_field_fails(self) -> None:
        # SF → SHT dimension matches, but coverage_sqft is null on the row.
        r = convert_item_to_sale_qty(
            item_qty=Decimal("100"),
            item_unit=Unit.SF,
            unit_raw="SF",
            pint_convertible=False,
            product={"sale_unit": "SHT", "coverage_sqft": None},
            dimensions=_dims(),
        )
        assert r.sale_qty is None
        assert r.note == "unit_conversion_unavailable"

    def test_missing_sale_unit_fails(self) -> None:
        r = convert_item_to_sale_qty(
            item_qty=Decimal("100"),
            item_unit=Unit.SF,
            unit_raw="SF",
            pint_convertible=False,
            product={"sale_unit": None},
            dimensions=_dims(),
        )
        assert r.note == "unit_conversion_unavailable"

    def test_pint_dimensionality_mismatch_fails(self) -> None:
        # "meters" is length; product is SHT (area dimension).
        r = convert_item_to_sale_qty(
            item_qty=Decimal("10"),
            item_unit=None,
            unit_raw="meters",
            pint_convertible=True,
            product={"sale_unit": "SHT", "coverage_sqft": 32},
            dimensions=_dims(),
        )
        assert r.sale_qty is None
        assert r.note == "unit_conversion_unavailable"


class TestConvertInvariantBreak:
    @pytest.mark.parametrize("qty", [Decimal("0"), Decimal("-1"), Decimal("-0.5")])
    def test_non_positive_qty_raises(self, qty: Decimal) -> None:
        # Analyst (§6.4 step 4) must drop qty≤0 to `unreadable`; reaching
        # pricing with a non-positive qty is an invariant break.
        with pytest.raises(ValueError, match="item_qty must be > 0"):
            convert_item_to_sale_qty(
                item_qty=qty,
                item_unit=Unit.LF,
                unit_raw="LF",
                pint_convertible=False,
                product={"sale_unit": "EA", "length_ft": 16},
                dimensions=_dims(),
            )
