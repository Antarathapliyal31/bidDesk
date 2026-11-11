"""Named-CHECK tests.

Every CHECK fails with its `<table>_<rule>_ck` name in the error
message. Tests match on the name so an unrelated CHECK failure
can't masquerade as the expected one.

Rules covered here:
  - items.verified=1 requires read_via='text' + page + non-empty quote.
  - items.read_via='vision' requires page, forbids verified=True;
    quote is optional.
  - item_requirements: same two rules on quote_verified and read_via.
  - bids: (kind='agent') ⇔ (revision=0).
"""

from __future__ import annotations

import aiosqlite
import pytest

from app.db.init import Database

from .conftest import seed_run, seed_run_and_item

_NOW = "2026-11-11T12:00:00+00:00"


# ---------------------------------------------------------------------------
# items — verified + vision CHECKs
# ---------------------------------------------------------------------------


async def _raw_insert_item(
    db: Database,
    *,
    verified: int,
    read_via: str | None,
    source_page: int | None,
    source_quote: str | None,
    item_id: str = "item_x",
) -> None:
    # Direct SQL (bypassing repo.insert_item) is intentional: the
    # CHECKs guard the DB regardless of whether the write came
    # through the repo.
    async with db.write() as conn:
        await conn.execute(
            """
            INSERT INTO items (
              id, run_id, kind, description,
              source_page, source_quote, read_via, verified, created_at
            ) VALUES (?, 'run_abc', 'material', 'x', ?, ?, ?, ?, ?)
            """,
            (item_id, source_page, source_quote, read_via, verified, _NOW),
        )


class TestItemsVerifiedCheck:
    async def test_verified_text_with_page_and_quote(self, db: Database) -> None:
        await seed_run(db)
        await _raw_insert_item(
            db, verified=1, read_via="text", source_page=3, source_quote="(40) 2x6"
        )

    async def test_verified_vision_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="items_verified_ck"):
            await _raw_insert_item(
                db, verified=1, read_via="vision", source_page=3, source_quote="x"
            )

    async def test_verified_null_page_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="items_verified_ck"):
            await _raw_insert_item(
                db, verified=1, read_via="text", source_page=None, source_quote="x"
            )

    async def test_verified_empty_quote_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="items_verified_ck"):
            await _raw_insert_item(db, verified=1, read_via="text", source_page=3, source_quote="")

    async def test_verified_whitespace_quote_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="items_verified_ck"):
            await _raw_insert_item(
                db, verified=1, read_via="text", source_page=3, source_quote="   "
            )


class TestItemsVisionCheck:
    async def test_vision_without_page_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="items_vision_ck"):
            await _raw_insert_item(
                db, verified=0, read_via="vision", source_page=None, source_quote="x"
            )

    async def test_vision_with_page_and_quote_accepted(self, db: Database) -> None:
        await seed_run(db)
        await _raw_insert_item(
            db, verified=0, read_via="vision", source_page=5, source_quote="OCR text"
        )

    async def test_vision_with_page_and_no_quote_accepted(self, db: Database) -> None:
        # Vision quote is optional — OCR may not surface one.
        await seed_run(db)
        await _raw_insert_item(db, verified=0, read_via="vision", source_page=5, source_quote=None)


# ---------------------------------------------------------------------------
# item_requirements — quote_verified + vision CHECKs
# ---------------------------------------------------------------------------


async def _raw_insert_req(
    db: Database,
    *,
    quote_verified: int,
    read_via: str | None,
    source_page: int | None,
    source_quote: str | None,
) -> None:
    async with db.write() as conn:
        await conn.execute(
            """
            INSERT INTO item_requirements (
              run_id, item_id, field, value_json, reason,
              source_page, source_quote, read_via, quote_verified,
              created_by, created_at
            ) VALUES ('run_abc', 'item_1', 'treatment', '"PT"', 'r',
                      ?, ?, ?, ?, 'orchestrator', ?)
            """,
            (source_page, source_quote, read_via, quote_verified, _NOW),
        )


