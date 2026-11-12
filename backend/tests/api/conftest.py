"""API-test fixtures. Seeds a fresh catalog + app DB under tmp_path
and points the Settings singleton at them so the real FastAPI app
boots end-to-end per test."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.catalog import seed


@pytest.fixture
def app_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point Settings at tmp DBs, seed the catalog, reset the
    singleton. Yields `tmp_path` so tests can inspect files."""

    app_db = tmp_path / "app.db"
    catalog_db = tmp_path / "catalog.db"

    # Seed the catalog from Phase 1's deterministic builder.
    seed.main(catalog_db)

    monkeypatch.setenv("APP_DB_PATH", str(app_db))
    monkeypatch.setenv("CATALOG_DB_PATH", str(catalog_db))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-value")
    monkeypatch.setenv("DEMO_FAULTS_ENABLED", "false")

    import app.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)
    yield tmp_path
    monkeypatch.setattr(cfg, "_settings", None)


@pytest_asyncio.fixture
async def client(app_env: Path) -> AsyncIterator[AsyncClient]:
    """Async HTTPX client over the real FastAPI app, with lifespan
    wired. Each test gets a fresh app instance."""

    # Import `create_app` AFTER the env patches are in place so the
    # Settings singleton picks up tmp paths.
    from app.main import create_app

    app: FastAPI = create_app()
    transport = ASGITransport(app=app)
    async with (
        AsyncClient(transport=transport, base_url="http://testserver") as c,
        app.router.lifespan_context(app),
    ):
        yield c
