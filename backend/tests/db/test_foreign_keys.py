"""Foreign-key tests.

  - item_requirements, item_triage, price_results → items(run_id, id).
  - bid_edits, bids, conflicts, events, pages, documents → runs(id).

Each per-child-table test runs the same INSERT against a DB that
has no parent row and asserts the FK fires. A baseline next to it
inserts against an existing parent and asserts success — so a
regression in the WHERE/row shape can't make the rejection tests
"pass" for the wrong reason.

INSERTs here go through `db.write()` as raw SQL rather than via
repo wrappers: the FK is a DB-level guarantee that must hold no
matter where the INSERT comes from, and most of these child tables
don't have a public repo function yet (Phase 2 scope). The write
lock still serializes them with every other write — review rule 7.
"""

from __future__ import annotations

import aiosqlite
import pytest

from app.contracts import RequirementView, SourceRef
from app.db import repo
from app.db.init import Database

from .conftest import seed_run, seed_run_and_item

_NOW = "2026-11-11T12:00:00+00:00"


def _verified_requirement() -> RequirementView:
    return RequirementView(
        field="treatment",
        value="pressure_treated_above_ground",
        reason="rule",
        source=SourceRef(page=3, quote="PT", read_via="text", verified=True),
    )


# ---------------------------------------------------------------------------
# items FK — item_requirements → items(run_id, id)
# ---------------------------------------------------------------------------


class TestItemRequirementsFk:
    async def test_existing_item_accepts_requirement(self, db: Database) -> None:
        """Baseline: same shape as the rejection test below, but the
        parent item exists. Proves repo writes read_via correctly
        and only the FK differs between the two tests."""

        run_id, item_id = await seed_run_and_item(db)
        req_id = await repo.insert_item_requirement(
            db,
            run_id=run_id,
            item_id=item_id,
            requirement=_verified_requirement(),
            created_by="orchestrator",
            created_at=_NOW,
        )
        assert req_id > 0
        cursor = await db.read_conn.execute(
            "SELECT read_via FROM item_requirements WHERE id = ?", (req_id,)
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None and row[0] == "text"

    async def test_missing_item_rejected(self, db: Database) -> None:
        await seed_run(db)  # run exists but no item_1
        with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
            await repo.insert_item_requirement(
                db,
                run_id="run_abc",
                item_id="item_does_not_exist",
                requirement=_verified_requirement(),
                created_by="orchestrator",
                created_at=_NOW,
            )


# ---------------------------------------------------------------------------
# Parametrized missing-parent sweep across every child table
# ---------------------------------------------------------------------------
# Each case: (name, needs_item_fk, sql, params).
#   - needs_item_fk cases seed only the RUN (no item) so an items FK
#     fires. They use a valid item_id placeholder so the FK is the
#     only thing that differs from a happy-path row.
#   - the rest seed nothing (fresh run-less DB) and use a
#     non-existent run_id so the runs FK fires.
# ---------------------------------------------------------------------------


_MISSING_ITEM_CASES = [
    (
        "item_triage",
        """
        INSERT INTO item_triage (run_id, item_id, category, reason, created_at)
        VALUES ('run_abc', 'item_missing', 'price', 'r', ?)
        """,
        (_NOW,),
    ),
    (
        "price_results",
        """
        INSERT INTO price_results (
          run_id, item_id, attempt, status, created_at
        ) VALUES ('run_abc', 'item_missing', 1, 'matched', ?)
        """,
        (_NOW,),
    ),
]


_MISSING_RUN_CASES = [
    (
        "documents",
        """
        INSERT INTO documents (run_id, filename, sha256, page_count, stored_path)
        VALUES ('run_missing', 'rfp.pdf', 'sha', 1, '/tmp/rfp.pdf')
        """,
        (),
    ),
    (
        "pages",
        """
        INSERT INTO pages (run_id, page_no, route) VALUES ('run_missing', 1, 'text')
        """,
        (),
    ),
    (
        "conflicts",
        """
        INSERT INTO conflicts (id, run_id, type, side_a_json, side_b_json, explanation, created_at)
        VALUES ('conf_1', 'run_missing', 'spec_vs_list', '{}', '{}', 'x', ?)
        """,
        (_NOW,),
    ),
    (
        "events",
        """
        INSERT INTO events (run_id, ts, type, payload_json)
        VALUES ('run_missing', ?, 'run.started', '{}')
        """,
        (_NOW,),
    ),
    (
        "bids",
        """
        INSERT INTO bids (id, run_id, kind, revision, data_json, created_at, updated_at)
        VALUES ('bid_1', 'run_missing', 'agent', 0, '{}', ?, ?)
        """,
        (_NOW, _NOW),
    ),
    (
        "bid_edits",
        """
        INSERT INTO bid_edits (run_id, revision, op_json, created_at)
        VALUES ('run_missing', 1, '{}', ?)
        """,
        (_NOW,),
    ),
]


@pytest.mark.parametrize(
    ("table", "sql", "params"),
    _MISSING_ITEM_CASES,
    ids=[c[0] for c in _MISSING_ITEM_CASES],
)
async def test_missing_item_parent_rejected(
    db: Database, table: str, sql: str, params: tuple[object, ...]
) -> None:
    await seed_run(db)  # run exists but no item
    with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
        async with db.write() as conn:
            await conn.execute(sql, params)


@pytest.mark.parametrize(
    ("table", "sql", "params"),
    _MISSING_RUN_CASES,
    ids=[c[0] for c in _MISSING_RUN_CASES],
)
async def test_missing_run_parent_rejected(
    db: Database, table: str, sql: str, params: tuple[object, ...]
) -> None:
    with pytest.raises(aiosqlite.IntegrityError, match="FOREIGN KEY"):
        async with db.write() as conn:
            await conn.execute(sql, params)
