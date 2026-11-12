"""Orchestrator tool-input contracts (SPEC §7.2).

Each tool the orchestrator can call has a Pydantic input model
here. Models are `extra='forbid'` and strict so the LLM's
`tool_use.input` dict is validated the same way the analyst's
and pricing agent's forced-tool inputs are.

The models live in `contracts/` (not `agents/orchestrator/`)
because the SSE event payloads and the trace span also carry
these shapes — the contract is the shared vocabulary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.common import ConflictType, Contract, SourceRef

# ---------------------------------------------------------------------------
# classify_items
# ---------------------------------------------------------------------------


class TriageEntry(BaseModel):
    """One row the model asks to insert into `item_triage`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    item_id: str
    category: Literal["price", "labor", "exclusion", "ambiguous", "ignore_note"]
    reason: str = Field(max_length=300)


class ClassifyItemsInput(Contract):
    """Input to the `classify_items` tool. Each `item_id` must
    exist on this run; unknowns → rejected with per-entry errors."""

    entries: list[TriageEntry] = Field(min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# check_gap_rules
# ---------------------------------------------------------------------------


class CheckGapRulesInput(Contract):
    """Input to the `check_gap_rules` tool. If `item_ids` is
    empty, the matcher evaluates every classified item + runs the
    bid-scope rules (SPEC §7.4)."""

    item_ids: list[str] = Field(default_factory=list, max_length=200)


# ---------------------------------------------------------------------------
# ask_document_analyst  — simple pass-through to the analyst agent
# ---------------------------------------------------------------------------


class AskDocumentAnalystInput(Contract):
    """Routed to the Document Analyst. `page_hint` optional —
    helps the analyst focus a Q&A pass on one page. The analyst
    enforces that every returned citation is verified."""

    question: str = Field(min_length=3, max_length=500)
    page_hint: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# add_requirements
# ---------------------------------------------------------------------------


class AddRequirementEntry(BaseModel):
    """One row the model asks to insert into `item_requirements`.
    Server verifies the quote if given (per DECISIONS #17)."""

    model_config = ConfigDict(extra="forbid", strict=True)

    item_id: str
    field: str
    # Exactly one of `value` / `value_in` per the RequirementView
    # contract — same invariant as `contracts/bid.py`.
    value: str | int | bool | None = None
    value_in: list[str | int | bool] | None = None
    reason: str = Field(max_length=300)
    source_page: int | None = Field(default=None, ge=1)
    source_quote: str | None = Field(default=None, max_length=400)


class AddRequirementsInput(Contract):
    entries: list[AddRequirementEntry] = Field(min_length=1, max_length=100)


# ---------------------------------------------------------------------------
# price_items
# ---------------------------------------------------------------------------


class PriceItemsInput(Contract):
    """Input to the `price_items` tool. Server joins item + its
    recorded requirements by item_id in code (SPEC §7.2), batches
    into ≤8 lines/batch, and delegates to the pricing agent
    concurrently (`asyncio.Semaphore(2)`)."""

    item_ids: list[str] = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=300)


# ---------------------------------------------------------------------------
# flag_conflict
# ---------------------------------------------------------------------------


class ConflictSideInput(BaseModel):
    """Half of a conflict the model proposes. `item_id` optional —
    some conflicts are between two sentences on a page without a
    single item anchor. `source` carries verified page + quote;
    code re-verifies both sides at the server before persisting."""

    model_config = ConfigDict(extra="forbid", strict=True)

    item_id: str | None = None
    source: SourceRef


class FlagConflictInput(Contract):
    type: ConflictType
    side_a: ConflictSideInput
    side_b: ConflictSideInput
    explanation: str = Field(min_length=3, max_length=400)


# ---------------------------------------------------------------------------
# submit_bid
# ---------------------------------------------------------------------------


class SubmitBidAssumptionInput(BaseModel):
    """`{item_id, text}` — SPEC §9.2. Links validated during
    assembly against pricing assumptions and requirements."""

    model_config = ConfigDict(extra="forbid", strict=True)

    item_id: str
    text: str = Field(min_length=3, max_length=300)


class SubmitBidQuestionBasisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["gap_rule", "conflict", "flagged_line"]
    ref_id: str


class SubmitBidQuestionInput(BaseModel):
    """Contractor question — `basis.ref_id` must exist at
    assembly time; else dropped to `rejected_claims`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    text: str = Field(min_length=3, max_length=300)
    basis: SubmitBidQuestionBasisInput


class SubmitBidInput(Contract):
    """What the model submits via the `submit_bid` tool.
    Code owns totals, lines, sections, conflicts — the model
    only authors the narrative (summary) + claim list
    (assumptions) + questions."""

    summary: str = Field(max_length=600)
    assumptions: list[SubmitBidAssumptionInput] = Field(default_factory=list, max_length=100)
    contractor_questions: list[SubmitBidQuestionInput] = Field(default_factory=list, max_length=100)
