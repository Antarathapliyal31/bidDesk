"""`Budget` unit tests. Pure arithmetic — no DB needed."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from app.runtime.budgets import Budget, BudgetExhausted


def _stepped_clock(values: list[float]) -> Callable[[], float]:
    """Monotonic clock that pops successive values."""

    def _clock() -> float:
        return values.pop(0)

    return _clock


class TestChargeAccumulates:
    def test_turns_accumulate(self) -> None:
        budget = Budget(turns=5, tokens=1000, deadline_s=60.0)
        budget.start()
        budget.charge(turns=2)
        budget.charge(turns=1)
        assert budget.remaining()["turns"] == 2

    def test_tokens_accumulate_across_in_and_out(self) -> None:
        budget = Budget(turns=10, tokens=1000, deadline_s=60.0)
        budget.start()
        budget.charge(tokens_in=100, tokens_out=50, turns=0)
        budget.charge(tokens_in=10, tokens_out=0, turns=0)
        assert budget.remaining()["tokens"] == 1000 - 160

    def test_default_turns_is_one(self) -> None:
        budget = Budget(turns=3, tokens=1000, deadline_s=60.0)
        budget.start()
        budget.charge()
        assert budget.remaining()["turns"] == 2

    def test_negative_amount_rejected(self) -> None:
        budget = Budget(turns=3, tokens=100, deadline_s=60.0)
        budget.start()
        with pytest.raises(ValueError, match="non-negative"):
            budget.charge(tokens_in=-1)


class TestLimitExhaustion:
    def test_turns_exhausted_raises_turns(self) -> None:
        budget = Budget(turns=1, tokens=1000, deadline_s=60.0)
        budget.start()
        budget.charge(turns=1)
        with pytest.raises(BudgetExhausted) as exc_info:
            budget.charge(turns=1)
        assert exc_info.value.which == "turns"
        assert exc_info.value.snapshot["turns"] == 2

    def test_tokens_exhausted_raises_tokens(self) -> None:
        budget = Budget(turns=10, tokens=100, deadline_s=60.0)
        budget.start()
        with pytest.raises(BudgetExhausted) as exc_info:
            budget.charge(tokens_in=101, turns=0)
        assert exc_info.value.which == "tokens"
        assert exc_info.value.snapshot["tokens"] == 101

    def test_deadline_exhausted_raises_deadline(self) -> None:
        # Two clock reads: start() captures 0.0; charge() → one read
        # inside _raise_if_exhausted (snapshot reuses that value).
        budget = Budget(
            turns=10,
            tokens=1000,
            deadline_s=5.0,
            clock=_stepped_clock([0.0, 10.0]),
        )
        budget.start()
        with pytest.raises(BudgetExhausted) as exc_info:
            budget.charge(turns=0)
        assert exc_info.value.which == "deadline"
        assert exc_info.value.snapshot["deadline_s"] == pytest.approx(10.0)

    def test_first_breached_limit_wins(self) -> None:
        # Over on both turns AND tokens — turns is checked first.
        budget = Budget(turns=1, tokens=10, deadline_s=60.0)
        budget.start()
        with pytest.raises(BudgetExhausted) as exc_info:
            budget.charge(tokens_in=100, turns=10)
        assert exc_info.value.which == "turns"


class TestRemainingAndSnapshot:
    def test_remaining_clamps_at_zero(self) -> None:
        budget = Budget(turns=2, tokens=100, deadline_s=60.0)
        budget.start()
        # Try to overshoot turns and catch — remaining stays at 0, not -N.
        with pytest.raises(BudgetExhausted):
            budget.charge(turns=5)
        assert budget.remaining()["turns"] == 0

    def test_snapshot_shows_consumed(self) -> None:
        # Three reads: start, charge, snapshot. (snapshot() calls
        # elapsed_s() when `elapsed_s=` is not passed.)
        budget = Budget(
            turns=10,
            tokens=1000,
            deadline_s=60.0,
            clock=_stepped_clock([0.0, 2.5, 2.5]),
        )
        budget.start()
        budget.charge(tokens_in=300, turns=2)
        snap = budget.snapshot()
        assert snap == {"turns": 2, "tokens": 300, "deadline_s": pytest.approx(2.5)}


class TestAssertAlive:
    def test_assert_alive_no_charge(self) -> None:
        # The clock advances past deadline without a charge call.
        clock_values = [0.0, 10.0, 10.0]
        budget = Budget(turns=10, tokens=1000, deadline_s=5.0, clock=lambda: clock_values.pop(0))
        budget.start()  # 0.0
        with pytest.raises(BudgetExhausted) as exc_info:
            budget.assert_alive()  # clock → 10.0, elapsed 10 > 5
        assert exc_info.value.which == "deadline"

    def test_assert_alive_before_start_no_elapsed(self) -> None:
        budget = Budget(turns=10, tokens=1000, deadline_s=5.0)
        # Haven't called start(); elapsed should be 0.
        budget.assert_alive()
        assert budget.elapsed_s() == 0.0


class TestStart:
    def test_start_is_idempotent(self) -> None:
        clock_values = [100.0, 102.0, 103.0]
        budget = Budget(
            turns=10,
            tokens=1000,
            deadline_s=60.0,
            clock=lambda: clock_values.pop(0),
        )
        budget.start()  # captures 100.0
        budget.start()  # no-op (does NOT consume the next clock value for baseline)
        # elapsed = 102.0 - 100.0 = 2.0
        assert budget.elapsed_s() == pytest.approx(2.0)
