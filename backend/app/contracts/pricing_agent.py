"""Pricing-agent-side contracts (SPEC §8.2).

Separate module from `contracts/pricing.py` (which carries the
public `PricedLineView` / `MatchInfo` / `StockInfo` shapes that
appear in the Bid). This file holds the orchestrator↔pricing
envelopes:
  - `PricingRequest`: what the orchestrator asks for
  - `MatchProposal` + `PricingResponse`: forced-tool shape
    returned by the pricing model
  - `PricingResult`: what the pricing agent hands back to the
    orchestrator — enriched with code-verified prices, stock, and
    confidence (built in verify.py)
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts import Contract, ItemView
from app.contracts.pricing import (
    MatchAssumption,
    PricedLineView,
)

# ---------------------------------------------------------------------------
# Orchestrator → Pricing
# ---------------------------------------------------------------------------


class RequirementSpec(Contract):
    """Flattened requirement passed from the orchestrator to the
    pricing agent. Mirrors `item_requirements` but only the fields
    the model needs to filter on."""

    field: str
    value: str | int | bool


class PricingItemRequest(Contract):
    """One line the orchestrator wants priced. Carries the item
    view (description / quantity_text / unit_raw / source quote)
    and any requirements the orchestrator attached."""

    item: ItemView
    requirements: list[RequirementSpec] = Field(default_factory=list)


class PricingRequest(Contract):
    """Full `PricingRequest` envelope. Batches of ≤8 items per
    delegate call (SPEC §8.2) — the orchestrator splits a larger
    set."""

    branch_id: str
    items: list[PricingItemRequest] = Field(min_length=1, max_length=8)


# ---------------------------------------------------------------------------
# Pricing model → verify (forced tool shape)
# ---------------------------------------------------------------------------


class AmbiguousFlag(BaseModel):
    """Attached to a `MatchProposal` when the chosen SKU resolves
    a genuine ambiguity in the description (e.g. "PT" that could
    be above-ground or ground-contact; "5/8 drywall" that could
    be Type X or regular). Code treats the match as valid but
    forces a review flag + a confidence cap (medium) so the
    estimator sees it. Distinct from `assumptions`:
      - `assumptions` fill in an *unstated* spec from a
        default-safe choice (grade=#2, species=SPF).
      - `ambiguous` records that the *stated* spec names a
        category the catalog splits across — the model made the
        call but it should go to the estimator.
    """

    model_config = ConfigDict(extra="forbid")

    is_ambiguous: bool
    reason: str = Field(max_length=300)


class MatchProposal(BaseModel):
    """`submit_matches.input.results[*]`. Model proposes a SKU or
    None (a valid answer); may list assumptions it made; writes a
    short `searched` sentence for the trace. `ambiguous` is set
    when the match resolves a genuine description ambiguity."""

    item_id: str
    sku: str | None
    is_substitute: bool = False
    substitute_reason: str | None = Field(default=None, max_length=300)
    assumptions: list[MatchAssumption] = Field(default_factory=list)
    ambiguous: AmbiguousFlag | None = None
    searched: str = Field(max_length=300)


class SubmitMatchesPayload(BaseModel):
    """The forced-tool input shape. `extraction.py` converts raw
    SDK output into this; `verify.py` consumes it."""

    results: list[MatchProposal]


# ---------------------------------------------------------------------------
# Pricing → orchestrator (post-verify)
# ---------------------------------------------------------------------------


class PricingLineResult(Contract):
    """One priced line after code-verify. Carries `priced` (the
    `PricedLineView` the SSE stream uses), `stock` (a loose
    snapshot — Phase 5 bid-assembly promotes this into the
    invariant-enforcing `StockInfo` once the full Bid is built),
    and `attribute_mismatches` / `assumptions` for the trace.

    `stock` is `dict[str, Any] | None` during the orchestrator
    handoff instead of the strict `StockInfo` — StockInfo enforces
    sorted-by-SPEC-§13 transfer_options and `covers_shortfall`
    flags, which are concerns of the final Bid, not the raw
    pricing snapshot.
    """

    item_id: str
    status: Literal[
        "matched",
        "substitute",
        "no_match",
        "needs_review",
        "failed",
        "skipped",
    ]
    priced: PricedLineView
    stock: dict[str, Any] | None = None
    attribute_mismatches: list[str] = Field(default_factory=list)
    assumptions: list[MatchAssumption] = Field(default_factory=list)
    # Review flags (SPEC §8.3). Disjoint from `confidence_reasons`
    # on `priced` (match certainty only). Known values today:
    # `sanity_range:<max>`, `unit_conversion_unavailable`,
    # `hallucinated_sku`, `max_steps_exceeded`,
    # `quantity_missing`, `ambiguous_pick`, `code_chose_length`,
    # `candidates_available` (when needs_review). The bid-assembly
    # layer (Phase 5) persists these into `verify_notes_json`.
    verify_notes: list[str] = Field(default_factory=list)
    is_substitute: bool = False
    substitute_reason: str | None = None


class ItemError(Contract):
    """Per-item failure carried on a partial `PricingResult`.

    `ref` is the item_id — the field name matches `AgentError.ref`
    so downstream consumers can treat these uniformly. `kind` is
    the short classification code; `message` is a one-line reason
    (max 300 chars so it round-trips through the SSE).
    """

    ref: str
    kind: Literal[
        "candidate_set",
        "max_steps_exceeded",
        "hallucinated_sku",
        "missing_proposal",
    ]
    message: str = Field(max_length=300)


class PricingResult(Contract):
    """Return type of the Pricing Agent `AgentSpec`. One `results`
    entry per item in the request (missing items → status='failed').

    `status='partial'` when any items were dropped by code
    guardrails that a retry couldn't clear (candidate-set
    violation that persisted, hallucinated SKUs, or max_steps hit);
    `item_errors` carries one entry per dropped item.
    """

    status: Literal["ok", "partial"] = "ok"
    results: list[PricingLineResult]
    item_errors: list[ItemError] = Field(default_factory=list)
