"""Row-seed helpers for DB-backed tests.

Plain async functions (not pytest fixtures) so inner test helpers
can call them and every write still routes through `repo.py` per
review rule 7.
"""

from __future__ import annotations

from app.contracts import InputKind, ItemKind
from app.db import repo
from app.db.init import Database
from tests.constants import FIXED_NOW


async def seed_run(db: Database, *, run_id: str = "run_abc") -> str:
    """Insert one `running` run. Returns its id."""

    await repo.insert_run(
        db,
        run_id=run_id,
        branch_id="BR-NJ-01",
        input_kind=InputKind.pdf,
        input_name="rfp.pdf",
        created_at=FIXED_NOW,
    )
    return run_id


async def seed_run_and_item(
    db: Database, *, run_id: str = "run_abc", item_id: str = "item_1"
) -> tuple[str, str]:
    """Insert a run plus one minimal material item. Used by any
    test that inserts into a table with an items FK."""

    await seed_run(db, run_id=run_id)
    await repo.insert_item(
        db,
        run_id=run_id,
        item_id=item_id,
        kind=ItemKind.material,
        description="2x6x16 PT",
        created_at=FIXED_NOW,
    )
    return run_id, item_id
