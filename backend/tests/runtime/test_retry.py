"""`with_retry` tests.

Uses the real EventBus (via `bus` fixture) so the `retry.scheduled`
events actually persist and validate. Sleep and jitter are
injected: sleep records delays without actually sleeping; jitter
is a fixed constant so delays are deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable

import pytest

from app.db.init import Database
from app.runtime.budgets import Budget, BudgetExhausted
from app.runtime.events import EventBus
from app.runtime.retry import RetryPolicy, with_retry
from tests._seeds import seed_run

# ---------------------------------------------------------------------------
# Fake async sleep that records calls instead of blocking the loop.
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)


def _no_jitter(_max: float) -> float:
    return 0.0


# ---------------------------------------------------------------------------
# Test exceptions and predicates
# ---------------------------------------------------------------------------


class _Retryable(Exception):
    pass


class _NotRetryable(Exception):
    pass


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, _Retryable)


def _failing_n_times(n: int, *, then_return: str = "ok") -> Callable[[], Awaitable[str]]:
    """Returns a stateful awaitable that raises _Retryable the first
    `n` times, then returns `then_return`."""

    attempts = {"count": 0}

    async def _fn() -> str:
        attempts["count"] += 1
        if attempts["count"] <= n:
            raise _Retryable(f"attempt {attempts['count']}")
        return then_return

    return _fn


# ---------------------------------------------------------------------------
# Happy path + retry mechanics
# ---------------------------------------------------------------------------


class TestWithRetry:
    async def test_success_on_first_attempt(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        recorder = _Recorder()

        async def _fn() -> str:
            return "immediate"

        result = await with_retry(
            _fn,
            policy=RetryPolicy(max_attempts=3),
            bus=bus,
            run_id="run_abc",
            is_retryable=_is_retryable,
            sleep=recorder.sleep,
            jitter=_no_jitter,
        )
        assert result == "immediate"
        assert recorder.delays == []

    async def test_succeeds_after_retries(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        recorder = _Recorder()
        fn = _failing_n_times(2, then_return="third time")

        result = await with_retry(
            fn,
            policy=RetryPolicy(max_attempts=5, base_delay_s=1.0, jitter_s=0.0),
            bus=bus,
            run_id="run_abc",
            is_retryable=_is_retryable,
            sleep=recorder.sleep,
            jitter=_no_jitter,
        )
        assert result == "third time"
        # Exponential: attempt 1 fails → delay 1.0; attempt 2 fails
        # → delay 2.0; attempt 3 succeeds.
        assert recorder.delays == [1.0, 2.0]

    async def test_gives_up_after_max_attempts(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        recorder = _Recorder()
        fn = _failing_n_times(10)  # never succeeds

        with pytest.raises(_Retryable):
            await with_retry(
                fn,
                policy=RetryPolicy(max_attempts=3),
                bus=bus,
                run_id="run_abc",
                is_retryable=_is_retryable,
                sleep=recorder.sleep,
                jitter=_no_jitter,
            )
        # 3 attempts; 2 waits between them.
        assert len(recorder.delays) == 2

    async def test_non_retryable_raises_immediately(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        recorder = _Recorder()

        async def _fn() -> str:
            raise _NotRetryable("permanent")

        with pytest.raises(_NotRetryable):
            await with_retry(
                _fn,
                policy=RetryPolicy(max_attempts=5),
                bus=bus,
                run_id="run_abc",
                is_retryable=_is_retryable,
                sleep=recorder.sleep,
                jitter=_no_jitter,
            )
        assert recorder.delays == []


class TestExponentialBackoff:
    async def test_delays_grow_and_cap(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        recorder = _Recorder()
        fn = _failing_n_times(10)
        policy = RetryPolicy(max_attempts=5, base_delay_s=1.0, max_delay_s=3.0, jitter_s=0.0)

        with pytest.raises(_Retryable):
            await with_retry(
                fn,
                policy=policy,
                bus=bus,
                run_id="run_abc",
                is_retryable=_is_retryable,
                sleep=recorder.sleep,
                jitter=_no_jitter,
            )
        # Attempts 1..5 → delays before 2,3,4,5 are 1.0, 2.0, 3.0 (cap), 3.0 (cap).
        assert recorder.delays == [1.0, 2.0, 3.0, 3.0]

    async def test_jitter_applied(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        recorder = _Recorder()
        fn = _failing_n_times(10)

        def _fixed_jitter(_max_j: float) -> float:
            return 0.1

        with pytest.raises(_Retryable):
            await with_retry(
                fn,
                policy=RetryPolicy(max_attempts=3, base_delay_s=1.0, jitter_s=0.25),
                bus=bus,
                run_id="run_abc",
                is_retryable=_is_retryable,
                sleep=recorder.sleep,
                jitter=_fixed_jitter,
            )
        assert recorder.delays == [1.0 + 0.1, 2.0 + 0.1]


class TestEventEmission:
    async def test_retry_scheduled_events_emitted(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        recorder = _Recorder()
        fn = _failing_n_times(10)

        with pytest.raises(_Retryable):
            await with_retry(
                fn,
                policy=RetryPolicy(max_attempts=3, base_delay_s=1.0, jitter_s=0.0),
                bus=bus,
                run_id="run_abc",
                is_retryable=_is_retryable,
                reason_of=lambda exc: f"retryable:{exc.args[0]}",
                sleep=recorder.sleep,
                jitter=_no_jitter,
            )

        cursor = await db.read_conn.execute(
            "SELECT payload_json FROM events "
            "WHERE run_id='run_abc' AND type='retry.scheduled' ORDER BY id"
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        payloads = [json.loads(row[0]) for row in rows]
        assert len(payloads) == 2
        assert payloads[0]["attempt"] == 2
        assert payloads[0]["delay_s"] == 1.0
        assert "retryable:" in payloads[0]["reason"]
        assert payloads[1]["attempt"] == 3


class TestBudgetAware:
    async def test_assert_alive_fires_between_attempts(self, db: Database, bus: EventBus) -> None:
        """If the deadline is already blown, `assert_alive()` fires
        inside `with_retry` before the next attempt is scheduled."""

        await seed_run(db)
        recorder = _Recorder()
        fn = _failing_n_times(10)
        # Clock reads during the retry flow:
        #   start()                → 0.0
        #   assert_alive()         → 10.0  (elapsed=10 > deadline 5 → raises)
        clock_values = [0.0, 10.0]
        budget = Budget(
            turns=10,
            tokens=1000,
            deadline_s=5.0,
            clock=lambda: clock_values.pop(0),
        )
        budget.start()

        with pytest.raises(BudgetExhausted) as exc_info:
            await with_retry(
                fn,
                policy=RetryPolicy(max_attempts=3, base_delay_s=1.0, jitter_s=0.0),
                bus=bus,
                run_id="run_abc",
                is_retryable=_is_retryable,
                budget=budget,
                sleep=recorder.sleep,
                jitter=_no_jitter,
            )
        assert exc_info.value.which == "deadline"
        assert recorder.delays == []

    async def test_delay_exceeding_budget_raises_before_sleeping(
        self, db: Database, bus: EventBus
    ) -> None:
        await seed_run(db)
        recorder = _Recorder()
        fn = _failing_n_times(10)
        # Clock reads during the retry flow:
        #   start()                → 0.0
        #   assert_alive()         → 0.0   (elapsed=0; OK)
        #   elapsed_s()            → 0.1   (reused by remaining+snapshot)
        clock_values = [0.0, 0.0, 0.1]
        budget = Budget(
            turns=10,
            tokens=1000,
            deadline_s=0.5,
            clock=lambda: clock_values.pop(0),
        )
        budget.start()

        with pytest.raises(BudgetExhausted) as exc_info:
            await with_retry(
                fn,
                policy=RetryPolicy(max_attempts=3, base_delay_s=2.0, jitter_s=0.0),
                bus=bus,
                run_id="run_abc",
                is_retryable=_is_retryable,
                budget=budget,
                sleep=recorder.sleep,
                jitter=_no_jitter,
            )
        assert exc_info.value.which == "deadline"
        assert "retry delay" in exc_info.value.detail
        # No sleep was called — we refused to schedule a wait that
        # would exceed the remaining deadline.
        assert recorder.delays == []
