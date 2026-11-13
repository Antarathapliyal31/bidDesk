"""Match-confidence calculator (SPEC §8.3 step 8) — pure, no I/O.

Confidence is **match certainty only**. Review flags
(`sanity_range`, `unit_conversion_unavailable`, shortfall, etc.)
open review independently through `verify_notes_json`.

Levels:
  - high: not substitute; zero mismatches; zero assumptions on
    matchable fields; item verified and read_via='text';
    unit resolved via our table (no pint).
  - medium: not substitute; zero mismatches; but ≥1 assumption,
    item read via vision, or unit resolved via pint fallback.
  - low: substitute, any mismatch, or item unverified.

Each level carries human-readable reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.contracts.common import ConfidenceLevel
from app.contracts.pricing import MatchAssumption


@dataclass(frozen=True)
class ConfidenceInputs:
    is_substitute: bool
    attribute_mismatches: list[str]
    assumptions: list[MatchAssumption]
    matchable_fields: frozenset[str]
    item_verified: bool  # ItemView.source.verified
    read_via: str | None  # "text" | "vision" | None
    unit_via_pint: bool  # True if pint fallback placed the unit
    # True when the analyst dropped the source unit and code
    # inferred EA from the extracted integer count (DECISIONS F14).
    # Caps confidence at medium with an explanatory reason.
    unit_assumed_ea_from_countless_qty: bool = False
    # Ambiguous pick (description resolves to multiple catalog
    # variants; model flagged it). Forces medium (or lower if a
    # low trigger also fires) + an explanatory reason.
    is_ambiguous: bool = False
    ambiguous_reason: str | None = None


@dataclass(frozen=True)
class ConfidenceResult:
    level: ConfidenceLevel
    reasons: list[str] = field(default_factory=list)


def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult:
    """Return `(level, reasons[])` per SPEC §8.3 step 8.

    The classifier is a simple tier check:
      1. Any low-trigger → low (substitute / mismatches / unverified).
      2. Any medium-trigger → medium (assumption on matchable field /
         vision / pint).
      3. Otherwise → high.

    Reasons are collected for every triggering condition, including
    ones that would only have knocked us to a higher-trust tier
    than the final verdict — gives the UI enough to explain why.
    """

    reasons: list[str] = []

    # --- Low triggers ---
    low_reasons: list[str] = []
    if inputs.is_substitute:
        low_reasons.append("Substituted a different product than requested")
    if inputs.attribute_mismatches:
        if len(inputs.attribute_mismatches) == 1:
            low_reasons.append(f"Attribute mismatch: {inputs.attribute_mismatches[0]}")
        else:
            low_reasons.append(f"Attribute mismatches: {', '.join(inputs.attribute_mismatches)}")
    if not inputs.item_verified:
        low_reasons.append("Item quote could not be text-verified")

    # --- Medium triggers (only count if no low triggers) ---
    medium_reasons: list[str] = []
    matchable_assumptions = [a for a in inputs.assumptions if a.field in inputs.matchable_fields]
    for a in matchable_assumptions:
        medium_reasons.append(f"{a.field} not specified — assumed {a.value!r} ({a.reason})")
    if inputs.read_via == "vision":
        medium_reasons.append("Item read from a scanned page (vision)")
    if inputs.unit_via_pint:
        medium_reasons.append("Unit converted via the pint fallback")
    if inputs.unit_assumed_ea_from_countless_qty:
        medium_reasons.append(
            "Source unit missing — code inferred EA from the extracted count"
        )
    if inputs.is_ambiguous:
        reason = inputs.ambiguous_reason or "description resolves to multiple catalog variants"
        medium_reasons.append(f"Ambiguous — {reason}")

    if low_reasons:
        reasons.extend(low_reasons)
        # Also include the softer triggers so the UI/trace can show
        # the full story.
        reasons.extend(medium_reasons)
        return ConfidenceResult(level=ConfidenceLevel.low, reasons=reasons)
    if medium_reasons:
        reasons.extend(medium_reasons)
        return ConfidenceResult(level=ConfidenceLevel.medium, reasons=reasons)
    return ConfidenceResult(level=ConfidenceLevel.high, reasons=[])
