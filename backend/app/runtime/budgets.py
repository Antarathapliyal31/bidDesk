"""`Budget`: turns + tokens + deadline accounting (SPEC §4.3).

One Budget per agent per run. `charge()` is the only mutator;
every charge call checks all three limits and raises
`BudgetExhausted` on the first one that overflows.

Clock is injected (review rule: explicit clock). `start()` captures
the baseline so elapsed time is measured from the first call —
budgets that are constructed but never started count as having
unlimited time until `start()` lands.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

# Snapshot shape matches `BudgetSnapshot` in contracts/events.py.
# Keep as a plain dict here to avoid importing the Pydantic model
# (budgets are pure arithmetic; the caller renders the event).
BudgetWhich = Literal["turns", "tokens", "deadline"]


class BudgetExhausted(Exception):
    """Raised on the charge() call that pushes a limit over.

    `which` names the limit; `snapshot` carries the consumed values
    at the moment of failure (turns, tokens, elapsed deadline_s) so
    the caller can emit `budget.exhausted(budget=which, snapshot=...)`
    without re-peeking the Budget.
    """

    def __init__(
        self,
        which: BudgetWhich,
        *,
        snapshot: dict[str, int | float],
        detail: str,
    ) -> None:
        self.which = which
        self.snapshot = snapshot
        self.detail = detail
        super().__init__(f"budget exhausted ({which}): {detail}")


@dataclass
class Budget:
    """Turns / tokens / seconds for one agent's work in one run.

    Access pattern:
      1. `budget.start()` once, when the agent begins.
      2. `budget.charge(tokens_in=X, tokens_out=Y)` after each
         model call; defaults to turns=1.
      3. `budget.remaining()` for read-only inspection (e.g. to
         decide whether a retry delay still fits).

    Every `charge()` checks every limit, not just the one being
    incremented — the clock keeps advancing between calls even when
    turns=0 is passed.
    """

    turns: int
    tokens: int
    deadline_s: float
    clock: Callable[[], float] = field(default=time.monotonic)

    _consumed_turns: int = field(default=0, init=False)
    _consumed_tokens: int = field(default=0, init=False)
    _start_s: float | None = field(default=None, init=False)

    def start(self) -> None:
        """Capture the deadline baseline. Idempotent: a repeat
        call is a no-op so the agent entry point doesn't need to
        track state."""

        if self._start_s is None:
            self._start_s = self.clock()

    def charge(self, *, tokens_in: int = 0, tokens_out: int = 0, turns: int = 1) -> None:
        """Add to consumed counters, then raise `BudgetExhausted`
        on the first limit that overflows."""

        if tokens_in < 0 or tokens_out < 0 or turns < 0:
            raise ValueError("charge amounts must be non-negative")
        self._consumed_tokens += tokens_in + tokens_out
        self._consumed_turns += turns
        self._raise_if_exhausted()

    def elapsed_s(self) -> float:
        """Seconds since `start()`; zero if the budget hasn't
        started. Monotonic (uses `self.clock`), so wall-clock skews
        don't move it."""

        if self._start_s is None:
            return 0.0
        return self.clock() - self._start_s

    def remaining(self, *, elapsed_s: float | None = None) -> dict[str, int | float]:
        """How much is left. Values are clamped at zero — the
        consumed counters go past the limit (that's how we detect
        exhaustion), but the UI shouldn't show negatives.

        Pass `elapsed_s` to reuse a value you already read (avoids a
        second clock call in the same instant).
        """

        if elapsed_s is None:
            elapsed_s = self.elapsed_s()
        return {
            "turns": max(0, self.turns - self._consumed_turns),
            "tokens": max(0, self.tokens - self._consumed_tokens),
            "deadline_s": max(0.0, self.deadline_s - elapsed_s),
        }

    def snapshot(self, *, elapsed_s: float | None = None) -> dict[str, int | float]:
        """Consumed values at this instant. Used as the
        `BudgetSnapshot` payload on `budget.exhausted`.

        Pass `elapsed_s` to reuse a value already read elsewhere.
        """

        if elapsed_s is None:
            elapsed_s = self.elapsed_s()
        return {
            "turns": self._consumed_turns,
            "tokens": self._consumed_tokens,
            "deadline_s": elapsed_s,
        }

    def assert_alive(self) -> None:
        """Check all limits without charging. Useful at the top of
        a loop iteration: `with_retry` calls this before scheduling
        the next attempt."""

        self._raise_if_exhausted()

    # ------------------------------------------------------------------

    def _raise_if_exhausted(self) -> None:
        # Order: turns, tokens, deadline. Pick the FIRST breached
        # limit so `which` is deterministic when more than one is
        # over at the same instant. One clock read per call.
        elapsed = self.elapsed_s()
        snap = self.snapshot(elapsed_s=elapsed)
        if self._consumed_turns > self.turns:
            raise BudgetExhausted(
                "turns",
                snapshot=snap,
                detail=f"consumed {self._consumed_turns} > limit {self.turns}",
            )
        if self._consumed_tokens > self.tokens:
            raise BudgetExhausted(
                "tokens",
                snapshot=snap,
                detail=f"consumed {self._consumed_tokens} > limit {self.tokens}",
            )
        if elapsed > self.deadline_s:
            raise BudgetExhausted(
                "deadline",
                snapshot=snap,
                detail=f"elapsed {elapsed:.3f}s > deadline {self.deadline_s:.3f}s",
            )
