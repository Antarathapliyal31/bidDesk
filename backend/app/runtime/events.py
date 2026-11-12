"""EventBus: persist typed `RunEvent`s and publish them to live
subscribers.

Discipline (step-2 docstring contract): persist in `db.write()`,
commit, THEN publish. A rolled-back event never fires on the
stream because the publish path only runs after `async with
db.write()` exits cleanly. Validation runs BEFORE persist so a
malformed event never touches the DB.

SSE replay (`stream_after`): subscribe FIRST so no live event
is dropped, THEN fetch DB events after `after_id`, drain the
in-memory buffer deduping by id, then yield live events. Tests
prove no gap.

Lifespan obligation (DECISIONS #18): after the bus is up, iterate
the `interrupted_run_ids` returned by `open_app_db` and emit
`run.finished(status="interrupted", outcome="error", ...)` per id.
Helper exported as `emit_interrupted_run_finished`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.contracts import AgentName, RunEventAdapter
from app.db.init import Database
from app.db.repo import _append_event, list_events_after

# Clock injection (review rule: explicit clock, no globals).
Clock = Callable[[], datetime]


def _now_utc() -> datetime:
    return datetime.now(UTC)


class EventBus:
    """One instance per process. Holds a reference to the shared
    `Database` so emit uses the write connection + lock.

    Subscribers are keyed by `run_id`; each subscription owns an
    `asyncio.Queue`. The internal subscriber map is guarded by its
    own `asyncio.Lock` so subscribe/unsubscribe during a publish
    doesn't race.
    """

    def __init__(self, db: Database, *, clock: Clock = _now_utc) -> None:
        self._db = db
        self._clock = clock
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._subs_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # emit
    # ------------------------------------------------------------------

    async def emit(
        self,
        *,
        type: str,
        run_id: str,
        payload: dict[str, Any],
        agent: AgentName | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> int:
        """Validate → persist → publish. Returns the assigned id.

        Validation uses the RunEvent discriminated union with a
        probe id=1 so a bad shape never reaches the DB. Publish
        happens after the `async with db.write()` block commits;
        on any exception inside that block the event never persists
        and never publishes.
        """

        ts = self._clock().isoformat()

        # Validate pre-persist. We don't know the real id yet, so
        # feed the union a placeholder id=1; the id check is just
        # `ge=1`, and we overwrite it with the autoincrement value
        # before publishing.
        probe = {
            "id": 1,
            "run_id": run_id,
            "ts": ts,
            "type": type,
            "agent": agent.value if agent is not None else None,
            "span_id": span_id,
            "parent_span_id": parent_span_id,
            "payload": payload,
        }
        try:
            RunEventAdapter.validate_python(probe)
        except ValidationError as exc:
            raise ValueError(f"EventBus.emit: invalid event shape: {exc}") from exc

        # Persist under the write lock; commit on clean exit.
        async with self._db.write() as conn:
            event_id = await _append_event(
                conn,
                run_id=run_id,
                ts=ts,
                type=type,
                payload=payload,
                agent=agent,
                span_id=span_id,
                parent_span_id=parent_span_id,
            )

        # Only reached if the write txn committed. Build the final
        # event with the assigned id and publish.
        event = dict(probe)
        event["id"] = event_id
        await self._publish(run_id, event)
        return event_id

    async def _publish(self, run_id: str, event: dict[str, Any]) -> None:
        async with self._subs_lock:
            queues = list(self._subscribers.get(run_id, ()))
        # Fan-out outside the subs lock so a slow subscriber doesn't
        # block subscribe/unsubscribe of unrelated runs.
        for queue in queues:
            await queue.put(event)

    # ------------------------------------------------------------------
    # subscribe / unsubscribe
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def subscribe(self, run_id: str) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        """Open a subscription queue for `run_id`. Live events
        arrive via `queue.get()`. The queue is detached on exit.
        """

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._subs_lock:
            self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            yield queue
        finally:
            async with self._subs_lock:
                self._subscribers.get(run_id, set()).discard(queue)
                if run_id in self._subscribers and not self._subscribers[run_id]:
                    del self._subscribers[run_id]

    async def subscriber_count(self, run_id: str) -> int:
        async with self._subs_lock:
            return len(self._subscribers.get(run_id, ()))

    # ------------------------------------------------------------------
    # stream_after — SSE catch-up + drain + live
    # ------------------------------------------------------------------

    async def stream_after(
        self,
        run_id: str,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield events for `run_id` with no gap and no duplicates.

        Sequence:
          1. subscribe first (so no live event is missed while we
             do the catch-up read).
          2. fetch DB events with `id > after_id` (ordered by id).
          3. drain the in-memory buffer, deduping by id against the
             rows we just yielded.
          4. yield live events as they arrive.

        The caller loops until it's done (or cancels). This
        generator never returns — callers drive the lifetime.
        """

        async with self.subscribe(run_id) as queue:
            seen: set[int] = set()

            # 2. DB catch-up
            rows = await list_events_after(self._db, run_id, after_id=after_id)
            for row in rows:
                event = _row_to_event(row)
                seen.add(event["id"])
                yield event

            # 3. drain the buffer (dedupe anything the catch-up
            # already covered)
            while not queue.empty():
                event = queue.get_nowait()
                if event["id"] in seen:
                    continue
                seen.add(event["id"])
                yield event

            # 4. live
            while True:
                event = await queue.get()
                if event["id"] in seen:
                    continue
                seen.add(event["id"])
                yield event


# ---------------------------------------------------------------------------
# Interrupted-run obligation (DECISIONS #18)
# ---------------------------------------------------------------------------


async def emit_interrupted_run_finished(bus: EventBus, interrupted_run_ids: list[str]) -> list[int]:
    """One `run.finished(status="interrupted", outcome="error",
    outcome_detail="Interrupted by server restart")` per id.

    Called by the lifespan right after the bus is up. Returns the
    list of event ids so the caller can log/trace. If the list is
    empty, this is a no-op.
    """

    event_ids: list[int] = []
    for run_id in interrupted_run_ids:
        event_id = await bus.emit(
            type="run.finished",
            run_id=run_id,
            payload={
                "status": "interrupted",
                "outcome": "error",
                "outcome_detail": "Interrupted by server restart",
            },
        )
        event_ids.append(event_id)
    return event_ids


# ---------------------------------------------------------------------------
# Row → event dict
# ---------------------------------------------------------------------------


def _row_to_event(row: Any) -> dict[str, Any]:
    """Shape an `events` row into the dict `stream_after` yields.

    Mirrors the publish shape: `{id, run_id, ts, type, agent,
    span_id, parent_span_id, payload}`. `payload_json` is parsed
    (it's JSON in the DB).
    """

    import json

    return {
        "id": int(row["id"]),
        "run_id": row["run_id"],
        "ts": row["ts"],
        "type": row["type"],
        "agent": row["agent"],
        "span_id": row["span_id"],
        "parent_span_id": row["parent_span_id"],
        "payload": json.loads(row["payload_json"]),
    }
