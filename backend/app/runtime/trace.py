"""`trace_span`: async context manager that emits `span.started`
on entry and `span.finished` / `span.failed` on exit (SPEC §10.2).

Span ids thread via a `ContextVar` so nested spans nest correctly
across `await` boundaries. The context var is set AFTER
`span.started` is emitted — if the emit fails, the var is
untouched (no leaked span id).

Discipline:
  - `will_retry` is a caller-supplied callable (used by `with_retry`
    in step 4) so each attempt gets its own `span.failed` with the
    accurate flag. trace_span never emits a retry event itself.
  - `CancelledError` → `error_kind="timeout"` (asyncio cancels on
    timeout).
  - `KeyboardInterrupt` / `SystemExit` re-raise without emitting —
    the process is going down.
  - Yields a `SpanHandle` with a writable `.summary` so callers can
    attach a one-line result to `span.finished`.

Clock + id factory are injectable (review rule: explicit clock).
`time.monotonic` because durations are elapsed-time, not wall clock.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from pydantic import ValidationError

from app.contracts import AgentName, ErrorKind
from app.runtime.budgets import BudgetExhausted
from app.runtime.events import EventBus

_logger = logging.getLogger(__name__)

# Public: readable by callers that want to attribute work to the
# current span (e.g. a tool handler emitting `tool.called`).
current_span_id: ContextVar[str | None] = ContextVar("biddesk.current_span_id", default=None)

# Caps `span.failed.message` so a raw exception string can't leak
# document text or model output into the trace.
_MAX_FAILURE_MESSAGE_CHARS = 500


@dataclass
class SpanHandle:
    """Yielded by `trace_span`. Callers can read `.id` (immutable
    after construction) and write `.summary` — which becomes
    `span.finished.payload.summary` on clean exit. Any string <=
    the API_CONTRACT `summary?` shape; longer strings are the
    caller's responsibility to trim."""

    id: str
    summary: str | None = None


def _classify(exc: BaseException) -> ErrorKind:
    """Best-effort mapping. Step 5 extends this (InjectedFault →
    injected). Unknown → transport."""

    if isinstance(exc, BudgetExhausted):
        return ErrorKind.budget
    if isinstance(exc, asyncio.CancelledError | asyncio.TimeoutError | TimeoutError):
        return ErrorKind.timeout
    if isinstance(exc, ValidationError):
        return ErrorKind.validation
    return ErrorKind.transport


def _safe_message(exc: BaseException) -> str:
    text = str(exc) if str(exc) else exc.__class__.__name__
    if len(text) > _MAX_FAILURE_MESSAGE_CHARS:
        return text[: _MAX_FAILURE_MESSAGE_CHARS - 1] + "…"
    return text


@asynccontextmanager
async def trace_span(
    bus: EventBus,
    *,
    run_id: str,
    name: str,
    label: str | None = None,
    agent: AgentName | None = None,
    will_retry: Callable[[BaseException], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> AsyncIterator[SpanHandle]:
    """Open a span.

    Emits `span.started` BEFORE touching the contextvar — a failed
    emit leaves `current_span_id` untouched and the context manager
    body never runs.

    `will_retry(exc)` is consulted on exception to set
    `span.failed.payload.will_retry`. `with_retry` (step 4) passes a
    predicate that returns True when the attempt's exception is
    retryable AND the budget still allows another try.
    """

    span_id = id_factory()
    parent_id = current_span_id.get()
    start_s = clock()

    # Emit FIRST. A failed emit raises without touching the
    # contextvar, so the caller's parent span state is intact.
    await bus.emit(
        type="span.started",
        run_id=run_id,
        agent=agent,
        span_id=span_id,
        parent_span_id=parent_id,
        payload={"name": name, "label": label},
    )

    handle = SpanHandle(id=span_id)
    token = current_span_id.set(span_id)
    try:
        try:
            yield handle
        except (KeyboardInterrupt, SystemExit):
            # Process-level signals: re-raise without emitting. The
            # bus itself is about to go down; the trace file on disk
            # already has span.started.
            raise
        except BaseException as exc:
            # The failure-reporting path must NEVER mask the
            # original exception. Wrap the emit (and its payload
            # construction) in its own try/except so an emit error
            # is logged and swallowed; the bare `raise` at the end
            # then re-raises `exc` — including CancelledError.
            try:
                duration_ms = max(0, int((clock() - start_s) * 1000))
                retry_flag = will_retry(exc) if will_retry is not None else False
                await bus.emit(
                    type="span.failed",
                    run_id=run_id,
                    agent=agent,
                    span_id=span_id,
                    parent_span_id=parent_id,
                    payload={
                        "name": name,
                        "duration_ms": duration_ms,
                        "error_kind": _classify(exc).value,
                        "message": _safe_message(exc),
                        "will_retry": retry_flag,
                    },
                )
            except BaseException:
                _logger.exception(
                    "trace_span: emitting span.failed raised; original exception propagates",
                )
            raise
        else:
            duration_ms = max(0, int((clock() - start_s) * 1000))
            await bus.emit(
                type="span.finished",
                run_id=run_id,
                agent=agent,
                span_id=span_id,
                parent_span_id=parent_id,
                payload={
                    "name": name,
                    "duration_ms": duration_ms,
                    "summary": handle.summary,
                },
            )
    finally:
        current_span_id.reset(token)
