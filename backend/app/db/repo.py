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
from collections.abc import Callable
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


# ---------------------------------------------------------------------------
# item_triage
# ---------------------------------------------------------------------------


async def _insert_item_triage(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    item_id: str,
    category: str,
    reason: str,
    created_at: str,
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO item_triage (run_id, item_id, category, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (run_id, item_id, category, reason, created_at),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise RuntimeError("_insert_item_triage: INSERT RETURNING produced no row")
    return int(row[0])


async def insert_item_triage_bulk(
    db: Database,
    *,
    run_id: str,
    entries: list[tuple[str, str, str]],  # (item_id, category, reason)
    created_at: str,
) -> list[int]:
    """Insert N triage rows under one write lock — SPEC §7.2's
    `classify_items` tool uses this so a batch is atomic."""

    ids: list[int] = []
    async with db.write() as conn:
        for item_id, category, reason in entries:
            ids.append(
                await _insert_item_triage(
                    conn,
                    run_id=run_id,
                    item_id=item_id,
                    category=category,
                    reason=reason,
                    created_at=created_at,
                )
            )
    return ids


async def list_item_triage(db: Database, *, run_id: str) -> list[dict[str, Any]]:
    """Latest category per item_id — `item_triage` is append-only
    (last row wins per item)."""

    cur = await db.read_conn.execute(
        """
        SELECT item_id, category, reason, created_at
        FROM item_triage
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    )
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    latest: dict[str, dict[str, Any]] = {}
    for r in rows:
        latest[r[0]] = {
            "item_id": r[0],
            "category": r[1],
            "reason": r[2],
            "created_at": r[3],
        }
    return list(latest.values())


# ---------------------------------------------------------------------------
# item_requirements (reads — write-side lives above)
# ---------------------------------------------------------------------------


async def list_item_requirements(db: Database, *, run_id: str) -> list[dict[str, Any]]:
    """Return every requirement row for the run. Caller joins by
    item_id. Shape matches `RequirementView` fields plus the
    `created_by` column for the trace span."""

    cur = await db.read_conn.execute(
        """
        SELECT item_id, field, value_json, reason, source_page,
               source_quote, read_via, quote_verified, created_by
        FROM item_requirements
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    )
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "item_id": r[0],
                "field": r[1],
                "value_json": r[2],
                "reason": r[3],
                "source_page": r[4],
                "source_quote": r[5],
                "read_via": r[6],
                "quote_verified": bool(r[7]),
                "created_by": r[8],
            }
        )
    return out


# ---------------------------------------------------------------------------
# items (reads)
# ---------------------------------------------------------------------------


