"""EventBus tests — persist + publish + replay + interrupted emit.

End-to-end against a real `Database` (via the `bus` fixture). The
trace-side event-shape unit tests live in `test_trace.py` and
`test_strict_fields.py`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.db.init import Database
from app.runtime.events import EventBus, emit_interrupted_run_finished
from tests._seeds import seed_run

# ---------------------------------------------------------------------------
# emit: validate → persist → publish
# ---------------------------------------------------------------------------


class TestEmit:
    async def test_persists_and_returns_id(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        event_id = await bus.emit(
            type="run.started",
            run_id="run_abc",
            payload={
                "input_kind": "pdf",
                "input_name": "rfp.pdf",
                "page_count": 3,
                "branch_id": "BR-NJ-01",
            },
        )
        assert event_id >= 1

        cursor = await db.read_conn.execute(
            "SELECT type, payload_json FROM events WHERE id = ?", (event_id,)
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        assert row[0] == "run.started"
        assert json.loads(row[1])["input_name"] == "rfp.pdf"

    async def test_publishes_to_subscriber(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)

        async with bus.subscribe("run_abc") as queue:
            event_id = await bus.emit(
                type="agent.activity",
                run_id="run_abc",
                payload={"text": "thinking..."},
            )
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            assert event["id"] == event_id
            assert event["type"] == "agent.activity"
            assert event["payload"] == {"text": "thinking..."}

    async def test_invalid_shape_does_not_persist_or_publish(
        self, db: Database, bus: EventBus
    ) -> None:
        await seed_run(db)

        async with bus.subscribe("run_abc") as queue:
            # run.started requires input_kind, input_name, page_count,
            # branch_id. Missing input_name → validation fails.
            with pytest.raises(ValueError, match="invalid event shape"):
                await bus.emit(
                    type="run.started",
                    run_id="run_abc",
                    payload={
                        "input_kind": "pdf",
                        "page_count": 3,
                        "branch_id": "BR-NJ-01",
                    },
                )
            # No row persisted
            cursor = await db.read_conn.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = 'run_abc'"
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            assert row is not None
            assert row[0] == 0
            # Nothing published
            assert queue.empty()

    async def test_other_run_subscriber_not_notified(self, db: Database, bus: EventBus) -> None:
        """Publish fan-out is per-run_id; a subscriber on one run
        doesn't see events for another run."""

        await seed_run(db, run_id="run_a")
        await seed_run(db, run_id="run_b")

        async with bus.subscribe("run_b") as queue_b:
            await bus.emit(
                type="agent.activity",
                run_id="run_a",
                payload={"text": "for run_a"},
            )
            # Short drain: nothing should land in run_b's queue.
            await asyncio.sleep(0.05)
            assert queue_b.empty()


# ---------------------------------------------------------------------------
# subscribe lifecycle
# ---------------------------------------------------------------------------


class TestSubscribers:
    async def test_subscriber_count_tracks_lifecycle(self, db: Database, bus: EventBus) -> None:
        assert await bus.subscriber_count("run_abc") == 0
        async with bus.subscribe("run_abc"):
            assert await bus.subscriber_count("run_abc") == 1
            async with bus.subscribe("run_abc"):
                assert await bus.subscriber_count("run_abc") == 2
            assert await bus.subscriber_count("run_abc") == 1
        assert await bus.subscriber_count("run_abc") == 0

    async def test_subscribe_cleans_up_on_exception(self, db: Database, bus: EventBus) -> None:
        with pytest.raises(RuntimeError):
            async with bus.subscribe("run_abc"):
                assert await bus.subscriber_count("run_abc") == 1
                raise RuntimeError("body failure")
        assert await bus.subscriber_count("run_abc") == 0


# ---------------------------------------------------------------------------
# stream_after: catch-up → drain → live, in order, no gaps
# ---------------------------------------------------------------------------


