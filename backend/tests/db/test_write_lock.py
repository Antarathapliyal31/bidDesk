"""Write-lock tests (review rule 7).

- Serialization: coroutine A grabs `db.write()`, inserts, yields,
  then raises — a concurrent coroutine B blocks on the lock,
  then commits its own insert once A releases. Without the lock,
  A and B would share the same implicit SQLite transaction on
  the shared connection and A's rollback would eat B's insert
  (the single-connection bug the lock is designed to prevent).
- Isolation: during an open `db.write()` transaction, the
  read-only connection still sees the pre-UPDATE value — WAL
  isolation between writer and reader is live.
- Rollback atomicity: a multi-write unit (bid update + bid_edit)
  that raises mid-transaction rolls back FULLY — neither write
  survives.
"""

from __future__ import annotations

import asyncio
import json

from app.contracts import BidKind, InputKind
from app.db import repo
from app.db.init import Database
from app.db.repo import _insert_bid_edit, _insert_run

from .conftest import seed_run

_NOW = "2026-11-11T12:00:00+00:00"


async def _count(db: Database, table: str) -> int:
    cursor = await db.read_conn.execute(f"SELECT COUNT(*) FROM {table}")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    assert row is not None
    return int(row[0])


class TestWriteLockSerialization:
    async def test_rollback_in_a_does_not_eat_insert_in_b(self, db: Database) -> None:
        """A holds the write lock, inserts `run_a`, raises → rolls
        back. B tries to insert `run_b` concurrently; it must block
        on the lock until A releases, then commit its own write.

        Final state: `run_b` present, `run_a` absent. Without the
        lock, both inserts would share an implicit SQLite
        transaction on the single write connection and A's rollback
        would drop both — the failure mode this test pins against.
        """

        a_entered = asyncio.Event()
        let_a_raise = asyncio.Event()

        class _InjectedFault(RuntimeError):
            pass

        async def coroutine_a() -> None:
            try:
                async with db.write() as conn:
                    await _insert_run(
                        conn,
                        run_id="run_a",
                        branch_id="BR-NJ-01",
                        input_kind=InputKind.pdf,
                        input_name="a.pdf",
                        created_at=_NOW,
                    )
                    a_entered.set()
                    await let_a_raise.wait()
                    raise _InjectedFault("A fails after inserting run_a")
            except _InjectedFault:
                pass

        async def coroutine_b() -> None:
            await a_entered.wait()
            await repo.insert_run(
                db,
                run_id="run_b",
                branch_id="BR-NJ-01",
                input_kind=InputKind.pdf,
                input_name="b.pdf",
                created_at=_NOW,
            )

        task_a = asyncio.create_task(coroutine_a())
        task_b = asyncio.create_task(coroutine_b())

        # Wait until A is inside the write lock.
        await a_entered.wait()
        # Confirm B is actually blocked on the lock — it shouldn't
        # have completed even though A has yielded the event loop.
        await asyncio.sleep(0.05)
        assert not task_b.done(), "B should be blocked on the write lock"

        # Let A raise; B should then acquire the lock and commit.
        let_a_raise.set()
        await asyncio.gather(task_a, task_b)

        assert await repo.get_run_status(db, "run_a") is None
        assert await repo.get_run_status(db, "run_b") == "running"


class TestReaderIsolation:
    async def test_read_conn_sees_pre_update_value_mid_transaction(self, db: Database) -> None:
        """WAL gives readers a snapshot of the last committed
        state. Inside an open `db.write()` (UPDATE applied but not
        committed), the read connection must still see the old row."""

        await seed_run(db)
        assert await repo.get_run_status(db, "run_abc") == "running"

        async with db.write() as conn:
            await conn.execute(
                "UPDATE runs SET status = 'completed', outcome = 'bid_ready' WHERE id = 'run_abc'"
            )
            # Writer has applied the UPDATE but not committed.
            mid_tx_status = await repo.get_run_status(db, "run_abc")
            assert mid_tx_status == "running", "read conn leaked uncommitted writer state"

        assert await repo.get_run_status(db, "run_abc") == "completed"


class TestMultiWriteRollback:
    async def test_bid_update_plus_edit_rolls_back_on_error(self, db: Database) -> None:
        """Simulate a PATCH transaction: update a draft bid and
        append the matching bid_edit atomically. If either write
        raises, the whole unit must roll back."""

        await seed_run(db)
        await repo.insert_bid(
            db,
            bid_id="bid_d",
            run_id="run_abc",
            kind=BidKind.draft,
            revision=1,
            data={"placeholder": True},
            created_at=_NOW,
        )
        edits_before = await _count(db, "bid_edits")

        class _InjectedFault(RuntimeError):
            pass

        try:
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE bids SET data_json = ? WHERE id = 'bid_d'",
                    (json.dumps({"placeholder": False}),),
                )
                await _insert_bid_edit(
                    conn,
                    run_id="run_abc",
                    revision=1,
                    op={"op": "set_qty", "line_id": "line_01", "sale_qty": "40"},
                    created_at=_NOW,
                )
                raise _InjectedFault("mid-transaction failure")
        except _InjectedFault:
            pass

        # Draft update reverted…
        cursor = await db.read_conn.execute("SELECT data_json FROM bids WHERE id = 'bid_d'")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        assert json.loads(row[0]) == {"placeholder": True}

        # …and the bid_edit insert didn't survive either.
        assert await _count(db, "bid_edits") == edits_before

    async def test_bid_update_plus_edit_commits_on_success(self, db: Database) -> None:
        """Mirror of the rollback test: same two writes, no fault,
        both commit."""

        await seed_run(db)
        await repo.insert_bid(
            db,
            bid_id="bid_d",
            run_id="run_abc",
            kind=BidKind.draft,
            revision=1,
            data={"placeholder": True},
            created_at=_NOW,
        )
        edits_before = await _count(db, "bid_edits")

        async with db.write() as conn:
            await conn.execute(
                "UPDATE bids SET data_json = ? WHERE id = 'bid_d'",
                (json.dumps({"placeholder": False}),),
            )
            await _insert_bid_edit(
                conn,
                run_id="run_abc",
                revision=1,
                op={"op": "set_qty", "line_id": "line_01", "sale_qty": "40"},
                created_at=_NOW,
            )

        cursor = await db.read_conn.execute("SELECT data_json FROM bids WHERE id = 'bid_d'")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        assert json.loads(row[0]) == {"placeholder": False}
        assert await _count(db, "bid_edits") == edits_before + 1
