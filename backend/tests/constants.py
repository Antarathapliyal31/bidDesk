"""Shared test constants. Timestamps are frozen so every DB
fixture, seed helper, and clock-injected test reads the same
reference value — change it here and everything downstream moves
together.
"""

from __future__ import annotations

from datetime import UTC, datetime

# ISO-8601 UTC string used by the `db` fixture, the seed helpers,
# and any test that needs a stable created_at.
FIXED_NOW = "2026-11-11T12:00:00+00:00"

# Same instant as a tz-aware datetime — used by injected clocks
# (EventBus, ticking_clock base). Keeping both forms in one file
# means a future bump stays in lockstep across string + datetime
# call sites.
FIXED_NOW_DT = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
