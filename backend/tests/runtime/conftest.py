"""Runtime-folder-specific fixtures. The shared `db` fixture lives
in `tests/conftest.py`."""

from __future__ import annotations

import pytest_asyncio

from app.db.init import Database
from app.runtime.events import EventBus
from tests.constants import FIXED_NOW_DT


@pytest_asyncio.fixture
async def bus(db: Database) -> EventBus:
    """EventBus with a frozen clock so event `ts` is predictable
    in tests. Tests that want ordered timestamps swap this for
    `ticking_clock` (top-level fixture)."""

    return EventBus(db, clock=lambda: FIXED_NOW_DT)
