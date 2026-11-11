"""Schema sanity: tables exist, PRAGMAs are set, read conn is
query-only (no accidental writes through it)."""

from __future__ import annotations

import aiosqlite
import pytest

from app.db.init import Database

_EXPECTED_TABLES = {
    "runs",
    "documents",
    "pages",
    "items",
    "item_triage",
    "item_requirements",
    "price_results",
    "conflicts",
    "rejected_claims",
    "events",
    "bids",
    "bid_edits",
}


async def _pragma(conn: aiosqlite.Connection, name: str) -> object:
    cursor = await conn.execute(f"PRAGMA {name}")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return row[0] if row is not None else None


class TestSchemaApplied:
    async def test_all_tables_exist(self, db: Database) -> None:
        cursor = await db.read_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        tables = {row[0] for row in rows}
        missing = _EXPECTED_TABLES - tables
        assert not missing, f"missing tables: {sorted(missing)}"

    async def test_write_conn_wal_and_foreign_keys(self, db: Database) -> None:
        assert await _pragma(db._write_conn, "journal_mode") == "wal"
        assert await _pragma(db._write_conn, "foreign_keys") == 1

    async def test_read_conn_is_query_only(self, db: Database) -> None:
        assert await _pragma(db.read_conn, "query_only") == 1
        assert await _pragma(db.read_conn, "foreign_keys") == 1

    async def test_read_conn_rejects_writes(self, db: Database) -> None:
        # query_only=ON turns any write through the read conn into a
        # read-only error — a bug can't accidentally bypass the write lock.
        with pytest.raises(aiosqlite.OperationalError, match="readonly"):
            await db.read_conn.execute(
                "INSERT INTO runs (id, created_at, status, input_kind, branch_id) "
                "VALUES ('x', 'now', 'running', 'pdf', 'b')"
            )