class TestItemRequirementsQuoteVerifiedCheck:
    async def test_quote_verified_text_valid(self, db: Database) -> None:
        await seed_run_and_item(db)
        await _raw_insert_req(
            db, quote_verified=1, read_via="text", source_page=3, source_quote="PT"
        )

    async def test_quote_verified_vision_rejected(self, db: Database) -> None:
        await seed_run_and_item(db)
        with pytest.raises(aiosqlite.IntegrityError, match="item_requirements_quote_verified_ck"):
            await _raw_insert_req(
                db,
                quote_verified=1,
                read_via="vision",
                source_page=3,
                source_quote="PT",
            )

    async def test_quote_verified_null_page_rejected(self, db: Database) -> None:
        await seed_run_and_item(db)
        with pytest.raises(aiosqlite.IntegrityError, match="item_requirements_quote_verified_ck"):
            await _raw_insert_req(
                db, quote_verified=1, read_via="text", source_page=None, source_quote="PT"
            )

    async def test_quote_verified_empty_quote_rejected(self, db: Database) -> None:
        await seed_run_and_item(db)
        with pytest.raises(aiosqlite.IntegrityError, match="item_requirements_quote_verified_ck"):
            await _raw_insert_req(
                db, quote_verified=1, read_via="text", source_page=3, source_quote=""
            )

    async def test_quote_verified_whitespace_quote_rejected(self, db: Database) -> None:
        await seed_run_and_item(db)
        with pytest.raises(aiosqlite.IntegrityError, match="item_requirements_quote_verified_ck"):
            await _raw_insert_req(
                db, quote_verified=1, read_via="text", source_page=3, source_quote="   "
            )


class TestItemRequirementsVisionCheck:
    async def test_vision_without_page_rejected(self, db: Database) -> None:
        await seed_run_and_item(db)
        with pytest.raises(aiosqlite.IntegrityError, match="item_requirements_vision_ck"):
            await _raw_insert_req(
                db,
                quote_verified=0,
                read_via="vision",
                source_page=None,
                source_quote=None,
            )

    async def test_vision_with_page_no_quote_accepted(self, db: Database) -> None:
        await seed_run_and_item(db)
        await _raw_insert_req(
            db, quote_verified=0, read_via="vision", source_page=5, source_quote=None
        )


# ---------------------------------------------------------------------------
# bids — (kind='agent') ⇔ (revision=0)
# ---------------------------------------------------------------------------


async def _raw_insert_bid(db: Database, *, kind: str, revision: int, bid_id: str = "bid_x") -> None:
    async with db.write() as conn:
        await conn.execute(
            """
            INSERT INTO bids (
              id, run_id, kind, revision, data_json,
              created_at, updated_at
            ) VALUES (?, 'run_abc', ?, ?, '{}', ?, ?)
            """,
            (bid_id, kind, revision, _NOW, _NOW),
        )


class TestBidsAgentRevisionCheck:
    async def test_agent_rev0_allowed(self, db: Database) -> None:
        await seed_run(db)
        await _raw_insert_bid(db, kind="agent", revision=0, bid_id="bid_a")

    async def test_draft_rev1_allowed(self, db: Database) -> None:
        await seed_run(db)
        await _raw_insert_bid(db, kind="draft", revision=1, bid_id="bid_d")

    async def test_final_rev2_allowed(self, db: Database) -> None:
        await seed_run(db)
        await _raw_insert_bid(db, kind="final", revision=2, bid_id="bid_f")

    async def test_agent_rev1_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="bids_agent_revision_ck"):
            await _raw_insert_bid(db, kind="agent", revision=1)

    async def test_draft_rev0_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="bids_agent_revision_ck"):
            await _raw_insert_bid(db, kind="draft", revision=0)

    async def test_final_rev0_rejected(self, db: Database) -> None:
        await seed_run(db)
        with pytest.raises(aiosqlite.IntegrityError, match="bids_agent_revision_ck"):
            await _raw_insert_bid(db, kind="final", revision=0)
