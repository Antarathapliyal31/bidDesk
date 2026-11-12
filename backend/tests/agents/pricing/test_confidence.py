"""`confidence.py` tests — pure, no I/O (SPEC §8.3 step 8)."""

from __future__ import annotations

from app.agents.pricing.confidence import ConfidenceInputs, compute_confidence
from app.contracts.common import ConfidenceLevel
from app.contracts.pricing import MatchAssumption

_MATCHABLE = frozenset({"grade", "species", "treatment", "length_ft"})


def _inputs(**over) -> ConfidenceInputs:
    defaults = dict(
        is_substitute=False,
        attribute_mismatches=[],
        assumptions=[],
        matchable_fields=_MATCHABLE,
        item_verified=True,
        read_via="text",
        unit_via_pint=False,
    )
    defaults.update(over)
    return ConfidenceInputs(**defaults)


class TestHigh:
    def test_perfect_match_is_high(self) -> None:
        result = compute_confidence(_inputs())
        assert result.level == ConfidenceLevel.high
        assert result.reasons == []


class TestMedium:
    def test_assumption_on_matchable_is_medium(self) -> None:
        result = compute_confidence(
            _inputs(assumptions=[MatchAssumption(field="grade", value="#2", reason="default")])
        )
        assert result.level == ConfidenceLevel.medium
        assert any("grade" in r for r in result.reasons)

    def test_assumption_on_non_matchable_stays_high(self) -> None:
        """Only assumptions on matchable fields knock us down — a
        note-level assumption shouldn't downgrade match certainty."""

        result = compute_confidence(
            _inputs(assumptions=[MatchAssumption(field="pack_qty", value=1, reason="test")])
        )
        assert result.level == ConfidenceLevel.high

    def test_vision_is_medium(self) -> None:
        result = compute_confidence(_inputs(read_via="vision"))
        assert result.level == ConfidenceLevel.medium
        assert any("scanned" in r for r in result.reasons)

    def test_pint_fallback_is_medium(self) -> None:
        result = compute_confidence(_inputs(unit_via_pint=True))
        assert result.level == ConfidenceLevel.medium
        assert any("pint" in r for r in result.reasons)


class TestLow:
    def test_substitute_is_low(self) -> None:
        result = compute_confidence(_inputs(is_substitute=True))
        assert result.level == ConfidenceLevel.low
        assert any("Substituted" in r for r in result.reasons)

    def test_mismatch_is_low(self) -> None:
        result = compute_confidence(
            _inputs(attribute_mismatches=["grade: requested '#1', catalog has '#2'"])
        )
        assert result.level == ConfidenceLevel.low
        assert any("mismatch" in r for r in result.reasons)

    def test_unverified_is_low(self) -> None:
        result = compute_confidence(_inputs(item_verified=False))
        assert result.level == ConfidenceLevel.low
        assert any("text-verified" in r for r in result.reasons)

    def test_low_absorbs_medium_reasons_too(self) -> None:
        """Both low and medium triggers set — final is low but
        medium reasons still surface (so the trace is complete)."""

        result = compute_confidence(
            _inputs(
                is_substitute=True,
                read_via="vision",
                assumptions=[MatchAssumption(field="grade", value="#2", reason="default")],
            )
        )
        assert result.level == ConfidenceLevel.low
        text = " | ".join(result.reasons)
        assert "Substituted" in text
        assert "scanned" in text
        assert "grade" in text
