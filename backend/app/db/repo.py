"""Narrow, typed repo functions over `Database` (init.py).

**Shape** — each write is a pair:
  - `_insert_foo(conn, ...)` — inner, takes an open connection, no
    commit, no lock. Compose these for multi-write transactions.
  - `insert_foo(db, ...)` — public wrapper that acquires
    `db.write()` for a single-statement transaction.

Multi-write example (atomic — rolls back fully on error):

    async with db.write() as conn:
        await _insert_bid(conn, ...)
        await _insert_bid_edit(conn, ...)

Reads use `db.read_conn` directly — WAL gives real concurrency so
the reader never blocks the writer and vice versa.

`append_event` is intentionally spelled `_append_event` (private) —
only the event bus (Phase 2 step 3) is allowed to write events, and
only after it has validated a `RunEvent`. Routes should never
INSERT into `events` directly.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import aiosqlite

from app.contracts import (
    AgentName,
    BidKind,
    InputKind,
    ItemKind,
    RequirementView,
)
from app.db.init import Database

# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------


async def _insert_run(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    branch_id: str,
    input_kind: InputKind,
    input_name: str | None,
    created_at: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO runs (id, created_at, status, input_kind, input_name, branch_id)
        VALUES (?, ?, 'running', ?, ?, ?)
        """,
        (run_id, created_at, input_kind.value, input_name, branch_id),
    )


async def insert_run(
    db: Database,
    *,
    run_id: str,
    branch_id: str,
    input_kind: InputKind,
    input_name: str | None,
    created_at: str,
) -> None:
    async with db.write() as conn:
        await _insert_run(
            conn,
            run_id=run_id,
            branch_id=branch_id,
            input_kind=input_kind,
            input_name=input_name,
            created_at=created_at,
        )


async def _add_run_tokens(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    await conn.execute(
        """
        UPDATE runs
        SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?
        WHERE id = ?
        """,
        (tokens_in, tokens_out, run_id),
    )


async def add_run_tokens(
    db: Database,
    *,
    run_id: str,
    tokens_in: int,
    tokens_out: int,
) -> None:
    """Accumulate token usage onto `runs.tokens_in` / `.tokens_out`.
    Called from `AnthropicLLM.call()` after every successful SDK
    response; the running total is what the trace / history page
    shows."""

    async with db.write() as conn:
        await _add_run_tokens(conn, run_id=run_id, tokens_in=tokens_in, tokens_out=tokens_out)


async def get_run_status(db: Database, run_id: str) -> str | None:
    """Read `runs.status`. Uses the read connection."""

    cursor = await db.read_conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,))
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row[0] if row is not None else None


# ---------------------------------------------------------------------------
# items (insert-only — trigger-enforced)
# ---------------------------------------------------------------------------


async def _insert_item(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    item_id: str,
    kind: ItemKind,
    description: str,
    created_at: str,
    quantity_text: str | None = None,
    quantity: str | None = None,
    unit_raw: str | None = None,
    unit: str | None = None,
    specs: dict[str, Any] | None = None,
    source_page: int | None = None,
    source_quote: str | None = None,
    read_via: Literal["text", "vision"] | None = None,
    verified: bool = False,
    verification_notes: list[str] | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO items (
          id, run_id, kind, description, quantity_text, quantity,
          unit_raw, unit, specs_json, source_page, source_quote,
          read_via, verified, verification_notes_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            run_id,
            kind.value,
            description,
            quantity_text,
            quantity,
            unit_raw,
            unit,
            json.dumps(specs or {}),
            source_page,
            source_quote,
            read_via,
            int(verified),
            json.dumps(verification_notes or []),
            created_at,
        ),
    )


async def insert_item(
    db: Database,
    *,
    run_id: str,
    item_id: str,
    kind: ItemKind,
    description: str,
    created_at: str,
    quantity_text: str | None = None,
    quantity: str | None = None,
    unit_raw: str | None = None,
    unit: str | None = None,
    specs: dict[str, Any] | None = None,
    source_page: int | None = None,
    source_quote: str | None = None,
    read_via: Literal["text", "vision"] | None = None,
    verified: bool = False,
    verification_notes: list[str] | None = None,
) -> None:
    async with db.write() as conn:
        await _insert_item(
            conn,
            run_id=run_id,
            item_id=item_id,
            kind=kind,
            description=description,
            created_at=created_at,
            quantity_text=quantity_text,
            quantity=quantity,
            unit_raw=unit_raw,
            unit=unit,
            specs=specs,
            source_page=source_page,
            source_quote=source_quote,
            read_via=read_via,
            verified=verified,
            verification_notes=verification_notes,
        )


# ---------------------------------------------------------------------------
# item_requirements (insert-only — trigger-enforced)
# ---------------------------------------------------------------------------


def _requirement_value_json(req: RequirementView) -> str:
    """Serialize a RequirementView's value XOR value_in to the shape
    `item_requirements.value_json` stores (scalar or list)."""

    payload: Any = req.value if req.value is not None else req.value_in
    return json.dumps(payload)


