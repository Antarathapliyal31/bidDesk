"""`delegate()`: the in-process delegation runtime (SPEC §4.2/§4.3).

Each agent registers an `AgentSpec` at startup; `delegate()` is the
single entry point the orchestrator uses to invoke a sub-agent.

Per SPEC §4.3:
  - Validation BOTH directions: input validated outbound (before
    dispatch); output validated inbound (on return).
  - Validation failures re-prompt with the structured errors fed
    back. Transport retries (429/5xx/timeouts) live BELOW this in
    `with_retry`. Two layers, clearly separated.
  - `AgentSpec.timeout_s` is the TOTAL wall-clock budget across
    every attempt — retries don't reset the clock. Each attempt's
    `asyncio.wait_for` cap is `remaining_total_s`; when it runs
    out we stop regardless of retries left. The effective total
    is `min(spec.timeout_s, ctx.budget.remaining_deadline_s)` so a
    delegate can never outlive the run's overall budget.
  - Each attempt opens its OWN `trace_span`. `will_retry` on that
    span is set from the retry decision we just made, so the trace
    shows the series: span.failed(will_retry=true), …,
    span.finished (success) or span.failed(will_retry=false) (give up).

Status mapping (`AgentResult.status`):
  - `ok`: output validated, no errors.
  - `failed`: validation exhausted all retries, or handler raised
    a non-retryable exception.
  - `timeout`: all attempts timed out OR the total deadline is
    exhausted.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError

from app.contracts import (
    AgentError,
    AgentName,
    AgentResult,
    ErrorKind,
    ValidationFeedback,
)
from app.runtime.budgets import Budget
from app.runtime.events import EventBus
from app.runtime.trace import trace_span

In = TypeVar("In", bound=BaseModel)
Out = TypeVar("Out", bound=BaseModel)


@dataclass
class AgentContext:
    """Per-call context passed into every handler.

    The handler gets a live `bus` for emitting activity events and
    a live `budget` for charging tokens/turns. `run_id` is
    convenience (also available on the AgentTask).
    """

    run_id: str
    bus: EventBus
    budget: Budget


@dataclass(frozen=True)
class AgentSpec(Generic[In, Out]):
    """Registered agent descriptor. Built once at process startup;
    `register_agent()` installs it in `_REGISTRY` and `delegate()`
    looks it up by name.

    `handler` receives the validated input, the context, and an
    optional `ValidationFeedback` on retry-after-validation-failure.
    It returns a raw dict; `delegate()` validates against
    `output_model` on the way back.

    `timeout_s` is the TOTAL across all attempts (not per attempt).
    """

    name: AgentName
    input_model: type[In]
    output_model: type[Out]
    handler: Callable[[In, AgentContext, ValidationFeedback | None], Awaitable[dict[str, Any]]]
    timeout_s: float
    max_validation_retries: int = 1
    max_timeout_retries: int = 1


_REGISTRY: dict[AgentName, AgentSpec[Any, Any]] = {}


def register_agent(spec: AgentSpec[Any, Any]) -> None:
    """Install `spec` under `spec.name`. Idempotent per process;
    re-registering overwrites (handy for tests)."""

    _REGISTRY[spec.name] = spec


def unregister_all() -> None:
    """Clear the registry. Test-only hook so one test's agent
    doesn't leak into another."""

    _REGISTRY.clear()


def _validation_feedback(exc: ValidationError, *, attempt: int) -> ValidationFeedback:
    """Shape Pydantic errors into feedback the model can re-prompt
    with. Truncation lives in `ValidationFeedback`."""

    errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return ValidationFeedback(errors=errors or ["validation failed"], attempt=attempt)


class DelegateUnknownAgent(Exception):
    """Raised when `delegate(name, ...)` doesn't match a registered
    `AgentSpec`. Treated as a programming error — the orchestrator
    should only delegate to agents it registered."""


