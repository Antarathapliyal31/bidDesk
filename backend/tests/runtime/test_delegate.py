"""`delegate()` tests — input/output validation, child span, timeout
and validation retry.

Registers one-off `AgentSpec`s per test and tears the registry
down afterward so tests don't leak.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from pydantic import BaseModel, ValidationError

from app.contracts import AgentName, Contract
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import (
    AgentContext,
    AgentSpec,
    DelegateUnknownAgent,
    delegate,
    register_agent,
    unregister_all,
)
from app.runtime.events import EventBus
from tests._seeds import seed_run

# ---------------------------------------------------------------------------
# Sample input/output models (kept Pydantic, not Contract, so the
# tests exercise both base types).
# ---------------------------------------------------------------------------


class _Query(BaseModel):
    text: str


class _Answer(Contract):
    reply: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ctx(db: Database, bus: EventBus) -> AsyncIterator[AgentContext]:
    """Context shared by every delegate test. Clean up the registry
    between tests so one spec doesn't bleed into another."""

    await seed_run(db)
    budget = Budget(turns=100, tokens=10_000, deadline_s=60.0)
    budget.start()
    try:
        yield AgentContext(run_id="run_abc", bus=bus, budget=budget)
    finally:
        unregister_all()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestDelegateHappyPath:
    async def test_ok_result_returns_validated_output(self, ctx: AgentContext) -> None:
        async def _handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            return {"reply": f"echo:{inp.text}"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_handler,
                timeout_s=5.0,
            )
        )

        result = await delegate(AgentName.document_analyst, {"text": "hello"}, ctx=ctx)
        assert result.status == "ok"
        assert isinstance(result.output, _Answer)
        assert result.output.reply == "echo:hello"
        assert result.errors == []
        assert result.attempts == 1

    async def test_unknown_agent_raises(self, ctx: AgentContext) -> None:
        with pytest.raises(DelegateUnknownAgent):
            await delegate(AgentName.pricing_agent, {"text": "x"}, ctx=ctx)

    async def test_outbound_input_validated(self, ctx: AgentContext) -> None:
        """A malformed input is a programming bug — the orchestrator
        shouldn't ship bad inputs. `delegate()` raises rather than
        returning a 'failed' result for this case."""

        async def _handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            return {"reply": "unreachable"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_handler,
                timeout_s=5.0,
            )
        )
        with pytest.raises(ValidationError):
            await delegate(
                AgentName.document_analyst,
                {"wrong_field": "bad"},
                ctx=ctx,
            )


# ---------------------------------------------------------------------------
# Validation retry: output fails first time, succeeds after feedback
# ---------------------------------------------------------------------------


class TestValidationRetry:
    async def test_retry_with_feedback_then_succeeds(self, ctx: AgentContext, db: Database) -> None:
        attempts = {"n": 0}

        async def _handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            attempts["n"] += 1
            if attempts["n"] == 1:
                # First attempt: wrong shape.
                return {"wrong": "field"}
            # Second attempt: handler sees the feedback and fixes it.
            assert feedback is not None
            assert feedback.attempt == 2
            assert any("reply" in e for e in feedback.errors)
            return {"reply": "fixed"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_handler,
                timeout_s=5.0,
                max_validation_retries=1,
            )
        )

        result = await delegate(AgentName.document_analyst, {"text": "hi"}, ctx=ctx)
        assert result.status == "ok"
        assert result.attempts == 2
        # The first attempt emitted a validation.failed event.
        cursor = await db.read_conn.execute(
            "SELECT payload_json FROM events WHERE type='validation.failed' ORDER BY id"
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        assert len(rows) == 1
        payload = json.loads(rows[0][0])
        assert payload["boundary"] == "document_analyst→orchestrator"
        assert payload["will_retry"] is True

    async def test_exhausted_validation_retries_returns_failed(self, ctx: AgentContext) -> None:
        async def _handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            return {"wrong": "always"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_handler,
                timeout_s=5.0,
                max_validation_retries=1,
            )
        )
        result = await delegate(AgentName.document_analyst, {"text": "hi"}, ctx=ctx)
        assert result.status == "failed"
        assert result.output is None
        assert result.attempts == 2  # 1 initial + 1 retry
        assert any(e.kind.value == "validation" for e in result.errors)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


class TestDelegateTimeout:
    async def test_timeout_returns_timeout_status(self, ctx: AgentContext) -> None:
        import asyncio

        async def _slow_handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            await asyncio.sleep(1.0)
            return {"reply": "late"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_slow_handler,
                timeout_s=0.05,
                max_timeout_retries=0,
            )
        )
        result = await delegate(AgentName.document_analyst, {"text": "x"}, ctx=ctx)
        assert result.status == "timeout"
        assert result.output is None
        assert any(e.kind.value == "timeout" for e in result.errors)


# ---------------------------------------------------------------------------
# Span attached via trace_span
# ---------------------------------------------------------------------------


