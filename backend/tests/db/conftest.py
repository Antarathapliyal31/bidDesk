"""Shared DB fixtures: a fresh file-backed app.db per test.

We use a file (in tmp_path) rather than `:memory:` because the two
connections in `Database` share state only when the file is on
disk; `:memory:` is per-connection so the read connection would see
an empty database.

Every write in these fixtures goes through `repo.py` — the review
rule "shared aiosqlite connection: every write transaction goes
through repo.py under one asyncio.Lock" applies to test setup too,
not just to app code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from app.contracts import InputKind, ItemKind
from app.db import repo
from app.db.init import Database, open_app_db

_FIXED_NOW = "2026-11-11T12:00:00+00:00"


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """Fresh app.db for one test. Deterministic startup timestamp."""

    db_path = tmp_path / "app.db"
    async with open_app_db(db_path, now_iso=_FIXED_NOW) as (database, _interrupted):
        yield database


@pytest_asyncio.fixture
async def db_with_interrupted(
    tmp_path: Path,
) -> AsyncIterator[tuple[Database, list[str]]]:
    """App.db that has a `running` run on first open; the second
    open (= server restart) should flip it to interrupted."""

    db_path = tmp_path / "app.db"
    async with open_app_db(db_path, now_iso=_FIXED_NOW) as (database, _):
        await repo.insert_run(
            database,
            run_id="run_abc",
            branch_id="BR-NJ-01",
            input_kind=InputKind.pdf,
            input_name="rfp.pdf",
            created_at=_FIXED_NOW,
        )
    async with open_app_db(db_path, now_iso="2026-11-11T13:00:00+00:00") as (
        database,
        interrupted,
    ):
        yield database, interrupted


# ---------------------------------------------------------------------------
# Row helpers — seed enough state to drop into individual tests
# ---------------------------------------------------------------------------


async def seed_run(db: Database, *, run_id: str = "run_abc") -> str:
    """Insert one `running` run. Returns its id."""

    await repo.insert_run(
        db,
        run_id=run_id,
        branch_id="BR-NJ-01",
        input_kind=InputKind.pdf,
        input_name="rfp.pdf",
        created_at=_FIXED_NOW,
    )
    return run_id


async def seed_run_and_item(
    db: Database, *, run_id: str = "run_abc", item_id: str = "item_1"
) -> tuple[str, str]:
    """Insert a run plus one minimal material item. Returns
    (run_id, item_id). Used by any test that inserts into a table
    with an items FK (item_triage / item_requirements / price_results)."""

    await seed_run(db, run_id=run_id)
    await repo.insert_item(
        db,
        run_id=run_id,
        item_id=item_id,
        kind=ItemKind.material,
        description="2x6x16 PT",
        created_at=_FIXED_NOW,
    )
    return run_id, item_id
