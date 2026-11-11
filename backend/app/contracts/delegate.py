"""Delegation envelope contracts — SPEC §4.2 / §4.3.

Every inter-agent call goes through `delegate()` wrapped in these
envelopes so the orchestrator never touches the raw sub-agent output:
input validated outbound, output validated inbound, errors typed with
their kind, retries + budgets + tracing live outside the agent code.

The runtime handler lives in `runtime/delegate.py` (step 5 of this
phase); this module only pins the shapes.

**AgentError is lenient on purpose** — it's built on the error path
so it must never raise. Fields that would otherwise reject bad input
coerce or truncate:
  - `message` longer than 500 chars → truncated to 499 + "…"
  - `detail` with non-JSON values → coerced via
    `json.loads(json.dumps(detail, default=str))`
  - `detail` larger than 2 KB serialized → replaced with
    `{"truncated": True, "bytes": N}`
This is the opposite discipline from the in-bound contracts; the
rationale is that an exception raised while *recording* a failure
would mask the failure it was trying to record.
"""

from __future__ import annotations

import json
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel as _PydanticBaseModel
from pydantic import Field, StrictInt, field_validator, model_validator

from .common import AgentName, Contract, Count, ErrorKind

In = TypeVar("In", bound=_PydanticBaseModel)
Out = TypeVar("Out", bound=_PydanticBaseModel)

_MAX_MESSAGE_CHARS = 500
_MAX_DETAIL_BYTES = 2048
_MAX_VALIDATION_ERRORS = 10
_MAX_VALIDATION_ERROR_CHARS = 300


def _truncate_detail(v: Any) -> Any:
    """Normalize `detail` for AgentError. Returns the sanitized value
    or a `{truncated: True, bytes: N}` marker when it exceeds the cap."""

    if v is None:
        return v
    if not isinstance(v, dict):
        return v  # let Pydantic report the type error normally
    # `default=str` stringifies anything json.dumps can't handle (class
    # instances, Decimal, etc.); json.loads round-trips it back to
    # plain JSON-compatible python so the field type (`dict`) still
    # fits.
    try:
        normalized = json.loads(json.dumps(v, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        return {"truncated": True, "bytes": 0}
    byte_len = len(json.dumps(normalized, ensure_ascii=False).encode("utf-8"))
    if byte_len > _MAX_DETAIL_BYTES:
        return {"truncated": True, "bytes": byte_len}
    return normalized


class AgentError(Contract):
    """One failure recorded while servicing a delegate call.

    `kind` maps to the `span.failed`/`validation.failed` event taxonomy
    (SPEC §10.3) so the trace shows the same kind the agent saw.
    `ref` is set on partial results to point at the row that failed
    (e.g. an item_id); None for whole-task failures.
    """

    kind: ErrorKind
    message: str
    ref: str | None = None
    code: str | None = None
    detail: dict[str, Any] | None = None

    @field_validator("message", mode="before")
    @classmethod
    def _truncate_message(cls, v: Any) -> Any:
        if isinstance(v, str) and len(v) > _MAX_MESSAGE_CHARS:
            # 499 chars + a single-char ellipsis so the final length is
            # exactly `_MAX_MESSAGE_CHARS`.
            return v[: _MAX_MESSAGE_CHARS - 1] + "…"
        return v

    @field_validator("detail", mode="before")
    @classmethod
    def _coerce_and_cap_detail(cls, v: Any) -> Any:
        return _truncate_detail(v)


class ValidationFeedback(Contract):
    """What `delegate()` passes back to the handler on a validation retry.

    SPEC §4.3: "retry once with the validation error fed back". The
    handler can re-prompt the model with the structured errors.

    Lenient on the retry path: a before-validator **truncates** to 10
    errors and each error string to 300 chars rather than raising —
    we'd rather feed back a trimmed list than crash the retry.
    """

    errors: list[str] = Field(min_length=1)
    attempt: StrictInt = Field(ge=1)

    @field_validator("errors", mode="before")
    @classmethod
    def _truncate_errors(cls, v: Any) -> Any:
        if not isinstance(v, list):
            return v
        truncated: list[Any] = v[:_MAX_VALIDATION_ERRORS]
        return [(s[:_MAX_VALIDATION_ERROR_CHARS] if isinstance(s, str) else s) for s in truncated]


class AgentTask(Contract, Generic[In]):
    """Per SPEC §4.2.

    `task_id` is a uuid4. `deadline_s` is the **TOTAL** wall-clock
    budget in seconds across every attempt — retries don't get a
    fresh clock. The runtime (`runtime/delegate.py`, step 5) enforces
    it via `asyncio.wait_for(..., timeout=min(deadline_s,
    remaining_run_deadline))`; whichever is smaller wins, so a
    per-call deadline can't exceed what the whole run has left.
    """

    task_id: str
    run_id: str
    agent: AgentName
    input: In
    deadline_s: float = Field(gt=0)
    attempt: StrictInt = Field(default=1, ge=1)


AgentResultStatus = Literal["ok", "partial", "failed", "timeout"]


class AgentResult(Contract, Generic[Out]):
    """Per SPEC §4.2.

    Invariants (enforced):
      - status='ok'      ⇒ output set, errors = []
      - status='partial' ⇒ output set, ≥1 error, every error has `ref`
                           (we must know which row failed)
      - status='failed'  ⇒ output is None, ≥1 error
      - status='timeout' ⇒ output is None, ≥1 error AND at least one
                           of those errors has kind='timeout'
    """

    task_id: str
    agent: AgentName
    status: AgentResultStatus
    output: Out | None
    errors: list[AgentError] = Field(default_factory=list)
    attempts: StrictInt = Field(ge=1)
    duration_ms: Count

    @model_validator(mode="after")
    def _status_invariants(self) -> AgentResult:
        if self.status == "ok":
            if self.output is None:
                raise ValueError("AgentResult: status='ok' requires output")
            if self.errors:
                raise ValueError("AgentResult: status='ok' must have empty errors")
        elif self.status == "partial":
            if self.output is None:
                raise ValueError("AgentResult: status='partial' requires output")
            if not self.errors:
                raise ValueError("AgentResult: status='partial' requires at least one error")
            for i, err in enumerate(self.errors):
                if err.ref is None:
                    raise ValueError(
                        f"AgentResult: status='partial' requires ref on every "
                        f"error; errors[{i}] has ref=None"
                    )
        elif self.status == "failed":
            if self.output is not None:
                raise ValueError("AgentResult: status='failed' must have output=None")
            if not self.errors:
                raise ValueError("AgentResult: status='failed' requires at least one error")
        elif self.status == "timeout":
            if self.output is not None:
                raise ValueError("AgentResult: status='timeout' must have output=None")
            if not self.errors:
                raise ValueError("AgentResult: status='timeout' requires a non-empty errors list")
            if not any(e.kind == ErrorKind.timeout for e in self.errors):
                raise ValueError(
                    "AgentResult: status='timeout' requires at least one error with kind='timeout'"
                )
        return self
