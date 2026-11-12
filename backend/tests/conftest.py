"""Shared test fixtures used across the whole backend suite.

Lives at the top-level so sub-conftests (tests/db, tests/runtime,
…) only need to declare folder-specific fixtures. Seed helpers
live in `tests/_seeds.py` as plain async functions. Shared
constants live in `tests/constants.py`; nothing imports from this
conftest module.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from app.db.init import Database, open_app_db
from tests.constants import FIXED_NOW, FIXED_NOW_DT


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    """Fresh file-backed app.db per test.

    We use a file (in `tmp_path`) rather than `:memory:` because
    `Database` holds two connections — `:memory:` is per-connection
    so the read connection would see an empty database.
    """

    db_path = tmp_path / "app.db"
    async with open_app_db(db_path, now_iso=FIXED_NOW) as (database, _interrupted):
        yield database


@pytest.fixture
def ticking_clock() -> Callable[[], datetime]:
    """Monotonic clock that advances by 1 second per call.

    Useful for ordering tests: three successive emits yield three
    distinct, deterministic `ts` values. Base is `FIXED_NOW_DT`.
    """

    state = {"step": 0}

    def _clock() -> datetime:
        now = FIXED_NOW_DT + timedelta(seconds=state["step"])
        state["step"] += 1
        return now

    return _clock