async def _insert_item_requirement(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    item_id: str,
    requirement: RequirementView,
    created_by: Literal["orchestrator", "user"],
    created_at: str,
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO item_requirements (
          run_id, item_id, field, value_json, reason,
          source_page, source_quote, read_via, quote_verified,
          created_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            run_id,
            item_id,
            requirement.field,
            _requirement_value_json(requirement),
            requirement.reason,
            requirement.source.page,
            requirement.source.quote,
            requirement.source.read_via,
            int(requirement.source.verified),
            created_by,
            created_at,
        ),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise RuntimeError("_insert_item_requirement: INSERT RETURNING produced no row")
    return int(row[0])


async def insert_item_requirement(
    db: Database,
    *,
    run_id: str,
    item_id: str,
    requirement: RequirementView,
    created_by: Literal["orchestrator", "user"],
    created_at: str,
) -> int:
    async with db.write() as conn:
        return await _insert_item_requirement(
            conn,
            run_id=run_id,
            item_id=item_id,
            requirement=requirement,
            created_by=created_by,
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------


async def _append_event(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    ts: str,
    type: str,
    payload: dict[str, Any],
    agent: AgentName | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
) -> int:
    """Private. Only the event bus (Phase 2 step 3) is allowed to
    call this, and only after it has validated the typed `RunEvent`.

    `id` monotonicity comes from `events.id INTEGER PRIMARY KEY
    AUTOINCREMENT` — SQLite guarantees strictly increasing values,
    so Last-Event-ID replay stays correct without extra bookkeeping.

    **Step-3 contract**: the event bus publishes to subscribers only
    AFTER the surrounding write transaction commits. A rolled-back
    event never fires on the SSE stream. Phase 2 step 3 will add a
    test that asserts this.
    """

    cursor = await conn.execute(
        """
        INSERT INTO events (
          run_id, ts, type, agent, span_id, parent_span_id, payload_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            run_id,
            ts,
            type,
            agent.value if agent is not None else None,
            span_id,
            parent_span_id,
            json.dumps(payload),
        ),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise RuntimeError("_append_event: INSERT RETURNING produced no row")
    return int(row[0])


async def list_events_after(db: Database, run_id: str, *, after_id: int = 0) -> list[aiosqlite.Row]:
    """Return every event for `run_id` with `id > after_id`, ascending.

    Read path — uses the read connection. Returns raw Row objects;
    the caller (SSE handler in step 3) decodes `payload_json` and
    validates against the RunEvent union.
    """

    cursor = await db.read_conn.execute(
        """
        SELECT id, run_id, ts, type, agent, span_id, parent_span_id, payload_json
        FROM events
        WHERE run_id = ? AND id > ?
        ORDER BY id
        """,
        (run_id, after_id),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    return list(rows)


# ---------------------------------------------------------------------------
# bids (agent=rev 0 + final: trigger-enforced immutable)
# ---------------------------------------------------------------------------


async def _insert_bid(
    conn: aiosqlite.Connection,
    *,
    bid_id: str,
    run_id: str,
    kind: BidKind,
    revision: int,
    data: dict[str, Any],
    created_at: str,
    updated_at: str | None = None,
    finalized_at: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO bids (
          id, run_id, kind, revision, data_json,
          created_at, updated_at, finalized_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            bid_id,
            run_id,
            kind.value,
            revision,
            json.dumps(data),
            created_at,
            updated_at or created_at,
            finalized_at,
        ),
    )


async def insert_bid(
    db: Database,
    *,
    bid_id: str,
    run_id: str,
    kind: BidKind,
    revision: int,
    data: dict[str, Any],
    created_at: str,
    updated_at: str | None = None,
    finalized_at: str | None = None,
) -> None:
    async with db.write() as conn:
        await _insert_bid(
            conn,
            bid_id=bid_id,
            run_id=run_id,
            kind=kind,
            revision=revision,
            data=data,
            created_at=created_at,
            updated_at=updated_at,
            finalized_at=finalized_at,
        )


# ---------------------------------------------------------------------------
# bid_edits (insert-only — trigger-enforced)
# ---------------------------------------------------------------------------


async def _insert_bid_edit(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    revision: int,
    op: dict[str, Any],
    created_at: str,
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO bid_edits (run_id, revision, op_json, created_at)
        VALUES (?, ?, ?, ?)
        RETURNING id
        """,
        (run_id, revision, json.dumps(op), created_at),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise RuntimeError("_insert_bid_edit: INSERT RETURNING produced no row")
    return int(row[0])


async def insert_bid_edit(
    db: Database,
    *,
    run_id: str,
    revision: int,
    op: dict[str, Any],
    created_at: str,
) -> int:
    async with db.write() as conn:
        return await _insert_bid_edit(
            conn, run_id=run_id, revision=revision, op=op, created_at=created_at
        )
