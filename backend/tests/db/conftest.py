"""DB-folder-specific fixtures. The shared `db` fixture lives in
`tests/conftest.py`; this file keeps only the db-centric
scenarios.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from app.contracts import InputKind
from app.db import repo
from app.db.init import Database, open_app_db
from tests.constants import FIXED_NOW

_SECOND_OPEN = "2026-11-11T13:00:00+00:00"


@pytest_asyncio.fixture
async def db_with_interrupted(
    tmp_path: Path,
) -> AsyncIterator[tuple[Database, list[str]]]:
    """App.db that has a `running` run on first open; the second
    open (= server restart) flips it to interrupted."""

    db_path = tmp_path / "app.db"
    async with open_app_db(db_path, now_iso=FIXED_NOW) as (database, _):
        await repo.insert_run(
            database,
            run_id="run_abc",
            branch_id="BR-NJ-01",
            input_kind=InputKind.pdf,
            input_name="rfp.pdf",
            created_at=FIXED_NOW,
        )
    async with open_app_db(db_path, now_iso=_SECOND_OPEN) as (database, interrupted):
        yield database, interrupted
