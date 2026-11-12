"""Lifespan tests:
- `run.finished(status="interrupted")` emitted per orphan run on
  startup (SPEC §10.5, DECISIONS #18).
- Missing catalog.db fails startup fast with a clear message —
  not a mystery 500 on the first `/api/branches` call.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.catalog import seed
from app.contracts import InputKind
from app.db import repo
from app.db.init import open_app_db

_FIXED_NOW = "2026-11-11T12:00:00+00:00"


@pytest.fixture
def seeded_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Seed the catalog and point Settings at tmp_path. Returns
    tmp_path so the test can inspect the app DB after startup."""

    app_db = tmp_path / "app.db"
    catalog_db = tmp_path / "catalog.db"
    seed.main(catalog_db)

    monkeypatch.setenv("APP_DB_PATH", str(app_db))
    monkeypatch.setenv("CATALOG_DB_PATH", str(catalog_db))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import app.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)
    return tmp_path


class TestLifespanInterruptedEmit:
    async def test_interrupted_runs_get_one_run_finished_event_each(self, seeded_env: Path) -> None:
        # Pre-seed two running runs via open_app_db (first open).
        app_db = seeded_env / "app.db"
        async with open_app_db(app_db, now_iso=_FIXED_NOW) as (database, _):
            await repo.insert_run(
                database,
                run_id="run_a",
                branch_id="BR-NJ-01",
                input_kind=InputKind.pdf,
                input_name="a.pdf",
                created_at=_FIXED_NOW,
            )
            await repo.insert_run(
                database,
                run_id="run_b",
                branch_id="BR-NJ-01",
                input_kind=InputKind.pdf,
                input_name="b.pdf",
                created_at=_FIXED_NOW,
            )

        # Now boot the real FastAPI lifespan — it should flip both
        # runs to interrupted AND emit one run.finished event each.
        from app.main import create_app

        app = create_app()
        async with app.router.lifespan_context(app):
            pass  # lifespan setup runs here

        # Open the DB read-only and inspect events.
        conn = sqlite3.connect(app_db)
        try:
            cursor = conn.execute(
                "SELECT run_id, payload_json FROM events WHERE type='run.finished' ORDER BY id"
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        assert len(rows) == 2
        assert {r[0] for r in rows} == {"run_a", "run_b"}
        for _run_id, payload_json in rows:
            payload = json.loads(payload_json)
            assert payload["status"] == "interrupted"
            assert payload["outcome"] == "error"
            assert payload["outcome_detail"] == "Interrupted by server restart"


class TestLifespanCatalogCheck:
    async def test_missing_catalog_fails_startup_fast(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lifespan must raise RuntimeError with "run `make seed`" if
        catalog.db isn't on disk. Prevents the "mystery 500 on first
        request" failure mode."""

        missing_catalog = tmp_path / "catalog.db"
        assert not missing_catalog.exists()

        monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
        monkeypatch.setenv("CATALOG_DB_PATH", str(missing_catalog))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        import app.config as cfg

        monkeypatch.setattr(cfg, "_settings", None)

        from app.main import create_app

        app = create_app()
        with pytest.raises(RuntimeError, match=r"catalog\.db not found.*make seed"):
            async with app.router.lifespan_context(app):
                pass  # unreachable
