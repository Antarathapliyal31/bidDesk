"""FastAPI app factory.

Phase 0: only exposes /api/health so we can verify the server boots.
Real lifespan (DB init, interrupted-run cleanup per SPEC §10.5) lands later.
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="BidDesk", version="0.1.0")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