async def delegate(
    name: AgentName,
    input_: dict[str, Any] | BaseModel,
    *,
    ctx: AgentContext,
) -> AgentResult[Any]:
    """Run the registered agent `name` with `input_`.

    Returns an `AgentResult` envelope — the status + typed output
    + any errors recorded along the way. Does NOT raise on
    validation/timeout failures of the sub-agent; those become
    `status='failed'` / `status='timeout'` on the result. It DOES
    raise if `name` isn't registered (`DelegateUnknownAgent`) or if
    the OUTBOUND input is malformed (the orchestrator should only
    send well-formed inputs).
    """

    spec = _REGISTRY.get(name)
    if spec is None:
        raise DelegateUnknownAgent(f"no registered agent {name.value!r}")

    # Validate OUTBOUND. If this fails, the orchestrator bug: raise.
    validated_input = (
        spec.input_model.model_validate(input_)
        if not isinstance(input_, spec.input_model)
        else input_
    )

    task_id = uuid.uuid4().hex
    errors: list[AgentError] = []
    feedback: ValidationFeedback | None = None
    attempts = 0
    timeout_retries_used = 0
    validation_retries_used = 0

    # Effective total deadline: this delegate can't outlive the
    # run's remaining deadline.
    effective_total_s = min(
        spec.timeout_s,
        float(ctx.budget.remaining()["deadline_s"]),
    )
    delegate_start_s = _loop_time()

    while True:
        attempts += 1

        # How much time is left for THIS attempt.
        elapsed_s = _loop_time() - delegate_start_s
        remaining_s = effective_total_s - elapsed_s
        if remaining_s <= 0:
            errors.append(
                AgentError(
                    kind=ErrorKind.timeout,
                    message=(
                        f"{name.value}: total deadline {effective_total_s:.3f}s "
                        f"exhausted after {attempts - 1} attempt(s)"
                    ),
                    code="total_deadline",
                )
            )
            return AgentResult[Any](
                task_id=task_id,
                agent=name,
                status="timeout",
                output=None,
                errors=errors,
                attempts=max(1, attempts - 1),
                duration_ms=_elapsed_ms(delegate_start_s),
            )

        # Pre-decide retryability so the span's will_retry flag is
        # accurate at span.failed emission time.
        can_retry_validation = validation_retries_used < spec.max_validation_retries
        can_retry_timeout = timeout_retries_used < spec.max_timeout_retries

        def _will_retry(
            exc: BaseException,
            *,
            cv: bool = can_retry_validation,
            ct: bool = can_retry_timeout,
        ) -> bool:
            # ValidationError → retry if budget allows
            if isinstance(exc, ValidationError):
                return cv
            # asyncio.wait_for raises asyncio.TimeoutError (alias for
            # the builtin TimeoutError in 3.11+).
            if isinstance(exc, asyncio.TimeoutError | TimeoutError):
                return ct
            return False

        # One span per attempt. trace_span emits span.started +
        # span.finished (or span.failed) with the will_retry flag.
        try:
            async with trace_span(
                ctx.bus,
                run_id=ctx.run_id,
                name=f"delegate.{name.value}",
                label=f"delegate → {name.value} (attempt {attempts})",
                agent=name,
                will_retry=_will_retry,
            ) as span:
                raw = await asyncio.wait_for(
                    spec.handler(validated_input, ctx, feedback),
                    timeout=remaining_s,
                )
                output = spec.output_model.model_validate(raw)
                span.summary = f"ok on attempt {attempts}"
                return AgentResult[Any](
                    task_id=task_id,
                    agent=name,
                    status="ok",
                    output=output,
                    errors=[],
                    attempts=attempts,
                    duration_ms=_elapsed_ms(delegate_start_s),
                )
        except ValidationError as exc:
            errors.append(
                AgentError(
                    kind=ErrorKind.validation,
                    message=f"output shape invalid: {_short_validation_message(exc)}",
                    code="pydantic",
                    detail={"count": len(exc.errors())},
                )
            )
            # Emit the validation.failed event so the trace shows
            # the attempt hit a schema problem (separate from the
            # span.failed emitted by trace_span).
            await ctx.bus.emit(
                type="validation.failed",
                run_id=ctx.run_id,
                agent=name,
                payload={
                    "boundary": f"{name.value}→orchestrator",
                    "errors": _truncate_errors(exc),
                    "will_retry": can_retry_validation,
                },
            )
            if not can_retry_validation:
                return AgentResult[Any](
                    task_id=task_id,
                    agent=name,
                    status="failed",
                    output=None,
                    errors=errors,
                    attempts=attempts,
                    duration_ms=_elapsed_ms(delegate_start_s),
                )
            validation_retries_used += 1
            feedback = _validation_feedback(exc, attempt=attempts + 1)
            continue
        except TimeoutError:
            errors.append(
                AgentError(
                    kind=ErrorKind.timeout,
                    message=f"handler for {name.value} exceeded its attempt deadline",
                    code="handler_timeout",
                )
            )
            if not can_retry_timeout:
                return AgentResult[Any](
                    task_id=task_id,
                    agent=name,
                    status="timeout",
                    output=None,
                    errors=errors,
                    attempts=attempts,
                    duration_ms=_elapsed_ms(delegate_start_s),
                )
            timeout_retries_used += 1
            feedback = None
            continue
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            errors.append(
                AgentError(
                    kind=ErrorKind.transport,
                    message=f"handler for {name.value} raised: {exc.__class__.__name__}",
                    code=exc.__class__.__name__,
                )
            )
            return AgentResult[Any](
                task_id=task_id,
                agent=name,
                status="failed",
                output=None,
                errors=errors,
                attempts=attempts,
                duration_ms=_elapsed_ms(delegate_start_s),
            )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _loop_time() -> float:
    return asyncio.get_event_loop().time()


def _elapsed_ms(start_s: float) -> int:
    return max(0, int((_loop_time() - start_s) * 1000))


def _truncate_errors(exc: ValidationError) -> list[str]:
    """Shape for the `validation.failed` event payload (≤ 5
    entries per SPEC §10.3). Each entry is `loc: msg`, short."""

    rendered = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"[:160] for e in exc.errors()]
    return rendered[:5]


def _short_validation_message(exc: ValidationError) -> str:
    errs = exc.errors()
    if not errs:
        return "no details"
    first = errs[0]
    return f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