async def list_items(db: Database, *, run_id: str) -> list[dict[str, Any]]:
    cur = await db.read_conn.execute(
        """
        SELECT id, kind, description, quantity_text, unit_raw,
               source_page, source_quote, read_via, verified,
               verification_notes_json, specs_json
        FROM items
        WHERE run_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (run_id,),
    )
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "kind": r[1],
                "description": r[2],
                "quantity_text": r[3],
                "unit_raw": r[4],
                "source_page": r[5],
                "source_quote": r[6],
                "read_via": r[7],
                "verified": bool(r[8]),
                "notes": json.loads(r[9] or "[]"),
                "specs": json.loads(r[10] or "{}"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# price_results
# ---------------------------------------------------------------------------


async def _next_price_attempt(conn: aiosqlite.Connection, *, run_id: str, item_id: str) -> int:
    cur = await conn.execute(
        "SELECT COALESCE(MAX(attempt), 0) FROM price_results WHERE run_id = ? AND item_id = ?",
        (run_id, item_id),
    )
    try:
        row = await cur.fetchone()
    finally:
        await cur.close()
    return int((row[0] if row else 0) or 0) + 1


async def _insert_price_result(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    item_id: str,
    attempt: int,
    row: dict[str, Any],
    created_at: str,
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO price_results (
          run_id, item_id, attempt, status, sku, product_name,
          sale_unit, sale_qty, conversion_path, unit_price_cents,
          branch_on_hand, shortfall, transfer_options_json,
          assumptions_json, attribute_mismatches_json,
          verify_notes_json, is_substitute, substitute_reason,
          confidence, confidence_reasons_json, searched, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            run_id,
            item_id,
            attempt,
            row["status"],
            row.get("sku"),
            row.get("product_name"),
            row.get("sale_unit"),
            row.get("sale_qty"),
            row.get("conversion_path"),
            row.get("unit_price_cents"),
            row.get("branch_on_hand"),
            row.get("shortfall"),
            json.dumps(row.get("transfer_options") or []),
            json.dumps(row.get("assumptions") or []),
            json.dumps(row.get("attribute_mismatches") or []),
            json.dumps(row.get("verify_notes") or []),
            int(bool(row.get("is_substitute"))),
            row.get("substitute_reason"),
            row.get("confidence"),
            json.dumps(row.get("confidence_reasons") or []),
            row.get("searched"),
            created_at,
        ),
    )
    try:
        inserted = await cursor.fetchone()
    finally:
        await cursor.close()
    if inserted is None:
        raise RuntimeError("_insert_price_result: INSERT RETURNING produced no row")
    return int(inserted[0])


async def insert_price_result(
    db: Database,
    *,
    run_id: str,
    item_id: str,
    row: dict[str, Any],
    created_at: str,
) -> int:
    """Append a new price_results row for `item_id` (attempt =
    MAX(attempt)+1). Thread-safe under `db.write()`'s single lock."""

    async with db.write() as conn:
        attempt = await _next_price_attempt(conn, run_id=run_id, item_id=item_id)
        return await _insert_price_result(
            conn,
            run_id=run_id,
            item_id=item_id,
            attempt=attempt,
            row=row,
            created_at=created_at,
        )


async def latest_price_result_per_item(db: Database, *, run_id: str) -> dict[str, dict[str, Any]]:
    """Return `{item_id: row}` keyed by item_id, picking the
    highest `attempt` per item. Used by `bid/assemble.py`."""

    cur = await db.read_conn.execute(
        """
        SELECT item_id, attempt, status, sku, product_name, sale_unit,
               sale_qty, conversion_path, unit_price_cents, branch_on_hand,
               shortfall, transfer_options_json, assumptions_json,
               attribute_mismatches_json, verify_notes_json,
               is_substitute, substitute_reason, confidence,
               confidence_reasons_json, searched
        FROM price_results
        WHERE run_id = ?
        ORDER BY item_id ASC, attempt ASC
        """,
        (run_id,),
    )
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    by_item: dict[str, dict[str, Any]] = {}
    for r in rows:
        by_item[r[0]] = {
            "item_id": r[0],
            "attempt": r[1],
            "status": r[2],
            "sku": r[3],
            "product_name": r[4],
            "sale_unit": r[5],
            "sale_qty": r[6],
            "conversion_path": r[7],
            "unit_price_cents": r[8],
            "branch_on_hand": r[9],
            "shortfall": r[10],
            "transfer_options": json.loads(r[11] or "[]"),
            "assumptions": json.loads(r[12] or "[]"),
            "attribute_mismatches": json.loads(r[13] or "[]"),
            "verify_notes": json.loads(r[14] or "[]"),
            "is_substitute": bool(r[15]),
            "substitute_reason": r[16],
            "confidence": r[17],
            "confidence_reasons": json.loads(r[18] or "[]"),
            "searched": r[19],
        }
    return by_item


# ---------------------------------------------------------------------------
# conflicts + rejected_claims
# ---------------------------------------------------------------------------


async def _insert_conflict(
    conn: aiosqlite.Connection,
    *,
    conflict_id: str,
    run_id: str,
    conflict_type: str,
    side_a: dict[str, Any],
    side_b: dict[str, Any],
    explanation: str,
    created_at: str,
) -> None:
    await conn.execute(
        """
        INSERT INTO conflicts (
          id, run_id, type, side_a_json, side_b_json, explanation, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            conflict_id,
            run_id,
            conflict_type,
            json.dumps(side_a),
            json.dumps(side_b),
            explanation,
            created_at,
        ),
    )


async def insert_conflict(
    db: Database,
    *,
    conflict_id: str,
    run_id: str,
    conflict_type: str,
    side_a: dict[str, Any],
    side_b: dict[str, Any],
    explanation: str,
    created_at: str,
) -> None:
    async with db.write() as conn:
        await _insert_conflict(
            conn,
            conflict_id=conflict_id,
            run_id=run_id,
            conflict_type=conflict_type,
            side_a=side_a,
            side_b=side_b,
            explanation=explanation,
            created_at=created_at,
        )


async def list_conflicts(db: Database, *, run_id: str) -> list[dict[str, Any]]:
    cur = await db.read_conn.execute(
        """
        SELECT id, type, side_a_json, side_b_json, explanation, created_at
        FROM conflicts
        WHERE run_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (run_id,),
    )
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    return [
        {
            "id": r[0],
            "type": r[1],
            "side_a": json.loads(r[2]),
            "side_b": json.loads(r[3]),
            "explanation": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


async def _insert_rejected_claim(
    conn: aiosqlite.Connection,
    *,
    run_id: str,
    kind: str,
    payload: dict[str, Any],
    reason: str,
    created_at: str,
) -> int:
    cursor = await conn.execute(
        """
        INSERT INTO rejected_claims (run_id, kind, payload_json, reason, created_at)
        VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (run_id, kind, json.dumps(payload), reason, created_at),
    )
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    if row is None:
        raise RuntimeError("_insert_rejected_claim: INSERT RETURNING produced no row")
    return int(row[0])


async def insert_rejected_claim(
    db: Database,
    *,
    run_id: str,
    kind: str,
    payload: dict[str, Any],
    reason: str,
    created_at: str,
) -> int:
    async with db.write() as conn:
        return await _insert_rejected_claim(
            conn,
            run_id=run_id,
            kind=kind,
            payload=payload,
            reason=reason,
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# runs: finalize + list + detail
# ---------------------------------------------------------------------------


async def finalize_run_status(
    db: Database,
    *,
    run_id: str,
    status: str,
    outcome: str | None,
    outcome_detail: str | None = None,
) -> None:
    """Flip `runs.status` + `outcome` + `outcome_detail` +
    `finished_at`. The runner calls this at the terminal state."""

    now = _now_iso()
    async with db.write() as conn:
        await conn.execute(
            """
            UPDATE runs
            SET status = ?, outcome = ?, outcome_detail = ?, finished_at = ?
            WHERE id = ?
            """,
            (status, outcome, outcome_detail, now, run_id),
        )


async def set_run_project_name(db: Database, *, run_id: str, project_name: str | None) -> None:
    async with db.write() as conn:
        await conn.execute(
            "UPDATE runs SET project_name = ? WHERE id = ?",
            (project_name, run_id),
        )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


async def get_run(db: Database, *, run_id: str) -> dict[str, Any] | None:
    cur = await db.read_conn.execute(
        """
        SELECT id, created_at, finished_at, status, outcome, outcome_detail,
               input_kind, input_name, project_name, branch_id,
               tokens_in, tokens_out, faults_json
        FROM runs
        WHERE id = ?
        """,
        (run_id,),
    )
    try:
        row = await cur.fetchone()
    finally:
        await cur.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "created_at": row[1],
        "finished_at": row[2],
        "status": row[3],
        "outcome": row[4],
        "outcome_detail": row[5],
        "input_kind": row[6],
        "input_name": row[7],
        "project_name": row[8],
        "branch_id": row[9],
        "tokens_in": row[10],
        "tokens_out": row[11],
        "faults_json": row[12],
    }


async def list_runs_page(
    db: Database,
    *,
    status: str | None = None,
    cursor_created_at: str | None = None,
    cursor_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return up to `limit` runs ordered `created_at DESC, id DESC`.
    Keyset pagination: pass `(cursor_created_at, cursor_id)` from
    the previous page's last row to fetch the next page."""

    sql = (
        "SELECT id, created_at, finished_at, status, outcome, outcome_detail, "
        "       input_kind, input_name, project_name, branch_id "
        "FROM runs "
    )
    where: list[str] = []
    params: list[Any] = []
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if cursor_created_at is not None and cursor_id is not None:
        where.append("(created_at, id) < (?, ?)")
        params.extend([cursor_created_at, cursor_id])
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    cur = await db.read_conn.execute(sql, params)
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    return [
        {
            "id": r[0],
            "created_at": r[1],
            "finished_at": r[2],
            "status": r[3],
            "outcome": r[4],
            "outcome_detail": r[5],
            "input_kind": r[6],
            "input_name": r[7],
            "project_name": r[8],
            "branch_id": r[9],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# documents (one per PDF run)
# ---------------------------------------------------------------------------


async def insert_document(
    db: Database,
    *,
    run_id: str,
    filename: str,
    sha256: str,
    page_count: int,
    stored_path: str,
) -> None:
    async with db.write() as conn:
        await conn.execute(
            """
            INSERT INTO documents (run_id, filename, sha256, page_count, stored_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (run_id, filename, sha256, page_count, stored_path),
        )


async def get_document(db: Database, *, run_id: str) -> dict[str, Any] | None:
    cur = await db.read_conn.execute(
        "SELECT filename, sha256, page_count, stored_path FROM documents WHERE run_id = ?",
        (run_id,),
    )
    try:
        row = await cur.fetchone()
    finally:
        await cur.close()
    if row is None:
        return None
    return {
        "filename": row[0],
        "sha256": row[1],
        "page_count": row[2],
        "stored_path": row[3],
    }


# ---------------------------------------------------------------------------
# bids: latest-by-kind + current-draft helpers
# ---------------------------------------------------------------------------


async def get_bid(
    db: Database, *, run_id: str, kind: str, revision: int | None = None
) -> dict[str, Any] | None:
    """Return the highest-revision bid of `kind` (or the exact
    revision if given)."""

    sql = (
        "SELECT id, kind, revision, data_json, created_at, updated_at, finalized_at "
        "FROM bids WHERE run_id = ? AND kind = ? "
    )
    params: list[Any] = [run_id, kind]
    if revision is not None:
        sql += "AND revision = ? "
        params.append(revision)
    sql += "ORDER BY revision DESC LIMIT 1"
    cur = await db.read_conn.execute(sql, params)
    try:
        row = await cur.fetchone()
    finally:
        await cur.close()
    if row is None:
        return None
    return {
        "id": row[0],
        "kind": row[1],
        "revision": row[2],
        "data": json.loads(row[3]),
        "created_at": row[4],
        "updated_at": row[5],
        "finalized_at": row[6],
    }


class StaleRevisionConflict(Exception):
    """Raised inside `apply_patch_atomically` when the current
    draft revision doesn't match the caller's `draft_revision`.
    The API layer catches it and renders `409 stale_revision`.
    `current` carries the server's current draft so the client
    can reconcile without a second round-trip."""

    def __init__(self, current: dict[str, Any]) -> None:
        self.current = current
        super().__init__("stale_revision")


async def apply_patch_atomically(
    db: Database,
    *,
    run_id: str,
    expected_draft_revision: int,
    apply: Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]]]],
    created_at: str,
) -> dict[str, Any]:
    """Atomic edit: inside ONE `db.write()` transaction, read the
    current draft, compare revisions, apply the ops, replace the
    draft row, and append audit rows. Two concurrent PATCHes on
    the same revision serialise behind `db.write()`'s lock — the
    second one sees the bumped revision and raises
    `StaleRevisionConflict`.

    `apply(current_data) → (new_data, audit_ops)` is the pure
    edit step — `bid/edits.apply_edits` composed around it.
    """

    async with db.write() as conn:
        cur = await conn.execute(
            """
            SELECT revision, data_json
            FROM bids
            WHERE run_id = ? AND kind = 'draft'
            ORDER BY revision DESC
            LIMIT 1
            """,
            (run_id,),
        )
        try:
            row = await cur.fetchone()
        finally:
            await cur.close()
        if row is None:
            # Caller validated existence upstream; reaching here
            # means the draft was deleted between get_bid and
            # the write — treat it as stale with an empty current.
            raise StaleRevisionConflict({"missing": True})
        current_rev = int(row[0])
        current_data = json.loads(row[1])
        if current_rev != expected_draft_revision:
            raise StaleRevisionConflict(current_data)

        new_data, audit_ops = apply(current_data)
        new_rev = int(new_data["revision"])

        await conn.execute("DELETE FROM bids WHERE run_id = ? AND kind = 'draft'", (run_id,))
        await _insert_bid(
            conn,
            bid_id=f"bid_{run_id}_draft_{new_rev}_{__import__('secrets').token_hex(4)}",
            run_id=run_id,
            kind=BidKind.draft,
            revision=new_rev,
            data=new_data,
            created_at=created_at,
        )
        for op in audit_ops:
            await _insert_bid_edit(
                conn,
                run_id=run_id,
                revision=new_rev,
                op=op,
                created_at=created_at,
            )
        return new_data


async def insert_final_bid(
    db: Database,
    *,
    run_id: str,
    final: dict[str, Any],
    created_at: str,
    finalized_at: str,
) -> None:
    """Write the immutable `kind='final'` row. `id` is
    `bid_<run_id>_final_<rev>_<nonce>` so it stays globally
    unique. DB triggers reject UPDATE/DELETE on agent + final
    rows."""

    import secrets as _secrets

    async with db.write() as conn:
        await _insert_bid(
            conn,
            bid_id=f"bid_{run_id}_final_{final['revision']}_{_secrets.token_hex(4)}",
            run_id=run_id,
            kind=BidKind.final,
            revision=int(final["revision"]),
            data=final,
            created_at=created_at,
            finalized_at=finalized_at,
        )
