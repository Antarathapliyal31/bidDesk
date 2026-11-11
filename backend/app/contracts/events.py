"""RunEvent discriminated union — SPEC §10.3 event table.

One typed model per `type` so the JSON payload in `events.payload` is
validated at write time (EventBus) and at read time (`/events`, SSE).

The frontend consumes this union via TanStack Query and the `useRunStream`
reducer; the OpenAPI schema will be surfaced at
`GET /api/_contract/events` in Phase 5 (deferred — SPEC §13).

**Note to frontend (deltas from `docs/API_CONTRACT.md`):**
  - `budget.exhausted`.payload is now `{budget, snapshot?, detail}` where
    `budget` is a literal `"turns"|"tokens"|"deadline"` naming the limit
    that ran out; `snapshot: {turns, tokens, deadline_s}` is optional.
  - `run.status`.payload.current_step, `span.started`.payload.label,
    `item.flagged`.payload.message are nullable (`str | None`).
  - `item.flagged`.payload.reason_code is typed as `ReviewReason`
    (the closed set from `contracts/bid.py`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, TypeAdapter

from .bid import ConflictView, ReviewReason
from .common import (
    AgentName,
    Contract,
    Count,
    ErrorKind,
    GeneratedBy,
    InputKind,
    PageRoute,
    RunOutcome,
    RunStatus,
)
from .items import ItemView
from .pricing import PricedLineView

# ---------------------------------------------------------------------------
# Envelope — common to every event (SPEC §10.3)
# ---------------------------------------------------------------------------


class _EventBase(Contract):
    id: StrictInt = Field(ge=1)
    run_id: str
    ts: datetime
    agent: AgentName | None = None
    span_id: str | None = None
    parent_span_id: str | None = None


# ---------------------------------------------------------------------------
# Per-type payloads + typed event wrappers
# ---------------------------------------------------------------------------
# Each pair: PayloadModel + EventModel. The EventModel pins the literal
# `type` string so the discriminated union can switch on it.


class RunStartedPayload(Contract):
    input_kind: InputKind
    input_name: str
    page_count: Count | None
    branch_id: str


class RunStarted(_EventBase):
    type: Literal["run.started"] = "run.started"
    payload: RunStartedPayload


class RunStatusPayload(Contract):
    status: RunStatus
    current_step: str | None


class RunStatusEvent(_EventBase):
    type: Literal["run.status"] = "run.status"
    payload: RunStatusPayload


class SpanStartedPayload(Contract):
    name: str
    label: str | None


class SpanStarted(_EventBase):
    type: Literal["span.started"] = "span.started"
    payload: SpanStartedPayload


class SpanFinishedPayload(Contract):
    name: str
    duration_ms: Count
    summary: str | None = None


class SpanFinished(_EventBase):
    type: Literal["span.finished"] = "span.finished"
    payload: SpanFinishedPayload


class SpanFailedPayload(Contract):
    name: str
    duration_ms: Count
    error_kind: ErrorKind
    message: str
    will_retry: StrictBool


class SpanFailed(_EventBase):
    type: Literal["span.failed"] = "span.failed"
    payload: SpanFailedPayload


class AgentActivityPayload(Contract):
    text: str = Field(max_length=200)


class AgentActivity(_EventBase):
    type: Literal["agent.activity"] = "agent.activity"
    payload: AgentActivityPayload


class ToolCalledPayload(Contract):
    tool: str
    args_preview: str = Field(max_length=300)


class ToolCalled(_EventBase):
    type: Literal["tool.called"] = "tool.called"
    payload: ToolCalledPayload


class ToolResultPayload(Contract):
    tool: str
    ok: StrictBool
    result_preview: str = Field(max_length=300)


class ToolResult(_EventBase):
    type: Literal["tool.result"] = "tool.result"
    payload: ToolResultPayload


class RetryScheduledPayload(Contract):
    attempt: StrictInt = Field(ge=1)
    delay_s: float = Field(ge=0)
    reason: str


class RetryScheduled(_EventBase):
    type: Literal["retry.scheduled"] = "retry.scheduled"
    payload: RetryScheduledPayload


class ValidationFailedPayload(Contract):
    boundary: str  # e.g. "pricing_agent→orchestrator"
    errors: list[str] = Field(max_length=5)  # short strings per SPEC §10.3
    will_retry: StrictBool


class ValidationFailed(_EventBase):
    type: Literal["validation.failed"] = "validation.failed"
    payload: ValidationFailedPayload


class PageRoutedPayload(Contract):
    page_no: StrictInt = Field(ge=1)
    route: PageRoute


class PageRouted(_EventBase):
    type: Literal["page.routed"] = "page.routed"
    payload: PageRoutedPayload


class ItemExtractedPayload(Contract):
    item: ItemView


class ItemExtracted(_EventBase):
    type: Literal["item.extracted"] = "item.extracted"
    payload: ItemExtractedPayload


class ItemFlaggedPayload(Contract):
    item_id: str
    reason_code: ReviewReason
    message: str | None


class ItemFlagged(_EventBase):
    type: Literal["item.flagged"] = "item.flagged"
    payload: ItemFlaggedPayload


class ItemPricedPayload(Contract):
    line: PricedLineView


class ItemPriced(_EventBase):
    type: Literal["item.priced"] = "item.priced"
    payload: ItemPricedPayload


class ConflictFlaggedPayload(Contract):
    conflict: ConflictView


class ConflictFlagged(_EventBase):
    type: Literal["conflict.flagged"] = "conflict.flagged"
    payload: ConflictFlaggedPayload


class BudgetSnapshot(Contract):
    """Budget state at the moment the exhaustion fired. Optional on
    `budget.exhausted` — if not provided, the frontend shows "budget
    exhausted" without the raw numbers."""

    turns: Count
    tokens: Count
    deadline_s: float = Field(ge=0)


class BudgetExhaustedPayload(Contract):
    """`budget` names *which* limit ran out; `snapshot` is the optional
    state at that moment; `detail` is a human string."""

    budget: Literal["turns", "tokens", "deadline"]
    snapshot: BudgetSnapshot | None = None
    detail: str


class BudgetExhaustedEvent(_EventBase):
    type: Literal["budget.exhausted"] = "budget.exhausted"
    payload: BudgetExhaustedPayload


class BidReadyPayload(Contract):
    revision: Count
    generated_by: GeneratedBy
    open_flags: Count


class BidReady(_EventBase):
    type: Literal["bid.ready"] = "bid.ready"
    payload: BidReadyPayload


class RunFinishedPayload(Contract):
    status: RunStatus
    outcome: RunOutcome | None
    outcome_detail: str | None


class RunFinished(_EventBase):
    type: Literal["run.finished"] = "run.finished"
    payload: RunFinishedPayload


# ---------------------------------------------------------------------------
# Discriminated union
# ---------------------------------------------------------------------------

RunEvent = Annotated[
    RunStarted
    | RunStatusEvent
    | SpanStarted
    | SpanFinished
    | SpanFailed
    | AgentActivity
    | ToolCalled
    | ToolResult
    | RetryScheduled
    | ValidationFailed
    | PageRouted
    | ItemExtracted
    | ItemFlagged
    | ItemPriced
    | ConflictFlagged
    | BudgetExhaustedEvent
    | BidReady
    | RunFinished,
    Field(discriminator="type"),
]


# Adapter for list-of-events parsing (used by `/events` + SSE replay).
RunEventAdapter: TypeAdapter[RunEvent] = TypeAdapter(RunEvent)
RunEventListAdapter: TypeAdapter[list[RunEvent]] = TypeAdapter(list[RunEvent])