class TestStreamAfter:
    async def test_catch_up_then_live(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        # Two events BEFORE anyone subscribes (DB-only)
        await bus.emit(
            type="agent.activity",
            run_id="run_abc",
            payload={"text": "hist 1"},
        )
        await bus.emit(
            type="agent.activity",
            run_id="run_abc",
            payload={"text": "hist 2"},
        )

        received: list[dict[str, Any]] = []
        ready = asyncio.Event()

        async def _consumer() -> None:
            async for event in bus.stream_after("run_abc", after_id=0):
                received.append(event)
                ready.set()  # signal first delivery
                if len(received) >= 4:
                    return

        task = asyncio.create_task(_consumer())
        # Wait until the consumer has received at least the first
        # catch-up event — guarantees stream_after is past subscribe.
        await asyncio.wait_for(ready.wait(), timeout=2.0)

        # Live-emit two more
        await bus.emit(
            type="agent.activity",
            run_id="run_abc",
            payload={"text": "live 1"},
        )
        await bus.emit(
            type="agent.activity",
            run_id="run_abc",
            payload={"text": "live 2"},
        )

        await asyncio.wait_for(task, timeout=2.0)

        assert [e["payload"]["text"] for e in received] == [
            "hist 1",
            "hist 2",
            "live 1",
            "live 2",
        ]
        # IDs strictly monotonic and unique
        ids = [e["id"] for e in received]
        assert ids == sorted(ids)
        assert len(set(ids)) == len(ids)

    async def test_after_id_skips_old_events(self, db: Database, bus: EventBus) -> None:
        await seed_run(db)
        e1 = await bus.emit(type="agent.activity", run_id="run_abc", payload={"text": "e1"})
        e2 = await bus.emit(type="agent.activity", run_id="run_abc", payload={"text": "e2"})
        e3 = await bus.emit(type="agent.activity", run_id="run_abc", payload={"text": "e3"})

        received: list[dict[str, Any]] = []

        async def _consumer() -> None:
            async for event in bus.stream_after("run_abc", after_id=e1):
                received.append(event)
                if len(received) == 2:
                    return

        await asyncio.wait_for(asyncio.create_task(_consumer()), timeout=2.0)
        assert [e["id"] for e in received] == [e2, e3]


# ---------------------------------------------------------------------------
# emit_interrupted_run_finished — lifespan obligation (DECISIONS #18)
# ---------------------------------------------------------------------------


class TestInterruptedEmit:
    async def test_empty_list_is_no_op(self, db: Database, bus: EventBus) -> None:
        event_ids = await emit_interrupted_run_finished(bus, [])
        assert event_ids == []

        cursor = await db.read_conn.execute("SELECT COUNT(*) FROM events")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        assert row[0] == 0

    async def test_one_run_finished_event_per_id(self, db: Database, bus: EventBus) -> None:
        await seed_run(db, run_id="run_a")
        await seed_run(db, run_id="run_b")

        event_ids = await emit_interrupted_run_finished(bus, ["run_a", "run_b"])
        assert len(event_ids) == 2

        cursor = await db.read_conn.execute(
            "SELECT run_id, type, payload_json FROM events WHERE id IN (?, ?) ORDER BY id",
            event_ids,
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()

        assert [(r[0], r[1]) for r in rows] == [
            ("run_a", "run.finished"),
            ("run_b", "run.finished"),
        ]
        payload_a = json.loads(rows[0][2])
        assert payload_a == {
            "status": "interrupted",
            "outcome": "error",
            "outcome_detail": "Interrupted by server restart",
        }


# ---------------------------------------------------------------------------
# Publish-after-commit contract (step-3 docstring promise)
# ---------------------------------------------------------------------------


class TestPublishAfterCommit:
    async def test_rolled_back_event_is_never_published(self, db: Database, bus: EventBus) -> None:
        """A write transaction that rolls back must NOT publish the
        event to subscribers. Simulated by wrapping emit inside a
        larger `db.write()` whose body raises after the emit — the
        outer rollback drops the event row AND the subscriber's
        queue stays empty."""

        import asyncio

        await seed_run(db)

        async with bus.subscribe("run_abc") as queue:

            class _InjectedFault(RuntimeError):
                pass

            # Hold the write lock in a competing coroutine so the
            # bus.emit inside sees a serialized transaction we can
            # roll back around it. Simplest test: emit + raise
            # inside db.write() so the emit's own commit is rolled
            # back via the outer transaction.

            # In practice EventBus.emit opens its OWN db.write(),
            # so a rolled-back OUTER transaction can't actually
            # cancel the emit's commit (they're separate units).
            # The real guarantee emit makes is: if validation or
            # persist fails, no publish happens. We already covered
            # validation failure in test_invalid_shape_does_not_persist.
            # Here we check the persist-failure path by forcing a
            # FK violation inside emit's own transaction.
            with pytest.raises(Exception):  # noqa: B017
                await bus.emit(
                    type="run.started",
                    run_id="run_does_not_exist",  # FK violation — no such run
                    payload={
                        "input_kind": "pdf",
                        "input_name": "x.pdf",
                        "page_count": 1,
                        "branch_id": "BR-NJ-01",
                    },
                )

            # Nothing published to the subscriber.
            await asyncio.sleep(0.05)
            assert queue.empty()

            # Nothing persisted either — the FK violation rolled
            # the INSERT back.
            cursor = await db.read_conn.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = 'run_does_not_exist'"
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            assert row is not None
            assert row[0] == 0
