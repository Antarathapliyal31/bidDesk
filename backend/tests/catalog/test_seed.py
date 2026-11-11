"""Catalog seed tests — determinism, invariants, scenarios, atomic build.

The seed produces a file that both the pricing path and the semantic
layer read, so these tests act as the contract: if a future tweak to
seed.py drops `coverage_sqft` off a SHT row or introduces wallclock
access, these assertions catch it before the bundle loader even runs.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from app.catalog import seed
from app.catalog.semantic_layer import load_bundle

_BUNDLE_DIR = Path(__file__).resolve().parents[2] / "app" / "catalog" / "semantic"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """A freshly seeded catalog.db in a tmp dir."""
    return seed.main(tmp_path / "catalog.db")


def _ro_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# No wallclock / no random
# ---------------------------------------------------------------------------


class TestNoWallclock:
    def test_seed_source_has_no_wallclock_calls(self) -> None:
        source = Path(seed.__file__).read_text(encoding="utf-8")
        forbidden = [
            "datetime.now",
            "datetime.today",
            "datetime.utcnow",
            "time.time(",
            "time.monotonic(",
        ]
        hits = [t for t in forbidden if t in source]
        assert not hits, f"seed.py must not touch wallclock; found: {hits}"

    def test_seed_source_has_no_random_import(self) -> None:
        source = Path(seed.__file__).read_text(encoding="utf-8")
        assert not re.search(r"^\s*import\s+random\b", source, re.M)
        assert not re.search(r"^\s*from\s+random\s+import", source, re.M)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def _canonical_dump(path: Path) -> str:
    conn = _ro_conn(path)
    try:
        tables: dict[str, list[dict]] = {}
        for table, pk in (
            ("branches", "id"),
            ("products", "sku"),
            ("stock", "sku, branch_id"),
        ):
            cur = conn.execute(f"SELECT * FROM {table} ORDER BY {pk}")
            tables[table] = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    return json.dumps(tables, sort_keys=True, default=str)


class TestDeterminism:
    def test_two_builds_produce_identical_canonical_json(self, tmp_path: Path) -> None:
        db1 = seed.main(tmp_path / "a.db")
        db2 = seed.main(tmp_path / "b.db")
        h1 = hashlib.sha256(_canonical_dump(db1).encode()).hexdigest()
        h2 = hashlib.sha256(_canonical_dump(db2).encode()).hexdigest()
        assert h1 == h2

    def test_fixed_price_updated_at(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            cur = conn.execute("SELECT DISTINCT price_updated_at FROM products")
            values = [row[0] for row in cur.fetchall()]
        finally:
            conn.close()
        assert values == [seed.PRICE_UPDATED_AT]


# ---------------------------------------------------------------------------
# Content invariants — SPEC §12.1
# ---------------------------------------------------------------------------


class TestContentInvariants:
    def test_sht_bag_roll_have_coverage_sqft(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            rows = conn.execute(
                "SELECT sku, sale_unit, coverage_sqft FROM products "
                "WHERE sale_unit IN ('SHT', 'BAG', 'ROLL')"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "expected at least one SHT/BAG/ROLL product"
        missing = [r["sku"] for r in rows if r["coverage_sqft"] is None or r["coverage_sqft"] <= 0]
        assert not missing, f"SHT/BAG/ROLL without coverage_sqft: {missing}"

    def test_bdl_has_pack_qty(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            rows = conn.execute(
                "SELECT sku, pack_qty FROM products WHERE sale_unit = 'BDL'"
            ).fetchall()
        finally:
            conn.close()
        assert rows
        missing = [r["sku"] for r in rows if r["pack_qty"] is None or r["pack_qty"] <= 0]
        assert not missing, f"BDL without pack_qty: {missing}"

    def test_dimensional_lumber_ea_has_length_and_nominal(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            rows = conn.execute(
                "SELECT sku, length_ft, nominal_size FROM products "
                "WHERE sale_unit = 'EA' AND category = 'dimensional_lumber'"
            ).fetchall()
        finally:
            conn.close()
        assert rows
        bad = [r["sku"] for r in rows if not r["length_ft"] or not r["nominal_size"]]
        assert not bad, f"lumber EA missing length/nominal: {bad}"

    def test_prices_positive(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            bad = conn.execute(
                "SELECT COUNT(*) FROM products WHERE unit_price_cents <= 0"
            ).fetchone()[0]
        finally:
            conn.close()
        assert bad == 0

    def test_a_few_inactive_skus_exist(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            n = conn.execute("SELECT COUNT(*) FROM products WHERE active = 0").fetchone()[0]
        finally:
            conn.close()
        assert 1 <= n <= 10, f"expected 1..10 inactive SKUs, got {n}"


# ---------------------------------------------------------------------------
# Exact tax rates per branch
# ---------------------------------------------------------------------------


class TestBranches:
    def test_exact_tax_rates(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            rows = conn.execute(
                "SELECT id, state, city, tax_rate_ppm FROM branches ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        assert [tuple(r) for r in rows] == [
            ("BR-NJ-01", "NJ", "Edison", 66250),
            ("BR-NY-01", "NY", "Islip", 86250),
            ("BR-PA-01", "PA", "Allentown", 60000),
        ]


# ---------------------------------------------------------------------------
# Seeded values ⊆ YAML allowed values
# ---------------------------------------------------------------------------


class TestSeededValuesWithinYamlAllowed:
    def test_filterable_field_values_are_declared(self, seeded_db: Path) -> None:
        bundle = load_bundle(_BUNDLE_DIR, seeded_db)
        products = bundle.entities["products"]
        checked = {
            name: set(f.values) for name, f in products.fields.items() if f.filterable and f.values
        }
        assert checked, "expected at least one filterable+enumerated products field"
        conn = _ro_conn(seeded_db)
        try:
            offences: list[str] = []
            for field, allowed in checked.items():
                cur = conn.execute(
                    f"SELECT DISTINCT {field} FROM products WHERE {field} IS NOT NULL"
                )
                for (val,) in cur.fetchall():
                    if val not in allowed:
                        offences.append(
                            f"products.{field} = {val!r} not in YAML values "
                            f"{sorted(allowed, key=str)}"
                        )
        finally:
            conn.close()
        assert not offences, "\n".join(offences)


# ---------------------------------------------------------------------------
# Stock density, baseline, scenarios
# ---------------------------------------------------------------------------


class TestStock:
    def test_stock_is_dense(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            nstock = conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0]
            nprods = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            nbranch = conn.execute("SELECT COUNT(*) FROM branches").fetchone()[0]
        finally:
            conn.close()
        assert nstock == nprods * nbranch

    def test_lumber_baseline_2x4x8(self, seeded_db: Path) -> None:
        # LUM-2X4-8-SPF-2 has no explicit override → baseline for
        # dimensional_lumber = 120/80/50.
        conn = _ro_conn(seeded_db)
        try:
            rows = {
                r["branch_id"]: r["on_hand"]
                for r in conn.execute(
                    "SELECT branch_id, on_hand FROM stock WHERE sku = 'LUM-2X4-8-SPF-2'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert rows == {"BR-NJ-01": 120, "BR-NY-01": 80, "BR-PA-01": 50}

    def test_fascia_trim_override(self, seeded_db: Path) -> None:
        # TRM-1X6-10-SPF-2 is the "no-run-branch, others have it" scenario.
        conn = _ro_conn(seeded_db)
        try:
            rows = {
                r["branch_id"]: r["on_hand"]
                for r in conn.execute(
                    "SELECT branch_id, on_hand FROM stock WHERE sku = 'TRM-1X6-10-SPF-2'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert rows == {"BR-NJ-01": 0, "BR-NY-01": 30, "BR-PA-01": 20}

    def test_full_cover_transfer_scenario(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            rows = {
                r["branch_id"]: r["on_hand"]
                for r in conn.execute(
                    "SELECT branch_id, on_hand FROM stock WHERE sku = 'LUM-2X6-16-SYP-2-PTAG'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert rows == {"BR-NJ-01": 20, "BR-NY-01": 80, "BR-PA-01": 60}

    def test_partial_cover_scenario(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            rows = {
                r["branch_id"]: r["on_hand"]
                for r in conn.execute(
                    "SELECT branch_id, on_hand FROM stock WHERE sku = 'LUM-2X10-18-SPF-2'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert rows == {"BR-NJ-01": 5, "BR-NY-01": 12, "BR-PA-01": 8}

    def test_zero_stock_scenario(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            on_hands = [
                r[0]
                for r in conn.execute(
                    "SELECT on_hand FROM stock WHERE sku = 'ROOF-UNDERLAY-ROLL'"
                ).fetchall()
            ]
        finally:
            conn.close()
        assert on_hands == [0, 0, 0]

    def test_next_receipt_dates_after_2026_12_15(self, seeded_db: Path) -> None:
        conn = _ro_conn(seeded_db)
        try:
            rows = conn.execute(
                "SELECT sku, branch_id, next_receipt_date FROM stock "
                "WHERE next_receipt_date IS NOT NULL"
            ).fetchall()
        finally:
            conn.close()
        assert rows, "expected at least one non-null next_receipt_date"
        bad = [
            (r["sku"], r["branch_id"], r["next_receipt_date"])
            for r in rows
            if r["next_receipt_date"] <= "2026-12-15"
        ]
        assert not bad, f"receipt dates not after 2026-12-15: {bad}"


# ---------------------------------------------------------------------------
# Foreign keys enforced
# ---------------------------------------------------------------------------


class TestForeignKeys:
    def test_fk_rejects_bogus_sku(self, seeded_db: Path, tmp_path: Path) -> None:
        copy = tmp_path / "copy.db"
        copy.write_bytes(seeded_db.read_bytes())
        conn = sqlite3.connect(copy)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO stock (sku, branch_id, on_hand) "
                    "VALUES ('NOPE-NOT-A-SKU', 'BR-NJ-01', 10)"
                )
                conn.commit()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Override validation
# ---------------------------------------------------------------------------


class TestOverrideValidation:
    def test_removed_sku_in_override_raises(self) -> None:
        # Drop TRM-1X6-10-SPF-2 (which has overrides) from the products
        # list and call _validate_stock_overrides. The validator should
        # raise ValueError naming the orphaned SKU — proving the typo guard
        # actually fires.
        products = [p for p in seed.build_products() if p["sku"] != "TRM-1X6-10-SPF-2"]
        with pytest.raises(ValueError, match=r"TRM-1X6-10-SPF-2"):
            seed._validate_stock_overrides(products)

    def test_missing_category_baseline_raises(self) -> None:
        # Append a product whose category isn't in _BASELINE_STOCK_BY_CATEGORY
        # to the full product list. Override validation passes (we don't
        # add any override for the bogus SKU), so the category check is
        # what trips — must name the offending category.
        bogus_product = {
            "sku": "FAKE-WIDGET-1",
            "category": "not_a_real_category",
            "nominal_size": None,
            "length_ft": None,
            "thickness_in": None,
            "width_in": None,
            "height_in": None,
            "grade": None,
            "species": None,
            "treatment": "none",
            "fire_rating": "none",
            "moisture_resistant": 0,
            "r_value": None,
            "sale_unit": "EA",
            "pack_qty": None,
            "coverage_sqft": None,
            "unit_price_cents": 100,
            "price_updated_at": seed.PRICE_UPDATED_AT,
            "active": 1,
            "name": "fake",
            "description": "fake",
        }
        with pytest.raises(ValueError, match=r"not_a_real_category"):
            seed.build_stock([*seed.build_products(), bogus_product])


# ---------------------------------------------------------------------------
# Atomic build / stale .tmp
# ---------------------------------------------------------------------------


class TestAtomicBuild:
    def test_rollback_leaves_original_db_untouched(self, tmp_path: Path) -> None:
        db = tmp_path / "catalog.db"
        tmp = tmp_path / "catalog.db.tmp"

        # First build succeeds → remember the bytes hash.
        seed.main(db)
        original_hash = hashlib.sha256(db.read_bytes()).hexdigest()

        # Monkey-patching sqlite3.Connection.commit isn't possible (C type),
        # so we patch seed.build_stock to raise. The build then fails
        # before commit; except-clause unlinks .tmp and re-raises.
        with (
            patch.object(seed, "build_stock", side_effect=RuntimeError("boom")),
            pytest.raises(RuntimeError, match="boom"),
        ):
            seed.main(db)

        # Original DB unchanged; no .tmp lingering.
        assert hashlib.sha256(db.read_bytes()).hexdigest() == original_hash
        assert not tmp.exists()

    def test_stale_tmp_is_cleaned_before_build(self, tmp_path: Path) -> None:
        db = tmp_path / "catalog.db"
        tmp = tmp_path / "catalog.db.tmp"
        tmp.write_bytes(b"garbage from a previous crashed run")
        seed.main(db)
        assert db.exists()
        assert not tmp.exists()


# ---------------------------------------------------------------------------
# Schema agreement
# ---------------------------------------------------------------------------


class TestSchemaAgreement:
    def test_bundle_loads_against_seeded_db(self, seeded_db: Path) -> None:
        # Passing load_bundle proves the columns in catalog.db match every
        # field declared in products.yaml / stock.yaml / branches.yaml —
        # that's the whole column-drift check. Having the seed produce a
        # DB the loader accepts closes the schema loop.
        bundle = load_bundle(_BUNDLE_DIR, seeded_db)
        assert set(bundle.entities) == {"products", "stock", "branches"}
