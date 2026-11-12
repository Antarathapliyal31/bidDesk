"""Least-waste length picker tests (DECISIONS #24)."""

from __future__ import annotations

from decimal import Decimal

from app.agents.pricing.length_picker import (
    LengthPick,
    NoCandidateMatch,
    pick_best_waste,
)

# Synthetic SPF 2x6 family — same nominal_size/species/grade,
# varying length_ft. Mirrors what query_catalog returns.
_SPF_2X6 = [
    {
        "sku": "LUM-2X6-8-SPF-2",
        "nominal_size": "2x6",
        "species": "SPF",
        "grade": "#2",
        "treatment": "none",
        "fire_rating": "none",
        "moisture_resistant": 0,
        "category": "dimensional_lumber",
        "length_ft": 8,
    },
    {
        "sku": "LUM-2X6-10-SPF-2",
        "nominal_size": "2x6",
        "species": "SPF",
        "grade": "#2",
        "treatment": "none",
        "fire_rating": "none",
        "moisture_resistant": 0,
        "category": "dimensional_lumber",
        "length_ft": 10,
    },
    {
        "sku": "LUM-2X6-12-SPF-2",
        "nominal_size": "2x6",
        "species": "SPF",
        "grade": "#2",
        "treatment": "none",
        "fire_rating": "none",
        "moisture_resistant": 0,
        "category": "dimensional_lumber",
        "length_ft": 12,
    },
    {
        "sku": "LUM-2X6-14-SPF-2",
        "nominal_size": "2x6",
        "species": "SPF",
        "grade": "#2",
        "treatment": "none",
        "fire_rating": "none",
        "moisture_resistant": 0,
        "category": "dimensional_lumber",
        "length_ft": 14,
    },
    {
        "sku": "LUM-2X6-16-SPF-2",
        "nominal_size": "2x6",
        "species": "SPF",
        "grade": "#2",
        "treatment": "none",
        "fire_rating": "none",
        "moisture_resistant": 0,
        "category": "dimensional_lumber",
        "length_ft": 16,
    },
]


class TestExactMatch:
    def test_12_lf_picks_12_ft_zero_waste(self) -> None:
        result = pick_best_waste(
            Decimal("12"), submitted_sku="LUM-2X6-8-SPF-2", candidates=_SPF_2X6
        )
        assert isinstance(result, LengthPick)
        assert result.length_ft == 12
        assert result.pieces == 1
        assert result.waste_ft == Decimal("0")
        assert result.sku == "LUM-2X6-12-SPF-2"


class TestTieBreakShorter:
    def test_tie_picks_shorter_length(self) -> None:
        """24 LF: 8 ft → 3 pieces waste 0, 12 ft → 2 pieces waste 0.
        Both zero waste — tie goes to the shorter length (= 8 ft).
        """

        result = pick_best_waste(
            Decimal("24"), submitted_sku="LUM-2X6-16-SPF-2", candidates=_SPF_2X6
        )
        assert isinstance(result, LengthPick)
        assert result.waste_ft == Decimal("0")
        assert result.length_ft == 8
        assert result.pieces == 3

    def test_fascia_36_lf_ties_on_waste_4(self) -> None:
        """Trim 8 ft vs 10 ft on 36 LF both yield waste=4 ft
        (ceil(36/8)=5 → 40 waste 4; ceil(36/10)=4 → 40 waste 4).
        Tie → 8 ft."""

        trim_8 = {
            "sku": "TRM-1X6-8-SPF-2",
            "nominal_size": "1x6",
            "species": "SPF",
            "grade": "#2",
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "category": "trim",
            "length_ft": 8,
        }
        trim_10 = {**trim_8, "sku": "TRM-1X6-10-SPF-2", "length_ft": 10}
        result = pick_best_waste(
            Decimal("36"), submitted_sku="TRM-1X6-10-SPF-2", candidates=[trim_8, trim_10]
        )
        assert isinstance(result, LengthPick)
        assert result.length_ft == 8
        assert result.pieces == 5
        assert result.waste_ft == Decimal("4")


class TestMinimumWaste:
    def test_17_lf_picks_18_shortest_covering(self) -> None:
        """Only candidates that keep piece-count sensible win.
        17 LF across 8/10/12/14/16/18:
          8 → 3 pcs, 24 ft, waste 7
          10 → 2 pcs, 20 ft, waste 3
          12 → 2 pcs, 24 ft, waste 7
          14 → 2 pcs, 28 ft, waste 11
          16 → 2 pcs, 32 ft, waste 15
          (no 18 in _SPF_2X6; would have been 1 pc waste 1, the true winner)
        Winner here: 10 ft (waste 3)."""

        result = pick_best_waste(
            Decimal("17"), submitted_sku="LUM-2X6-16-SPF-2", candidates=_SPF_2X6
        )
        assert isinstance(result, LengthPick)
        assert result.length_ft == 10
        assert result.pieces == 2
        assert result.waste_ft == Decimal("3")


class TestFamilyScope:
    def test_cross_family_ignored(self) -> None:
        """Candidate pool mixes 2x6 SPF (model's family) with a
        rogue 2x8. Picker only considers the submitted SKU's
        family; the 2x8 is ignored."""

        rogue = {
            "sku": "LUM-2X8-10-SPF-2",
            "nominal_size": "2x8",  # different nominal_size
            "species": "SPF",
            "grade": "#2",
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "category": "dimensional_lumber",
            "length_ft": 10,
        }
        result = pick_best_waste(
            Decimal("24"),
            submitted_sku="LUM-2X6-8-SPF-2",
            candidates=[*_SPF_2X6, rogue],
        )
        assert isinstance(result, LengthPick)
        assert result.sku.startswith("LUM-2X6-")


class TestNoCandidate:
    def test_submitted_sku_absent(self) -> None:
        result = pick_best_waste(Decimal("12"), submitted_sku="NOT-IN-THE-SET", candidates=_SPF_2X6)
        assert isinstance(result, NoCandidateMatch)

    def test_family_without_length_ft(self) -> None:
        """A product family that doesn't carry length_ft (e.g.
        drywall sheets) returns NoCandidateMatch — nothing for
        the picker to work with."""

        drywall = [
            {
                "sku": "DRY-1_2-48X96",
                "nominal_size": None,
                "species": None,
                "grade": None,
                "treatment": "none",
                "fire_rating": "none",
                "moisture_resistant": 0,
                "category": "drywall",
                "length_ft": None,
            }
        ]
        result = pick_best_waste(Decimal("100"), submitted_sku="DRY-1_2-48X96", candidates=drywall)
        assert isinstance(result, NoCandidateMatch)

    def test_non_positive_total_ft(self) -> None:
        result = pick_best_waste(Decimal("0"), submitted_sku="LUM-2X6-8-SPF-2", candidates=_SPF_2X6)
        assert isinstance(result, NoCandidateMatch)