class TestDelegateSpan:
    async def test_opens_span_with_delegate_label(self, ctx: AgentContext, db: Database) -> None:
        async def _handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            return {"reply": "ok"}

        register_agent(
            AgentSpec(
                name=AgentName.pricing_agent,
                input_model=_Query,
                output_model=_Answer,
                handler=_handler,
                timeout_s=5.0,
            )
        )
        await delegate(AgentName.pricing_agent, {"text": "x"}, ctx=ctx)

        cursor = await db.read_conn.execute(
            "SELECT type, payload_json FROM events WHERE agent='pricing_agent' ORDER BY id"
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        types = [r[0] for r in rows]
        assert "span.started" in types
        assert "span.finished" in types
        started = next(r for r in rows if r[0] == "span.started")
        payload = json.loads(started[1])
        assert payload["name"] == "delegate.pricing_agent"
        # Label records the attempt number — one span per attempt.
        assert payload["label"] == "delegate → pricing_agent (attempt 1)"


# ---------------------------------------------------------------------------
# One span per attempt with will_retry from the retry decision
# ---------------------------------------------------------------------------


class TestOneSpanPerAttempt:
    async def test_retried_attempt_emits_will_retry_true_then_finished(
        self, ctx: AgentContext, db: Database
    ) -> None:
        """Validation fails once, succeeds second time. Expect:
        attempt 1 → span.failed(will_retry=True); attempt 2 →
        span.finished. No single span carrying both outcomes."""

        attempts = {"n": 0}

        async def _handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            attempts["n"] += 1
            if attempts["n"] == 1:
                return {"wrong": "field"}
            return {"reply": "fixed"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_handler,
                timeout_s=5.0,
                max_validation_retries=1,
            )
        )
        result = await delegate(AgentName.document_analyst, {"text": "x"}, ctx=ctx)
        assert result.status == "ok"
        assert result.attempts == 2

        # Scan span events for the delegate spans in order.
        cursor = await db.read_conn.execute(
            "SELECT type, payload_json FROM events "
            "WHERE agent='document_analyst' AND type LIKE 'span.%' ORDER BY id"
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        # Expect: span.started (attempt 1), span.failed (retry=True),
        #         span.started (attempt 2), span.finished.
        assert [r[0] for r in rows] == [
            "span.started",
            "span.failed",
            "span.started",
            "span.finished",
        ]
        failed_payload = json.loads(rows[1][1])
        assert failed_payload["will_retry"] is True
        assert failed_payload["error_kind"] == "validation"

    async def test_final_attempt_emits_will_retry_false(
        self, ctx: AgentContext, db: Database
    ) -> None:
        """Both attempts fail validation. Expect attempt 1's
        span.failed(will_retry=True) AND attempt 2's
        span.failed(will_retry=False)."""

        async def _handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            return {"wrong": "always"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_handler,
                timeout_s=5.0,
                max_validation_retries=1,
            )
        )
        result = await delegate(AgentName.document_analyst, {"text": "x"}, ctx=ctx)
        assert result.status == "failed"
        assert result.attempts == 2

        cursor = await db.read_conn.execute(
            "SELECT type, payload_json FROM events "
            "WHERE agent='document_analyst' AND type LIKE 'span.%' ORDER BY id"
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        assert [r[0] for r in rows] == [
            "span.started",
            "span.failed",
            "span.started",
            "span.failed",
        ]
        first_failed = json.loads(rows[1][1])
        second_failed = json.loads(rows[3][1])
        assert first_failed["will_retry"] is True
        assert second_failed["will_retry"] is False


# ---------------------------------------------------------------------------
# Total deadline across all attempts
# ---------------------------------------------------------------------------


class TestTotalDeadline:
    async def test_slow_attempts_cannot_exceed_total_timeout(self, ctx: AgentContext) -> None:
        """Three attempts, each would sleep ~50 ms; total timeout is
        100 ms. Delegate must stop before three full attempts
        complete (the third would push total elapsed > 100 ms)."""

        import asyncio
        import time

        attempts_started = {"n": 0}

        async def _slow_handler(inp: _Query, c: AgentContext, feedback: Any) -> dict[str, Any]:
            attempts_started["n"] += 1
            # Each attempt sleeps longer than max_timeout_retries lets
            # us retry fully — ~50 ms per attempt vs 100 ms total.
            await asyncio.sleep(1.0)
            return {"reply": "late"}

        register_agent(
            AgentSpec(
                name=AgentName.document_analyst,
                input_model=_Query,
                output_model=_Answer,
                handler=_slow_handler,
                timeout_s=0.1,  # TOTAL budget across all attempts
                max_timeout_retries=5,  # many retries allowed, but no time
            )
        )
        wall_start = time.monotonic()
        result = await delegate(AgentName.document_analyst, {"text": "x"}, ctx=ctx)
        elapsed = time.monotonic() - wall_start

        assert result.status == "timeout"
        # Wall-clock elapsed stays under the total + a small
        # scheduler slack (300 ms headroom accommodates CI jitter).
        assert elapsed < 0.4, f"elapsed {elapsed:.3f}s exceeded total deadline budget"
        # And we didn't somehow run 5+ attempts within 100 ms either.
        assert attempts_started["n"] <= 3
