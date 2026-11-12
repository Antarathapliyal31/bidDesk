"""Agents-folder fixtures. Re-exposes the `bus` fixture from
tests/runtime/conftest.py so integration tests under tests/agents
can use it too — pytest doesn't auto-propagate across sibling
folders."""

from __future__ import annotations

import pytest_asyncio

from app.db.init import Database
from app.runtime.events import EventBus
from tests.constants import FIXED_NOW_DT


@pytest_asyncio.fixture
async def bus(db: Database) -> EventBus:
    """Mirror of tests/runtime/conftest.py::bus."""

    return EventBus(db, clock=lambda: FIXED_NOW_DT)
