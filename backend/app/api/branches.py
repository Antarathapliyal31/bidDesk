"""`GET /api/branches`: lists the three BidDesk branches.

Reads the catalog DB (read-only) rather than the hard-coded seed
list — the catalog is the single source of truth once built.
Phase 5 widens this to a dependency-injected resource; for now a
plain sqlite3 connection per request is simple and correct (3 rows,
no hot path).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Request

from app.config import get_settings

router = APIRouter()


@router.get("/api/branches")
def list_branches(_request: Request) -> list[dict[str, object]]:
    """Return every branch row. The frontend treats this as
    `staleTime: Infinity` (§F1) so a per-request sqlite connection
    is fine."""

    settings = get_settings()
    conn = sqlite3.connect(f"file:{settings.catalog_db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, city, state, tax_rate_ppm FROM branches ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "city": row["city"],
            "state": row["state"],
            "tax_rate_ppm": row["tax_rate_ppm"],
        }
        for row in rows
    ]
