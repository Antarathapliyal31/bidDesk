"""`with_retry`: transport-layer retry for transient failures
(429 / 5xx / timeout / overloaded) per SPEC §10.4.

Scope:
  - Retries only transport-style failures. Validation retries live
    one layer up (delegate, step 5) because they feed the structured
    errors back to the model — exponential backoff doesn't apply.
  - `is_retryable(exc)` is caller-supplied so the retry layer
    stays decoupled from any specific HTTP client. Phase 3+'s
    `AnthropicLLM` wrapper will pass a predicate that keys on
    `RateLimitError`, `APITimeoutError`, `InternalServerError`, etc.
  - Budget-aware: if a Budget is passed, every attempt calls
    `budget.assert_alive()` before scheduling the next retry, and
    a delay that overruns the remaining deadline is downgraded to
    "give up" rather than "sleep too long".
  - Every retry emits a `retry.scheduled` event via the bus so
    the trace sees each wait.
  - `sleep` and `jitter` are injected (review rule: explicit clock)
    so tests can make attempts deterministic without real time.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from app.runtime.budgets import Budget, BudgetExhausted
from app.runtime.events import EventBus

T = TypeVar("T")

# Default jitter spread in seconds. SPEC §10.4 names ≤ 250 ms.
_DEFAULT_JITTER_S = 0.25


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff. Attempt N uses
    `min(base_delay_s * 2 ** (N - 1), max_delay_s) + jitter()`.

    `max_attempts` counts the first try, so `max_attempts=3` means
    one initial attempt + two retries.
    """

    max_attempts: int = 3
    base_delay_s: float = 1.0
    max_delay_s: float = 30.0
    jitter_s: float = _DEFAULT_JITTER_S


def _default_jitter(max_jitter_s: float) -> float:
    return random.uniform(0.0, max_jitter_s) if max_jitter_s > 0 else 0.0


def _backoff_delay(
    *,
    attempt: int,
    policy: RetryPolicy,
    jitter: Callable[[float], float],
) -> float:
    """Exponential delay for the Nth attempt. `attempt=1` is the
    first attempt (no delay needed before it ran); the delay
    returned is the wait BEFORE attempt `N+1`."""

    base = policy.base_delay_s * (2 ** (attempt - 1))
    capped = min(base, policy.max_delay_s)
    return capped + jitter(policy.jitter_s)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    bus: EventBus,
    run_id: str,
    is_retryable: Callable[[BaseException], bool],
    reason_of: Callable[[BaseException], str] = lambda exc: exc.__class__.__name__,
    budget: Budget | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    jitter: Callable[[float], float] = _default_jitter,
) -> T:
    """Call `fn` with retries. Returns its result on success.

    Re-raises:
      - The exception from the final attempt, if every attempt
        raised a retryable error.
      - Any non-retryable exception immediately (no delay, no event).
      - `BudgetExhausted` from `budget.assert_alive()` when the
        budget runs out mid-retry sequence.
    """

    last_exc: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except BaseException as exc:
            if not is_retryable(exc):
                raise
            last_exc = exc
            if attempt >= policy.max_attempts:
                # Final attempt failed. Re-raise the last exception
                # so the trace shows the actual failure, not a
                # synthesized one.
                raise

            # Budget: fail fast if we're already over.
            if budget is not None:
                budget.assert_alive()

            delay = _backoff_delay(attempt=attempt, policy=policy, jitter=jitter)

            # Budget: don't sleep past the remaining deadline. We'd
            # rather give up now than wake up to a dead budget. One
            # clock read for both remaining + snapshot.
            if budget is not None:
                elapsed_s = budget.elapsed_s()
                remaining_s = float(budget.remaining(elapsed_s=elapsed_s)["deadline_s"])
                if delay > remaining_s:
                    raise BudgetExhausted(
                        "deadline",
                        snapshot=budget.snapshot(elapsed_s=elapsed_s),
                        detail=(
                            f"retry delay {delay:.3f}s would exceed "
                            f"remaining deadline {remaining_s:.3f}s"
                        ),
                    ) from exc

            await bus.emit(
                type="retry.scheduled",
                run_id=run_id,
                payload={
                    "attempt": attempt + 1,
                    "delay_s": delay,
                    "reason": reason_of(exc),
                },
            )
            await sleep(delay)

    # Unreachable: the loop either returns or raises. Guard against
    # a future refactor.
    assert last_exc is not None
    raise last_exc
