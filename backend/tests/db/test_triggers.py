"""Trigger-enforced immutability: items + item_requirements +
bid_edits insert-only; agent (rev 0) + final bids immutable.

Every seed goes through `repo.py` (conftest helpers). Each test
hits one trigger with one write; the match string pins the exact
trigger message."""

from __future__ import annotations

import json

import aiosqlite
import pytest

from app.contracts import BidKind, RequirementView, SourceRef
from app.db import repo
from app.db.init import Database
from tests._seeds import seed_run, seed_run_and_item

_NOW = "2026-11-11T12:00:00+00:00"


def _verified_requirement() -> RequirementView:
    return RequirementView(
        field="treatment",
        value="pressure_treated_above_ground",
        reason="rule: PT_on_concrete",
        source=SourceRef(
            page=3, quote="all plates on concrete to be PT", read_via="text", verified=True
        ),
    )


# ---------------------------------------------------------------------------
# items: UPDATE + DELETE both blocked
# ---------------------------------------------------------------------------


class TestItemsImmutable:
    async def test_update_blocked(self, db: Database) -> None:
        await seed_run_and_item(db)
        with pytest.raises(aiosqlite.IntegrityError, match="items are immutable"):
            async with db.write() as conn:
                await conn.execute("UPDATE items SET description = 'x' WHERE id = 'item_1'")

    async def test_delete_blocked(self, db: Database) -> None:
        await seed_run_and_item(db)
        with pytest.raises(aiosqlite.IntegrityError, match="items are immutable"):
            async with db.write() as conn:
                await conn.execute("DELETE FROM items WHERE id = 'item_1'")


# ---------------------------------------------------------------------------
# item_requirements: UPDATE + DELETE both blocked; value_json stores
# scalar or list verbatim
# ---------------------------------------------------------------------------


class TestItemRequirementsImmutable:
    async def test_update_blocked(self, db: Database) -> None:
        run_id, item_id = await seed_run_and_item(db)
        req_id = await repo.insert_item_requirement(
            db,
            run_id=run_id,
            item_id=item_id,
            requirement=_verified_requirement(),
            created_by="orchestrator",
            created_at=_NOW,
        )
        with pytest.raises(aiosqlite.IntegrityError, match="item_requirements are insert-only"):
            async with db.write() as conn:
                await conn.execute(
                    "UPDATE item_requirements SET reason = 'x' WHERE id = ?",
                    (req_id,),
                )

    async def test_delete_blocked(self, db: Database) -> None:
        run_id, item_id = await seed_run_and_item(db)
        req_id = await repo.insert_item_requirement(
            db,
            run_id=run_id,
            item_id=item_id,
            requirement=_verified_requirement(),
            created_by="orchestrator",
            created_at=_NOW,
        )
        with pytest.raises(aiosqlite.IntegrityError, match="item_requirements are insert-only"):
            async with db.write() as conn:
                await conn.execute("DELETE FROM item_requirements WHERE id = ?", (req_id,))

    async def test_value_json_scalar_stored(self, db: Database) -> None:
        run_id, item_id = await seed_run_and_item(db)
        await repo.insert_item_requirement(
            db,
            run_id=run_id,
            item_id=item_id,
            requirement=_verified_requirement(),
            created_by="orchestrator",
            created_at=_NOW,
        )
        cursor = await db.read_conn.execute(
            "SELECT value_json FROM item_requirements WHERE item_id = ?", (item_id,)
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        assert json.loads(row[0]) == "pressure_treated_above_ground"

    async def test_value_json_list_stored(self, db: Database) -> None:
        run_id, item_id = await seed_run_and_item(db)
        req = RequirementView(
            field="treatment",
            value_in=["pressure_treated_above_ground", "pressure_treated_ground_contact"],
            reason="rule",
            source=SourceRef(page=3, quote="PT", read_via="text", verified=True),
        )
        await repo.insert_item_requirement(
            db,
            run_id=run_id,
            item_id=item_id,
            requirement=req,
            created_by="orchestrator",
            created_at=_NOW,
        )
        cursor = await db.read_conn.execute(
            "SELECT value_json FROM item_requirements WHERE item_id = ?", (item_id,)
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        parsed = json.loads(row[0])
        assert isinstance(parsed, list)
        assert parsed == [
            "pressure_treated_above_ground",
            "pressure_treated_ground_contact",
        ]


# ---------------------------------------------------------------------------
# bids: agent (rev 0) + final immutable; drafts mutable
# ---------------------------------------------------------------------------


async def _seed_bid(db: Database, *, kind: BidKind, revision: int, bid_id: str) -> None:
    await seed_run(db)
    await repo.insert_bid(
        db,
        bid_id=bid_id,
        run_id="run_abc",
        kind=kind,
        revision=revision,
        data={"placeholder": True},
        created_at=_NOW,
    )


class TestBidsImmutable:
    async def test_agent_rev0_update_blocked(self, db: Database) -> None:
        await _seed_bid(db, kind=BidKind.agent, revision=0, bid_id="bid_a")
        with pytest.raises(aiosqlite.IntegrityError, match="agent/final bids are immutable"):
            async with db.write() as conn:
                await conn.execute("UPDATE bids SET data_json = '{}' WHERE id = 'bid_a'")

    async def test_agent_rev0_delete_blocked(self, db: Database) -> None:
        await _seed_bid(db, kind=BidKind.agent, revision=0, bid_id="bid_a")
        with pytest.raises(aiosqlite.IntegrityError, match="agent/final bids are immutable"):
            async with db.write() as conn:
                await conn.execute("DELETE FROM bids WHERE id = 'bid_a'")

    async def test_final_update_blocked(self, db: Database) -> None:
        await _seed_bid(db, kind=BidKind.final, revision=2, bid_id="bid_f")
        with pytest.raises(aiosqlite.IntegrityError, match="agent/final bids are immutable"):
            async with db.write() as conn:
                await conn.execute("UPDATE bids SET data_json = '{}' WHERE id = 'bid_f'")

    async def test_draft_update_allowed(self, db: Database) -> None:
        await _seed_bid(db, kind=BidKind.draft, revision=1, bid_id="bid_d")
        async with db.write() as conn:
            await conn.execute(
                "UPDATE bids SET data_json = ? WHERE id = 'bid_d'",
                (json.dumps({"placeholder": False}),),
            )


# ---------------------------------------------------------------------------
# bid_edits: UPDATE + DELETE both blocked
# ---------------------------------------------------------------------------


class TestBidEditsImmutable:
    async def test_update_blocked(self, db: Database) -> None:
        await seed_run(db)
        edit_id = await repo.insert_bid_edit(
            db,
            run_id="run_abc",
            revision=1,
            op={"op": "set_qty", "line_id": "line_01", "sale_qty": "40"},
            created_at=_NOW,
        )
        with pytest.raises(aiosqlite.IntegrityError, match="bid_edits are insert-only"):
            async with db.write() as conn:
                await conn.execute("UPDATE bid_edits SET op_json = '{}' WHERE id = ?", (edit_id,))

    async def test_delete_blocked(self, db: Database) -> None:
        await seed_run(db)
        edit_id = await repo.insert_bid_edit(
            db,
            run_id="run_abc",
            revision=1,
            op={"op": "set_qty", "line_id": "line_01", "sale_qty": "40"},
            created_at=_NOW,
        )
        with pytest.raises(aiosqlite.IntegrityError, match="bid_edits are insert-only"):
            async with db.write() as conn:
                await conn.execute("DELETE FROM bid_edits WHERE id = ?", (edit_id,))
