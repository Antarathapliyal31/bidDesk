"""`GET /api/meta`: UI-visible config surface.

Returns `{demo_faults_enabled, faults, models, limits}` per SPEC §13.
The `faults` list is static for Phase 2 (the demo-faults catalogue).
Phase 5 wires the real fault registry; the shape is pinned so the
frontend can render it today.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()


# Static fault catalogue — mirrors `useFaults()` on the frontend
# (`dev-fixtures/faults.ts`). Phase 5 populates this from the real
# fault registry under `runtime/faults.py`.
_FAULTS: list[dict[str, str]] = [
    {
        "key": "pricing_timeout_once",
        "title": "Pricing timeout (once)",
        "description": "First batch exceeds its 45 s timeout; retry succeeds.",
    },
    {
        "key": "pricing_timeout_always",
        "title": "Pricing timeout (always)",
        "description": "One batch always times out; lines flagged timeout, run partial.",
    },
    {
        "key": "analyst_malformed_once",
        "title": "Analyst malformed output (once)",
        "description": "First extraction returns invalid JSON; retry with feedback succeeds.",
    },
    {
        "key": "pricing_hallucinated_sku",
        "title": "Pricing hallucinated SKU",
        "description": "One proposal returns a non-existent SKU; code downgrades to no_match.",
    },
    {
        "key": "orchestrator_no_submit",
        "title": "Orchestrator never submits bid",
        "description": "Orchestrator ends without submit_bid; fallback assembly kicks in.",
    },
]


@router.get("/api/meta")
def meta() -> dict[str, object]:
    """Config surface for the UI. Faults are hidden client-side when
    `demo_faults_enabled=false`; the server lists them anyway so the
    enum stays discoverable in the OpenAPI schema."""

    settings = get_settings()
    return {
        "demo_faults_enabled": settings.demo_faults_enabled,
        "faults": _FAULTS,
        "models": {
            "orchestrator": settings.orchestrator_model,
            "analyst": settings.analyst_model,
            "analyst_text_only": settings.analyst_text_only_model,
            "pricing": settings.pricing_model,
        },
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_pages": settings.max_pages,
            "max_text_chars": settings.max_text_chars,
        },
    }
