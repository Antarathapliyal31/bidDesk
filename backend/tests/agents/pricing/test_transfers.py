"""`transfers.py` tests — SPEC §8.3 step 7 sort/exclusion rules."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.agents.pricing.transfers import transfer_options_for


def _pick_shortfall_sku(catalog_db: Path) -> tuple[str, str]:
    """Return a (sku, home_branch) pair where at least one OTHER
    branch stocks the SKU. Lets the sort/exclusion tests run
    against real seed data without hand-picking SKUs."""

    conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT s1.sku AS sku, s1.branch_id AS home "
            "FROM stock s1 "
            "WHERE EXISTS (SELECT 1 FROM stock s2 "
            "              WHERE s2.sku = s1.sku "
            "              AND s2.branch_id != s1.branch_id "
            "              AND s2.on_hand > 0) "
            "LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "seed missing a cross-branch stock row"
    return row["sku"], row["home"]


class TestShortfallZero:
    def test_no_shortfall_returns_empty(self, catalog_db: Path) -> None:
        sku, home = _pick_shortfall_sku(catalog_db)
        assert (
            transfer_options_for(sku, shortfall=0, home_branch_id=home, catalog_db_path=catalog_db)
            == []
        )


class TestExclusion:
    def test_home_branch_excluded(self, catalog_db: Path) -> None:
        sku, home = _pick_shortfall_sku(catalog_db)
        rows = transfer_options_for(
            sku, shortfall=1, home_branch_id=home, catalog_db_path=catalog_db
        )
        assert all(r.branch_id != home for r in rows)


class TestCoverSort:
    def test_full_cover_rows_appear_before_partial(self, catalog_db: Path) -> None:
        sku, home = _pick_shortfall_sku(catalog_db)
        # Pull the actual on_hand values for this SKU so we can
        # pick a shortfall that straddles some branches.
        conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
        try:
            onhand_rows = conn.execute(
                "SELECT branch_id, on_hand FROM stock "
                "WHERE sku = ? AND on_hand > 0 AND branch_id != ? "
                "ORDER BY on_hand DESC",
                (sku, home),
            ).fetchall()
        finally:
            conn.close()
        assert len(onhand_rows) >= 2, (
            "test SKU must have at least two other branches to exercise the sort"
        )
        # Pick shortfall so the top row fully covers but the bottom
        # row doesn't — straddles the full/partial boundary.
        top_qty = onhand_rows[0][1]
        bottom_qty = onhand_rows[-1][1]
        shortfall = (top_qty + bottom_qty) // 2 if top_qty > bottom_qty else top_qty
        if shortfall <= bottom_qty:
            shortfall = bottom_qty + 1
        rows = transfer_options_for(
            sku, shortfall=shortfall, home_branch_id=home, catalog_db_path=catalog_db
        )
        # Full-cover first (cover is None), partial-cover last (cover is "N of M").
        saw_partial = False
        for r in rows:
            if r.cover is None:
                assert not saw_partial, "full-cover rows must appear before partial rows"
                assert r.on_hand >= shortfall
            else:
                saw_partial = True
                assert r.on_hand < shortfall
                assert f" of {shortfall}" in r.cover

    def test_partial_cover_label_shape(self, catalog_db: Path) -> None:
        sku, home = _pick_shortfall_sku(catalog_db)
        huge_shortfall = 10_000_000  # no branch can cover
        rows = transfer_options_for(
            sku,
            shortfall=huge_shortfall,
            home_branch_id=home,
            catalog_db_path=catalog_db,
        )
        assert all(r.cover and r.cover.endswith(f" of {huge_shortfall}") for r in rows)
