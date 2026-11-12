"""query_builder.py tests (SPEC §8.2).

Scope:
  - Allowlist: unknown field + non-filterable field rejected.
  - Type check: bool-only for bool fields; int rejects bool; str
    for text/enum. Invalid enum value rejected by `field.values`.
  - Catalog guardrails: `active = 1` always appended; stock LEFT
    JOIN always carries the run's branch_id as a bound param.
  - text_search LIKE: parameterized, not interpolated.
  - Named queries: stock_all_branches returns rows for every
    branch that stocks the SKU.

All tests use the real seeded catalog from `app.catalog.seed.main`
so the row counts / SKU shape match production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.catalog.query_builder import (
    CatalogQuery,
    QueryBuilderError,
    build_sql,
    query_catalog,
    run_named_query,
)
from app.catalog.seed import main as seed_main
from app.catalog.semantic_layer import load_bundle

_BUNDLE_DIR = Path(__file__).resolve().parents[2] / "app" / "catalog" / "semantic"


@pytest.fixture(scope="module")
def catalog_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fully seeded catalog.db. Module-scoped so the 100+ products
    aren't rebuilt per test."""

    tmp = tmp_path_factory.mktemp("catalog")
    return seed_main(tmp / "catalog.db")


@pytest.fixture(scope="module")
def bundle(catalog_db: Path):
    return load_bundle(_BUNDLE_DIR, catalog_db)


class TestAllowlist:
    def test_unknown_field_rejected(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={"not_a_field": "x"})
        with pytest.raises(QueryBuilderError, match=r"unknown field 'not_a_field'"):
            build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")

    def test_non_filterable_field_rejected(self, bundle) -> None:
        # `sale_unit` is returnable but not filterable.
        query = CatalogQuery(entity="products", filters={"sale_unit": "EA"})
        with pytest.raises(QueryBuilderError, match=r"'sale_unit' is not filterable"):
            build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")

    def test_enum_value_rejected(self, bundle) -> None:
        # species is enum=[SPF, SYP, DF]
        query = CatalogQuery(entity="products", filters={"species": "Oak"})
        with pytest.raises(QueryBuilderError, match=r"not an allowed value for 'species'"):
            build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")

    def test_unknown_entity_rejected(self, bundle, catalog_db: Path) -> None:
        with pytest.raises(QueryBuilderError, match=r"entity 'stock'"):
            # `CatalogQuery.entity` is Literal["products"], so build
            # the Pydantic model with model_construct to bypass the
            # type check and prove the runtime guard still fires.
            query = CatalogQuery.model_construct(
                entity="stock", filters={}, text_search=None, limit=20
            )
            query_catalog(bundle, query, catalog_db_path=catalog_db, branch_id="BR-NJ-01")


class TestTypeValidation:
    """Antara addition #1: filter values must match the declared type.

    Fixed Phase 2 inherited pain points: bool-fields reject 0/1 ints,
    int-fields reject True/False (even though `isinstance(True, int)`).
    """

    def test_bool_field_accepts_bool(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={"moisture_resistant": True}, limit=20)
        # No raise.
        build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")

    def test_bool_field_rejects_int(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={"moisture_resistant": 1}, limit=20)
        with pytest.raises(QueryBuilderError, match=r"expects bool"):
            build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")

    def test_int_field_accepts_int(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={"length_ft": 8}, limit=20)
        build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")

    def test_int_field_rejects_bool(self, bundle) -> None:
        """Python's `isinstance(True, int)` is True — the trap this
        check exists to close."""

        query = CatalogQuery(entity="products", filters={"length_ft": True}, limit=20)
        with pytest.raises(QueryBuilderError, match=r"expects int.*bool"):
            build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")

    def test_enum_field_rejects_int(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={"species": 1}, limit=20)
        with pytest.raises(QueryBuilderError, match=r"expects str"):
            build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")


class TestGuardrails:
    def test_active_eq_1_always_appended(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={}, limit=20)
        sql, params = build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")
        assert "p.active = 1" in sql
        # branch_id bound first; no literal interpolation.
        assert params[0] == "BR-NJ-01"
        assert "BR-NJ-01" not in sql  # the branch_id is NEVER literal

    def test_stock_left_joined_for_branch(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={}, limit=20)
        sql, params = build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")
        assert "LEFT JOIN stock" in sql
        assert "s.sku = p.sku AND s.branch_id = ?" in sql
        # branch_id is the FIRST placeholder bound (matches JOIN order).
        assert params[0] == "BR-NJ-01"

    def test_on_hand_in_result(self, bundle, catalog_db: Path) -> None:
        # A SPF 2x4x8 exists everywhere in the seed.
        query = CatalogQuery(
            entity="products",
            filters={"nominal_size": "2x4", "length_ft": 8, "species": "SPF"},
            limit=20,
        )
        rows = query_catalog(bundle, query, catalog_db_path=catalog_db, branch_id="BR-NJ-01")
        assert rows, "seed should include at least one SPF 2x4x8 SKU"
        assert all("on_hand" in r for r in rows)


class TestTextSearch:
    def test_text_search_parameterized(self, bundle) -> None:
        query = CatalogQuery(entity="products", filters={}, text_search="drywall", limit=20)
        sql, params = build_sql(bundle.entities["products"], query, branch_id="BR-NJ-01")
        # LIKE clauses present
        assert "LIKE ?" in sql
        # Bind value is parameterized, not substituted
        assert "%drywall%" in params
        # Branch_id still first
        assert params[0] == "BR-NJ-01"

    def test_text_search_finds_rows(self, bundle, catalog_db: Path) -> None:
        query = CatalogQuery(
            entity="products",
            filters={},
            text_search="drywall",
            limit=20,
        )
        rows = query_catalog(bundle, query, catalog_db_path=catalog_db, branch_id="BR-NJ-01")
        assert rows
        assert all(
            "drywall" in str(r.get("name", "")).lower()
            or "drywall" in str(r.get("description", "")).lower()
            or r.get("category") == "drywall"
            for r in rows
        )


class TestNamedQueries:
    def test_stock_all_branches_returns_rows(self, catalog_db: Path) -> None:
        # Pick any SKU from seed — a SPF 2x4x8 line exists everywhere.
        import sqlite3

        with sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            sku_row = c.execute(
                "SELECT sku FROM products WHERE nominal_size = '2x4' "
                "AND length_ft = 8 AND species = 'SPF' AND active = 1 "
                "LIMIT 1"
            ).fetchone()
        sku = sku_row[0]
        rows = run_named_query("stock_all_branches", [sku], catalog_db_path=catalog_db)
        assert len(rows) >= 1
        assert all("branch_id" in r and "on_hand" in r for r in rows)

    def test_unknown_named_query_rejected(self, catalog_db: Path) -> None:
        with pytest.raises(QueryBuilderError, match=r"unknown named query"):
            run_named_query("all_products", [], catalog_db_path=catalog_db)  # not registered
