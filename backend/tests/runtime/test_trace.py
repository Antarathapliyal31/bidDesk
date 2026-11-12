"""`trace_span` unit tests.

Uses a `_FakeBus` that captures emits in a list — the EventBus
end-to-end (persist + publish + validate) is covered in
`test_event_bus.py`. Isolating the trace logic keeps these tests
fast and lets us force "span.started emit fails" without a DB.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.contracts import ErrorKind
from app.runtime.trace import SpanHandle, current_span_id, trace_span

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeBus:
    """Captures emits; optionally raises on a specified `type`."""

    events: list[dict[str, Any]] = field(default_factory=list)
    raise_on_type: str | None = None

    async def emit(self, **kwargs: Any) -> int:
        if self.raise_on_type is not None and kwargs.get("type") == self.raise_on_type:
            raise RuntimeError(f"forced failure on {kwargs['type']}")
        event_id = len(self.events) + 1
        self.events.append({"id": event_id, **kwargs})
        return event_id


class _AppError(Exception):
    pass


def _clock_from_steps(steps: list[float]) -> Callable[[], float]:
    """Monotonic clock that pops successive values from `steps`."""

    def _clock() -> float:
        return steps.pop(0)

    return _clock


@pytest.fixture
def fake_bus() -> _FakeBus:
    return _FakeBus()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestCleanExit:
    async def test_emits_started_and_finished(self, fake_bus: _FakeBus) -> None:
        captured_id: str | None = None
        async with trace_span(
            fake_bus,  # type: ignore[arg-type]
            run_id="run_abc",
            name="pricing.lookup",
            label="price 40x 2x6",
        ) as handle:
            assert isinstance(handle, SpanHandle)
            captured_id = handle.id

        types = [e["type"] for e in fake_bus.events]
        assert types == ["span.started", "span.finished"]

        started, finished = fake_bus.events
        assert started["span_id"] == captured_id
        assert started["parent_span_id"] is None
        assert started["payload"] == {"name": "pricing.lookup", "label": "price 40x 2x6"}
        assert finished["payload"]["name"] == "pricing.lookup"
        assert finished["payload"]["duration_ms"] >= 0
        assert finished["payload"]["summary"] is None

    async def test_duration_ms_from_injected_clock(self, fake_bus: _FakeBus) -> None:
        clock = _clock_from_steps([100.0, 103.5])  # 3.5 s elapsed
        async with trace_span(
            fake_bus,  # type: ignore[arg-type]
            run_id="run_abc",
            name="x",
            clock=clock,
            id_factory=lambda: "span-1",
        ):
            pass
        assert fake_bus.events[-1]["payload"]["duration_ms"] == 3500

    async def test_span_handle_summary_lands_in_finished(self, fake_bus: _FakeBus) -> None:
        async with trace_span(
            fake_bus,  # type: ignore[arg-type]
            run_id="run_abc",
            name="pricing.lookup",
        ) as handle:
            handle.summary = "picked SKU LUM-2X6-16-SYP-2-PTAG"
        assert fake_bus.events[-1]["payload"]["summary"] == "picked SKU LUM-2X6-16-SYP-2-PTAG"

    async def test_contextvar_reset_on_clean_exit(self, fake_bus: _FakeBus) -> None:
        assert current_span_id.get() is None
        async with trace_span(
            fake_bus,  # type: ignore[arg-type]
            run_id="run_abc",
            name="x",
        ) as handle:
            assert current_span_id.get() == handle.id
        assert current_span_id.get() is None


# ---------------------------------------------------------------------------
# Nested spans
# ---------------------------------------------------------------------------


class TestNesting:
    async def test_inner_parent_is_outer_id(self, fake_bus: _FakeBus) -> None:
        ids = iter(["outer", "inner"])
        async with trace_span(
            fake_bus,  # type: ignore[arg-type]
            run_id="run_abc",
            name="outer",
            id_factory=lambda: next(ids),
        ):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="inner",
                id_factory=lambda: next(ids),
            ):
                # Inner span is current while body runs.
                assert current_span_id.get() == "inner"
            # After inner exits, outer is current again.
            assert current_span_id.get() == "outer"
        assert current_span_id.get() is None

        starts = [e for e in fake_bus.events if e["type"] == "span.started"]
        assert len(starts) == 2
        assert starts[0]["span_id"] == "outer"
        assert starts[0]["parent_span_id"] is None
        assert starts[1]["span_id"] == "inner"
        assert starts[1]["parent_span_id"] == "outer"


# ---------------------------------------------------------------------------
# Exception paths
# ---------------------------------------------------------------------------


class TestFailure:
    async def test_generic_exception_emits_span_failed_transport(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(_AppError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise _AppError("boom")

        failed = fake_bus.events[-1]
        assert failed["type"] == "span.failed"
        assert failed["payload"]["error_kind"] == ErrorKind.transport.value
        assert "boom" in failed["payload"]["message"]
        assert failed["payload"]["will_retry"] is False

    async def test_cancelled_error_classified_as_timeout(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(asyncio.CancelledError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise asyncio.CancelledError()
        assert fake_bus.events[-1]["type"] == "span.failed"
        assert fake_bus.events[-1]["payload"]["error_kind"] == ErrorKind.timeout.value

    async def test_timeout_error_classified_as_timeout(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(TimeoutError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise TimeoutError("deadline exceeded")
        assert fake_bus.events[-1]["type"] == "span.failed"
        assert fake_bus.events[-1]["payload"]["error_kind"] == ErrorKind.timeout.value

    async def test_keyboard_interrupt_no_span_failed(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(KeyboardInterrupt):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise KeyboardInterrupt()
        # Only span.started recorded — no span.failed, no span.finished.
        assert [e["type"] for e in fake_bus.events] == ["span.started"]

    async def test_system_exit_no_span_failed(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(SystemExit):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise SystemExit(0)
        assert [e["type"] for e in fake_bus.events] == ["span.started"]

    async def test_contextvar_reset_on_exception(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(_AppError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                assert current_span_id.get() is not None
                raise _AppError()
        assert current_span_id.get() is None


# ---------------------------------------------------------------------------
# will_retry
# ---------------------------------------------------------------------------


class TestWillRetry:
    async def test_retried_attempt_emits_will_retry_true(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(_AppError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
                will_retry=lambda _exc: True,
            ):
                raise _AppError()
        assert fake_bus.events[-1]["payload"]["will_retry"] is True

    async def test_final_attempt_emits_will_retry_false(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(_AppError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
                will_retry=lambda _exc: False,
            ):
                raise _AppError()
        assert fake_bus.events[-1]["payload"]["will_retry"] is False

    async def test_will_retry_default_is_false(self, fake_bus: _FakeBus) -> None:
        with pytest.raises(_AppError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise _AppError()
        assert fake_bus.events[-1]["payload"]["will_retry"] is False


# ---------------------------------------------------------------------------
# Emit-first (no contextvar leak if span.started fails)
# ---------------------------------------------------------------------------


class TestContextVarSafety:
    async def test_failed_started_emit_leaves_contextvar_untouched(self) -> None:
        bus = _FakeBus(raise_on_type="span.started")
        assert current_span_id.get() is None

        with pytest.raises(RuntimeError, match="forced failure"):
            async with trace_span(
                bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                pytest.fail("body must not run when span.started emit raises")

        # No leak: contextvar is still the pre-span value.
        assert current_span_id.get() is None
        # And the failing emit shouldn't have been recorded (raise_on_type
        # raises before the append).
        assert bus.events == []


# ---------------------------------------------------------------------------
# Failing span.failed emit must NOT mask the original exception
# ---------------------------------------------------------------------------


class TestFailedEmitDoesNotMaskOriginal:
    async def test_original_app_error_propagates(self) -> None:
        bus = _FakeBus(raise_on_type="span.failed")
        original = _AppError("real failure")

        with pytest.raises(_AppError) as exc_info:
            async with trace_span(
                bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise original

        # The original exception is what the caller sees — NOT the
        # RuntimeError from the forced emit failure.
        assert exc_info.value is original
        # span.started was recorded, span.failed was rejected by
        # raise_on_type (so not appended).
        assert [e["type"] for e in bus.events] == ["span.started"]

    async def test_original_cancelled_error_propagates(self) -> None:
        bus = _FakeBus(raise_on_type="span.failed")

        with pytest.raises(asyncio.CancelledError):
            async with trace_span(
                bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise asyncio.CancelledError()

        # Still only span.started recorded.
        assert [e["type"] for e in bus.events] == ["span.started"]


# ---------------------------------------------------------------------------
# Concurrent inner spans share an outer parent without cross-talk
# ---------------------------------------------------------------------------


class TestConcurrentSpans:
    async def test_two_gathered_inners_both_parent_is_outer(self, fake_bus: _FakeBus) -> None:
        async def _inner(inner_id: str) -> None:
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="inner",
                id_factory=lambda: inner_id,
            ):
                # Yield so the two tasks interleave; each must see
                # its own inner span_id, not the other task's.
                await asyncio.sleep(0)
                assert current_span_id.get() == inner_id

        async with trace_span(
            fake_bus,  # type: ignore[arg-type]
            run_id="run_abc",
            name="outer",
            id_factory=lambda: "outer",
        ):
            await asyncio.gather(_inner("inner-a"), _inner("inner-b"))
            # After both finish, the outer span is current again.
            assert current_span_id.get() == "outer"

        starts = [e for e in fake_bus.events if e["type"] == "span.started"]
        assert len(starts) == 3  # outer + 2 inners
        outer_start = next(s for s in starts if s["span_id"] == "outer")
        inner_starts = [s for s in starts if s["span_id"].startswith("inner-")]

        assert outer_start["parent_span_id"] is None
        assert len(inner_starts) == 2
        # Each inner sees "outer" as its parent — no cross-talk.
        for s in inner_starts:
            assert s["parent_span_id"] == "outer"
        # And neither inner saw the OTHER inner as its parent.
        inner_ids = {s["span_id"] for s in inner_starts}
        assert inner_ids == {"inner-a", "inner-b"}


# ---------------------------------------------------------------------------
# Long exception message is truncated with an ellipsis
# ---------------------------------------------------------------------------


class TestLongMessageTruncation:
    async def test_message_truncated_to_500_chars_ending_ellipsis(self, fake_bus: _FakeBus) -> None:
        long = "x" * 1000
        with pytest.raises(_AppError):
            async with trace_span(
                fake_bus,  # type: ignore[arg-type]
                run_id="run_abc",
                name="x",
            ):
                raise _AppError(long)

        failed = fake_bus.events[-1]
        assert failed["type"] == "span.failed"
        message = failed["payload"]["message"]
        assert len(message) == 500
        assert message.endswith("…")
        # Everything except the ellipsis is the raw prefix.
        assert message[:-1] == "x" * 499
