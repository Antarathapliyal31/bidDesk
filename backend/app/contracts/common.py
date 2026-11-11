"""Shared contract primitives: enums, SourceRef, BidDiff, Decimal helper,
`Cents` / `Count` numeric aliases.

Every contract model in `app/contracts/*` inherits `Contract`, which
pins `extra="forbid"` per CLAUDE.md §3 so a stray field at a boundary
surfaces as a ValidationError, never a silent drop.

Rationale for lax global config + targeted strictness:
Pydantic v2's `strict=True` applied to the whole model still accepts
enum string values via `model_validate_json`; it only rejects the
string form on `model_validate(dict)` (python mode). Keeping the
global config lax means SSE replay and `model_dump(mode='json')`
round-trips work without plumbing a special path. The invariants
that genuinely care about primitive identity — integer cents,
counts, booleans — use `StrictInt` / `StrictBool` per field via the
`Cents` and `Count` aliases below, so `unit_price_cents="325"` and
`verified="true"` still fail loudly.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

# ---------------------------------------------------------------------------
# Base config
# ---------------------------------------------------------------------------

CONTRACT_CONFIG = ConfigDict(extra="forbid", frozen=False)


class Contract(BaseModel):
    """Every contract model inherits this so the config is uniform."""

    model_config = CONTRACT_CONFIG


# ---------------------------------------------------------------------------
# Decimal fields: JSON string ⇄ Decimal
# ---------------------------------------------------------------------------
# SPEC §9.1 keeps quantities as `Decimal` so int math never rounds a 1.5x4
# shortfall to 1x4. Across the wire they travel as strings (API_CONTRACT.md
# §Conventions) so JS can't float them.
#
# `_coerce_to_decimal` only accepts int + str forms. Floats are rejected on
# purpose — golden rule 8 forbids float money; the string form is the
# contract. NaN / Infinity are rejected even though Decimal accepts them,
# because the whole point of using Decimal here is fixed-point arithmetic
# on quantities.


def _coerce_to_decimal(v: Any) -> Any:
    if isinstance(v, Decimal):
        if not v.is_finite():
            raise ValueError("Decimal must be finite (no NaN or Infinity)")
        return v
    if isinstance(v, int) and not isinstance(v, bool):
        return Decimal(v)
    if isinstance(v, str):
        try:
            d = Decimal(v)
        except InvalidOperation as exc:
            raise ValueError(f"not a valid Decimal: {v!r}") from exc
        if not d.is_finite():
            raise ValueError("Decimal must be finite (no NaN or Infinity)")
        return d
    return v


def _serialize_decimal(d: Decimal) -> str:
    # `format(d, "f")` keeps fixed-point without exponent — "1E+3" would
    # round-trip through JSON as a string that some parsers reject.
    return format(d, "f")


DecimalStr = Annotated[
    Decimal,
    BeforeValidator(_coerce_to_decimal),
    PlainSerializer(_serialize_decimal, return_type=str, when_used="json"),
]


# ---------------------------------------------------------------------------
# Strict numeric aliases
# ---------------------------------------------------------------------------
# `StrictInt` rejects bool, str, and float coercions — the common silent
# bugs we want loud. `Cents` and `Count` are both non-negative integers;
# we keep them as separate names for grep-ability and so a future
# divergence (e.g. cents allowing ≥0 but counts being ≥1 somewhere) only
# needs to edit one alias.

Cents = Annotated[StrictInt, Field(ge=0)]
Count = Annotated[StrictInt, Field(ge=0)]


# ---------------------------------------------------------------------------
# Enums used across many contract modules
# ---------------------------------------------------------------------------


class AgentName(StrEnum):
    orchestrator = "orchestrator"
    document_analyst = "document_analyst"
    pricing_agent = "pricing_agent"


class ItemKind(StrEnum):
    material = "material"
    labor = "labor"
    exclusion = "exclusion"
    note = "note"
    unreadable = "unreadable"


class MatchStatus(StrEnum):
    matched = "matched"
    substitute = "substitute"
    no_match = "no_match"
    failed = "failed"
    timeout = "timeout"
    skipped = "skipped"


class ConfidenceLevel(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


class RunStatus(StrEnum):
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    interrupted = "interrupted"


class RunOutcome(StrEnum):
    bid_ready = "bid_ready"
    no_materials = "no_materials"
    invalid_input = "invalid_input"
    budget_exhausted = "budget_exhausted"
    error = "error"


class PageRoute(StrEnum):
    text = "text"
    vision = "vision"
    text_vision = "text+vision"
    unreadable = "unreadable"


class Origin(StrEnum):
    agent = "agent"
    user_filled = "user_filled"
    user_overridden = "user_overridden"


class ConflictType(StrEnum):
    size_mismatch = "size_mismatch"
    excluded_but_listed = "excluded_but_listed"
    spec_vs_list = "spec_vs_list"
    stock_vs_delivery = "stock_vs_delivery"


class BidKind(StrEnum):
    agent = "agent"
    draft = "draft"
    final = "final"


class GeneratedBy(StrEnum):
    orchestrator = "orchestrator"
    fallback = "fallback"


class ReviewStateT(StrEnum):
    none = "none"
    open = "open"
    resolved = "resolved"


class ResolvedBy(StrEnum):
    edit = "edit"
    accept = "accept"
    delete = "delete"


class ErrorKind(StrEnum):
    """Span-failure categorizer — see SPEC §10.3 `span.failed`."""

    timeout = "timeout"
    validation = "validation"
    transport = "transport"
    injected = "injected"
    budget = "budget"


class InputKind(StrEnum):
    pdf = "pdf"
    text = "text"


# Typed aliases for fields that are a small closed set of strings
# but not worth a StrEnum (keeps the Pydantic schema simple).

ReadVia = Literal["text", "vision"]


# ---------------------------------------------------------------------------
# Small reusable value objects
# ---------------------------------------------------------------------------


class SourceRef(Contract):
    """Per SPEC §9.1 — page + quote + how we read it + whether it was verified.

    A `verified=True` SourceRef makes a promise: this exact quote appeared
    on this page and we checked it against the page text. Vision reads give
    us a quote but not a guaranteed byte match, so `verified` must be
    paired with `read_via="text"`. The post-validator enforces the
    four-way link so it isn't possible to build a contract-valid SourceRef
    that lies about its own provenance.
    """

    page: int | None = Field(default=None, ge=1)
    quote: str | None = None
    read_via: ReadVia | None = None
    verified: StrictBool

    @field_validator("quote", mode="before")
    @classmethod
    def _blank_quote_to_none(cls, v: Any) -> Any:
        # An empty or whitespace-only quote is semantically "no quote".
        # Normalizing to None here lets the model validator and
        # downstream tests pattern-match on `quote is None` instead
        # of also checking `quote.strip()`.
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="after")
    def _verified_and_vision_rules(self) -> SourceRef:
        # read_via='vision' checked FIRST so a vision+verified SourceRef
        # fails with the specific "vision cannot be 'verified'" message
        # rather than the general verified-rule message.
        if self.read_via == "vision":
            if self.page is None:
                raise ValueError("read_via='vision' requires a page")
            if self.verified:
                raise ValueError("read_via='vision' cannot be 'verified'")
        # verified=True promises a text-match citation: a text read
        # on a page with a non-empty quote. (The blank-quote
        # normalizer above has already turned "" / "   " into None.)
        if self.verified and (self.quote is None or self.page is None or self.read_via != "text"):
            raise ValueError(
                "verified=True requires a non-empty quote, page and "
                "read_via='text' (vision quotes are never 'verified')"
            )
        return self


class BidDiff(Contract):
    """Per SPEC §9.7 — history-view summary of changes between agent rev 0 and final."""

    changed: Count
    added: Count
    deleted: Count
    accepted: Count
