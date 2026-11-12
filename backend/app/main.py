"""FastAPI app factory.

Phase 2 lifespan wires the app DB (schema applied, interrupted runs
flipped) and the EventBus (emits one `run.finished(status="interrupted")`
per flipped run id). Routes: `/api/health`, `/api/branches`,
`/api/meta`. Exception handlers: `ApiError` → envelope, anything
else → opaque HTTP 500 (no leaked internals).

Phases 3+ add `/api/runs`, `/api/runs/{id}` + variants, SSE, etc.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from app.api import branches as branches_router
from app.api import meta as meta_router
from app.api import runs as runs_router
from app.api.errors import install_exception_handlers
from app.config import get_settings
from app.db.init import open_app_db
from app.runtime.events import EventBus, emit_interrupted_run_finished
from app.runtime.runner import RunRegistry


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open app DB + EventBus; emit `run.finished(interrupted)` for
    orphan runs. State attaches to `app.state` so routes can pull
    it via dependencies (Phase 3+).

    Fails fast if the catalog DB is missing — otherwise every
    `/api/branches` call would 500 later. The user experience is
    a clean startup error, not a mysterious 500 on first request.
    """

    settings = get_settings()
    if not settings.catalog_db_path.exists():
        raise RuntimeError(f"catalog.db not found at {settings.catalog_db_path} — run `make seed`")
    async with open_app_db(settings.app_db_path, now_iso=_now_iso) as (
        database,
        interrupted_ids,
    ):
        bus = EventBus(database)
        await emit_interrupted_run_finished(bus, interrupted_ids)
        registry = RunRegistry()
        app.state.db = database
        app.state.bus = bus
        app.state.settings = settings
        app.state.run_registry = registry
        try:
            yield
        finally:
            # Shutdown: cancel in-flight pipelines. Rows + events
            # survive; next startup flips them to `interrupted`.
            await registry.cancel_all()


def create_app() -> FastAPI:
    app = FastAPI(title="BidDesk", version="0.1.0", lifespan=_lifespan)

    install_exception_handlers(app)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(branches_router.router)
    app.include_router(meta_router.router)
    app.include_router(runs_router.router)
    return app


app = create_app()
