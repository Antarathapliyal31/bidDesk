"""`running` runs flip to `interrupted` on startup (SPEC §10.5,
DECISIONS #18). `open_app_db` yields the flipped ids so the
lifespan can emit `run.finished(status="interrupted")` per run
once the event bus is live (Phase 2 step 3).
"""

from __future__ import annotations

from pathlib import Path

from app.contracts import InputKind
from app.db import repo
from app.db.init import Database, mark_running_as_interrupted, open_app_db

_FIRST_OPEN = "2026-11-11T12:00:00+00:00"
_SECOND_OPEN = "2026-11-11T13:00:00+00:00"


class TestStartupInterrupt:
    async def test_running_runs_flip_and_are_reported(
        self, db_with_interrupted: tuple[Database, list[str]]
    ) -> None:
        database, interrupted_ids = db_with_interrupted
        # Seeded run was `running` on the first open; the second
        # open flipped it to `interrupted` and surfaced the id.
        assert interrupted_ids == ["run_abc"]
        assert await repo.get_run_status(database, "run_abc") == "interrupted"

    async def test_run_finished_fields_recorded(
        self, db_with_interrupted: tuple[Database, list[str]]
    ) -> None:
        database, _ = db_with_interrupted
        cursor = await database.read_conn.execute(
            "SELECT status, outcome, outcome_detail, finished_at FROM runs WHERE id = 'run_abc'"
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        assert row["status"] == "interrupted"
        assert row["outcome"] == "error"
        assert row["outcome_detail"] == "Interrupted by server restart"
        assert row["finished_at"] == _SECOND_OPEN

    async def test_no_running_runs_yields_empty_list(self, db: Database) -> None:
        # Fresh DB has no rows. `UPDATE … RETURNING` returns an empty
        # set — the function returns an empty list without racing
        # anything.
        flipped = await mark_running_as_interrupted(
            db._write_conn, now_iso="2026-11-11T14:00:00+00:00"
        )
        assert flipped == []

    async def test_only_running_runs_are_touched(self, tmp_path: Path) -> None:
        """Seed completed + failed + running runs, close, reopen —
        only the running one flips. The other two keep their status
        and their original finished_at."""

        db_path = tmp_path / "app.db"
        async with open_app_db(db_path, now_iso=_FIRST_OPEN) as (database, _):
            # Create three runs through repo (all start `running`),
            # then transition two of them to terminal states via
            # direct SQL — there's no public repo finish function yet.
            for run_id in ("run_done", "run_fail", "run_live"):
                await repo.insert_run(
                    database,
                    run_id=run_id,
                    branch_id="BR-NJ-01",
                    input_kind=InputKind.pdf,
                    input_name=f"{run_id}.pdf",
                    created_at=_FIRST_OPEN,
                )
            async with database.write() as conn:
                await conn.execute(
                    "UPDATE runs SET status='completed', outcome='bid_ready', "
                    "finished_at=? WHERE id='run_done'",
                    (_FIRST_OPEN,),
                )
                await conn.execute(
                    "UPDATE runs SET status='failed', outcome='error', "
                    "finished_at=? WHERE id='run_fail'",
                    (_FIRST_OPEN,),
                )

        # Reopen: only `run_live` should flip.
        async with open_app_db(db_path, now_iso=_SECOND_OPEN) as (
            database,
            interrupted,
        ):
            assert interrupted == ["run_live"]

            cursor = await database.read_conn.execute(
                "SELECT id, status, finished_at FROM runs ORDER BY id"
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

        by_id = {row["id"]: dict(row) for row in rows}
        assert by_id["run_done"]["status"] == "completed"
        assert by_id["run_done"]["finished_at"] == _FIRST_OPEN
        assert by_id["run_fail"]["status"] == "failed"
        assert by_id["run_fail"]["finished_at"] == _FIRST_OPEN
        assert by_id["run_live"]["status"] == "interrupted"
        assert by_id["run_live"]["finished_at"] == _SECOND_OPEN
