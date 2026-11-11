"""App DB lifecycle: open two connections (write + read), apply
schema + pragmas, mark any `running` runs from the previous process
as `interrupted`, expose a `Database` wrapper that carries both
connections and the write lock.

The write connection is serialized behind `db.write()` so two
coroutines can't commit each other's partial work. The read
connection is used by `db.read_conn` and benefits from WAL's real
reader concurrency — reads never block on a write and vice versa.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


# ---------------------------------------------------------------------------
# Database wrapper — write conn + read conn + write lock
# ---------------------------------------------------------------------------


@dataclass
class Database:
    """Two-connection aiosqlite wrapper.

    `_write_conn` carries every transaction that mutates state. The
    write lock holds the whole begin→commit unit so two coroutines
    can't interleave INSERTs and flush each other's partial work.
    aiosqlite serializes individual commands on a connection, but
    the lock is what serializes multi-statement transactions.

    `_read_conn` is a parallel connection used only for reads. With
    the file in WAL mode, this connection never blocks the writer
    and vice versa — SQLite returns the last committed snapshot
    from the WAL file. Reads MUST use `db.read_conn`, never the
    write connection; writes MUST go through `db.write()`.
    """

    _write_conn: aiosqlite.Connection
    _read_conn: aiosqlite.Connection
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def read_conn(self) -> aiosqlite.Connection:
        return self._read_conn

    @asynccontextmanager
    async def write(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire the write lock for one transaction. Commits on
        clean exit; rolls back on exception, then re-raises.

        Multi-write transactions use the inner `_x(conn, ...)`
        helpers in `repo.py`:

            async with db.write() as conn:
                await _insert_bid(conn, ...)
                await _insert_bid_edit(conn, ...)

        If either inner raises, the whole unit rolls back.
        """

        async with self._write_lock:
            try:
                yield self._write_conn
            except BaseException:
                await self._write_conn.rollback()
                raise
            else:
                await self._write_conn.commit()


# ---------------------------------------------------------------------------
# Startup steps — exposed so tests can drive them explicitly
# ---------------------------------------------------------------------------


async def apply_schema(conn: aiosqlite.Connection) -> None:
    """Apply the whole schema.sql in one go. Every CREATE is
    `IF NOT EXISTS`, so re-running is idempotent."""

    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    await conn.executescript(schema_sql)
    await conn.commit()


async def configure_pragmas(conn: aiosqlite.Connection) -> None:
    """WAL for concurrent reads; foreign_keys=ON; synchronous=NORMAL
    (safe with WAL and much faster than FULL). WAL is persistent
    once set on the file, so both connections see it."""

    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA synchronous = NORMAL")
    await conn.commit()


async def mark_running_as_interrupted(conn: aiosqlite.Connection, *, now_iso: str) -> list[str]:
    """Flip every `status='running'` row to `interrupted` and return
    the flipped run_ids (SPEC §10.5 / DECISIONS #18).

    Uses SQLite 3.35+'s `UPDATE … RETURNING` so the flip and the
    id-collection happen in one statement — no SELECT-then-UPDATE
    race. Called at startup; the caller (lifespan) emits a
    `run.finished(status="interrupted")` per id once the event bus
    is live (Phase 2 step 3).
    """

    cursor = await conn.execute(
        """
        UPDATE runs
        SET status = 'interrupted',
            outcome = 'error',
            outcome_detail = 'Interrupted by server restart',
            finished_at = ?
        WHERE status = 'running'
        RETURNING id
        """,
        (now_iso,),
    )
    try:
        rows = await cursor.fetchall()
    finally:
        await cursor.close()
    await conn.commit()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Lifespan entry point
# ---------------------------------------------------------------------------


@asynccontextmanager
async def open_app_db(
    path: Path | str, *, now_iso: Callable[[], str] | str
) -> AsyncIterator[tuple[Database, list[str]]]:
    """Open the app DB for the FastAPI lifespan.

    `now_iso` is either a string (one-shot test fixtures) or a
    callable returning an ISO timestamp (production — the lifespan
    passes something like `lambda: datetime.now(UTC).isoformat()`).
    DECISIONS: explicit clock injection, not a global.

    Yields `(Database, interrupted_run_ids)`. The interrupted ids
    are the runs that were `running` on the previous shutdown and
    just got flipped to `interrupted`; the lifespan emits one
    `run.finished(status="interrupted")` per id after the event bus
    starts (Phase 2 step 3).
    """

    write_conn = await aiosqlite.connect(path)
    read_conn: aiosqlite.Connection | None = None
    try:
        write_conn.row_factory = aiosqlite.Row
        await configure_pragmas(write_conn)
        await apply_schema(write_conn)
        stamp = now_iso() if callable(now_iso) else now_iso
        interrupted_ids = await mark_running_as_interrupted(write_conn, now_iso=stamp)
        read_conn = await aiosqlite.connect(path)
        read_conn.row_factory = aiosqlite.Row
        # foreign_keys is per-connection; set it on the read conn too.
        await read_conn.execute("PRAGMA foreign_keys = ON")
        # query_only=ON makes this connection truly read-only — an
        # accidental INSERT/UPDATE/DELETE routed through it raises
        # "attempt to write a readonly database" instead of silently
        # bypassing the write lock.
        await read_conn.execute("PRAGMA query_only = ON")
        await read_conn.commit()
        yield Database(_write_conn=write_conn, _read_conn=read_conn), interrupted_ids
    finally:
        if read_conn is not None:
            await read_conn.close()
        await write_conn.close()
