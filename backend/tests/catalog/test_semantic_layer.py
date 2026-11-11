"""semantic_layer.py tests — happy path + one failing case per validation.

Each failing case copies the real bundle to a tmp dir and mutates exactly
one file, so the assertion isolates a single check. The happy-path fixture
builds a catalog.db from the SHARED `app/catalog/schema.sql` (same file
`seed.py` uses, so this file and the seed can't drift) and inserts the
SKUs that examples.yaml references, so the real bundle loads cleanly
without needing to run the full Phase 1 step 4 seeder.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from app.catalog.semantic_layer import SemanticLayerError, load_bundle

_BUNDLE_DIR = Path(__file__).resolve().parents[2] / "app" / "catalog" / "semantic"
_SCHEMA_SQL = (Path(__file__).resolve().parents[2] / "app" / "catalog" / "schema.sql").read_text(
    encoding="utf-8"
)


_EXAMPLE_SKUS = ("LUM-2X6-16-SYP-2-PTAG", "LUM-2X4-8-SPF-2", "LUM-2X10-18-SPF-2")


def _make_catalog_db(
    tmp_path: Path,
    *,
    inactive_skus: frozenset[str] = frozenset(),
    missing_skus: frozenset[str] = frozenset(),
) -> Path:
    """Build a minimal catalog.db matching SPEC §5.2 and seed the example SKUs.

    By default every example SKU is inserted as active=1. `inactive_skus`
    flips the active flag on the named entries; `missing_skus` skips
    inserting them entirely. Targeting a specific SKU (rather than wiping
    all three) lets tests prove the validator checks every example — the
    error has to name the deliberately-broken one.
    """
    unknown = (set(inactive_skus) | set(missing_skus)) - set(_EXAMPLE_SKUS)
    assert not unknown, (
        f"unknown example SKUs in fixture call: {sorted(unknown)}; known: {sorted(_EXAMPLE_SKUS)}"
    )
    db = tmp_path / "catalog.db"
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_SQL)
    for sku in _EXAMPLE_SKUS:
        if sku in missing_skus:
            continue
        active_val = 0 if sku in inactive_skus else 1
        conn.execute(
            """
            INSERT INTO products (sku, category, sale_unit, unit_price_cents,
                                  price_updated_at, active, name, description)
            VALUES (?, 'dimensional_lumber', 'EA', 100, '2026-01-01', ?, ?, ?)
            """,
            (sku, active_val, f"Test {sku}", f"Seeded by test fixture: {sku}"),
        )
    conn.commit()
    conn.close()
    return db


def _clone_bundle(tmp_path: Path, mutations: dict[str, Callable[[str], str]]) -> Path:
    broken = tmp_path / "semantic"
    broken.mkdir(exist_ok=True)
    existing = {p.name for p in _BUNDLE_DIR.glob("*.yaml")}
    unknown = set(mutations) - existing
    assert not unknown, f"unknown bundle files in mutations: {sorted(unknown)}"
    for src in _BUNDLE_DIR.glob("*.yaml"):
        text = src.read_text(encoding="utf-8")
        if src.name in mutations:
            after = mutations[src.name](text)
            assert after != text, f"mutation for {src.name} changed nothing — target text not found"
            text = after
        (broken / src.name).write_text(text, encoding="utf-8")
    return broken


@pytest.fixture
def catalog_db(tmp_path: Path) -> Path:
    return _make_catalog_db(tmp_path)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_loads_real_bundle(self, catalog_db: Path) -> None:
        bundle = load_bundle(_BUNDLE_DIR, catalog_db)
        assert set(bundle.entities) == {"products", "stock", "branches"}
        assert len(bundle.dimensions) == 3
        assert {d.name for d in bundle.dimensions} == {"length", "area", "board_measure"}
        assert "insulation" in bundle.sanity_ranges
        assert any(sr.unit == "ROLL" for sr in bundle.sanity_ranges["roofing"])
        assert "treatment_ambiguity" in bundle.playbooks
        assert len(bundle.examples) == 4
        assert "stock_all_branches" in bundle.named_queries

    def test_branches_is_agent_hidden(self, catalog_db: Path) -> None:
        bundle = load_bundle(_BUNDLE_DIR, catalog_db)
        assert bundle.entities["branches"].agent_visible is False
        assert bundle.entities["products"].agent_visible is True
        assert bundle.entities["stock"].agent_visible is True


# ---------------------------------------------------------------------------
# File-level failures
# ---------------------------------------------------------------------------


class TestFileLevelFailures:
    def test_missing_catalog_db(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.db"
        with pytest.raises(SemanticLayerError, match=r"Catalog not built — run make seed"):
            load_bundle(_BUNDLE_DIR, missing)

    def test_missing_bundle_file(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(tmp_path, {})
        (broken / "products.yaml").unlink()
        with pytest.raises(SemanticLayerError, match=r"missing bundle file: products\.yaml"):
            load_bundle(broken, catalog_db)

    def test_non_utf8_bytes(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(tmp_path, {})
        (broken / "products.yaml").write_bytes(b"\xff\x80\xff invalid utf-8")
        with pytest.raises(SemanticLayerError, match=r"products\.yaml: not valid UTF-8"):
            load_bundle(broken, catalog_db)

    def test_empty_file(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(tmp_path, {"products.yaml": lambda _s: ""})
        with pytest.raises(SemanticLayerError, match=r"products\.yaml: file is empty"):
            load_bundle(broken, catalog_db)

    def test_yaml_syntax_error(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(
            tmp_path,
            {"products.yaml": lambda s: s + "\n  : : : : ::::\n"},
        )
        with pytest.raises(SemanticLayerError, match=r"products\.yaml: YAML parse error"):
            load_bundle(broken, catalog_db)

    def test_duplicate_key(self, tmp_path: Path, catalog_db: Path) -> None:
        # PyYAML would silently keep the last of duplicate keys; the strict
        # loader catches it and names the offending key + line.
        def mutate(s: str) -> str:
            marker = "joins:\n  products_stock:"
            return s.replace(marker, "  r_value:\n    type: int\n\n" + marker, 1)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"duplicate key 'r_value' at line \d+"):
            load_bundle(broken, catalog_db)

    def test_pydantic_validation_wraps_with_filename(
        self, tmp_path: Path, catalog_db: Path
    ) -> None:
        # `type: textX` fails the Literal["text","int","bool","enum"] check.
        broken = _clone_bundle(
            tmp_path,
            {"products.yaml": lambda s: s.replace("type: text", "type: textX", 1)},
        )
        with pytest.raises(SemanticLayerError, match=r"(?s)products\.yaml.*type"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Conversions / dimensions
# ---------------------------------------------------------------------------


class TestDimensionValidation:
    def test_unknown_function(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(
            tmp_path,
            {"conversions.yaml": lambda s: s.replace("length_to_pieces", "nope_not_registered")},
        )
        with pytest.raises(SemanticLayerError, match=r"nope_not_registered"):
            load_bundle(broken, catalog_db)

    def test_uses_fields_references_missing_column(self, tmp_path: Path, catalog_db: Path) -> None:
        # Target uses_fields specifically rather than the first `length_ft`
        # occurrence anywhere in the file.
        broken = _clone_bundle(
            tmp_path,
            {
                "conversions.yaml": lambda s: s.replace(
                    "uses_fields: [length_ft]", "uses_fields: [not_a_real_field]", 1
                )
            },
        )
        with pytest.raises(SemanticLayerError, match=r"not_a_real_field"):
            load_bundle(broken, catalog_db)

    def test_rounding_floor_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(
            tmp_path,
            {"conversions.yaml": lambda s: s.replace("rounding: ceil", "rounding: floor", 1)},
        )
        with pytest.raises(SemanticLayerError, match=r"rounding='floor'"):
            load_bundle(broken, catalog_db)

    def test_base_unit_not_in_Unit_enum(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(
            tmp_path,
            {"conversions.yaml": lambda s: s.replace("base_unit: LF", "base_unit: WIBBLE", 1)},
        )
        with pytest.raises(SemanticLayerError, match=r"WIBBLE"):
            load_bundle(broken, catalog_db)

    def test_units_key_not_in_Unit_enum(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace("    units:\n      LF: 1", "    units:\n      WIBBLE: 1", 1)

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"WIBBLE"):
            load_bundle(broken, catalog_db)

    def test_sale_unit_not_in_products_sale_unit(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace(
                "SHT:  {function: area_to_coverage_units, uses_fields: [coverage_sqft]}",
                "FOO:  {function: area_to_coverage_units, uses_fields: [coverage_sqft]}",
                1,
            )

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"FOO"):
            load_bundle(broken, catalog_db)

    def test_strict_mode_rejects_bool_as_int(self, tmp_path: Path, catalog_db: Path) -> None:
        # With strict=True on _DimensionInput, YAML `true` is a bool and is
        # NOT coerced to 1 for the `dict[str, PositiveInt]` units map.
        def mutate(s: str) -> str:
            return s.replace("SQ: 100", "SQ: true", 1)

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        # (?s) enables DOTALL so .* matches the newlines in Pydantic's
        # multi-line validation message.
        with pytest.raises(SemanticLayerError, match=r"(?s)conversions\.yaml.*SQ.*int"):
            load_bundle(broken, catalog_db)

    def test_units_factor_zero_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # PositiveInt on the units dict value: a zero factor is nonsense.
        def mutate(s: str) -> str:
            return s.replace("SQ: 100", "SQ: 0", 1)

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"(?s)SQ.*greater than 0"):
            load_bundle(broken, catalog_db)

    def test_empty_uses_fields_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # min_length=1 on _SaleUnitInput.uses_fields catches a conversion
        # function that would be called with no product-row context.
        def mutate(s: str) -> str:
            return s.replace("uses_fields: [length_ft]", "uses_fields: []", 1)

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"uses_fields"):
            load_bundle(broken, catalog_db)

    def test_base_unit_not_in_units_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # length declares base_unit: LF but we drop LF from the units map.
        def mutate(s: str) -> str:
            return s.replace(
                "    base_unit: LF\n    units:\n      LF: 1",
                "    base_unit: LF\n    units:\n      SF: 1",
                1,
            )

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"base_unit 'LF' is not declared"):
            load_bundle(broken, catalog_db)

    def test_base_unit_factor_not_1_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # base_unit is in the units map but with a non-1 factor — invariant
        # of a base unit is that it IS the unit of measure (factor 1).
        def mutate(s: str) -> str:
            return s.replace(
                "    base_unit: LF\n    units:\n      LF: 1",
                "    base_unit: LF\n    units:\n      LF: 2",
                1,
            )

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"base_unit 'LF' must have factor 1"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Synonyms
# ---------------------------------------------------------------------------


class TestSynonymValidation:
    def test_synonym_target_not_in_enum(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(
            tmp_path,
            {
                "products.yaml": lambda s: s.replace(
                    '"fire rated": type_x', '"fire rated": not_a_value', 1
                )
            },
        )
        with pytest.raises(SemanticLayerError, match=r"not_a_value"):
            load_bundle(broken, catalog_db)

    def test_synonym_key_not_canonical(self, tmp_path: Path, catalog_db: Path) -> None:
        broken = _clone_bundle(
            tmp_path,
            {"products.yaml": lambda s: s.replace('"type x": type_x', '"Type X": type_x', 1)},
        )
        with pytest.raises(SemanticLayerError, match=r"not canonical"):
            load_bundle(broken, catalog_db)

    def test_synonym_on_unconstrained_text_field_rejected(
        self, tmp_path: Path, catalog_db: Path
    ) -> None:
        # products.yaml declares `name:` as a text field with no enumerated
        # values. Attaching synonyms is meaningless (no canonical value to
        # map to), so the loader must reject.
        def mutate(s: str) -> str:
            return s.replace(
                "  name:\n    type: text\n    searchable: true\n"
                '    description: "Short product name used for display."',
                "  name:\n    type: text\n    searchable: true\n"
                '    description: "Short product name used for display."\n'
                '    synonyms:\n      "product name": something',
                1,
            )

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"synonyms are only meaningful"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Sanity ranges
# ---------------------------------------------------------------------------


class TestSanityRangeValidation:
    def test_unit_not_in_sale_unit_enum(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace("{unit: SHT, max_qty: 2000}", "{unit: FOO, max_qty: 2000}", 1)

        broken = _clone_bundle(tmp_path, {"sanity_ranges.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"FOO"):
            load_bundle(broken, catalog_db)

    def test_category_not_in_products_category_enum(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace("  drywall:\n", "  invalid_cat:\n", 1)

        broken = _clone_bundle(tmp_path, {"sanity_ranges.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"invalid_cat"):
            load_bundle(broken, catalog_db)

    def test_duplicate_unit_within_category(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace(
                "    - {unit: BAG,  max_qty: 500}\n    - {unit: ROLL, max_qty: 200}\n",
                "    - {unit: BAG,  max_qty: 500}\n    - {unit: BAG,  max_qty: 400}\n",
                1,
            )

        broken = _clone_bundle(tmp_path, {"sanity_ranges.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"duplicate"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Column drift
# ---------------------------------------------------------------------------


class TestColumnDrift:
    def test_extra_field_in_yaml_fails(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            data = yaml.safe_load(s)
            data["fields"]["made_up_column"] = {"type": "int", "filterable": True}
            return yaml.safe_dump(data, sort_keys=False)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"made_up_column"):
            load_bundle(broken, catalog_db)

    def test_missing_field_in_yaml_fails(self, tmp_path: Path, catalog_db: Path) -> None:
        # Structural removal: parse YAML, drop r_value, dump.
        def mutate(s: str) -> str:
            data = yaml.safe_load(s)
            del data["fields"]["r_value"]
            return yaml.safe_dump(data, sort_keys=False)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"r_value"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Joins
# ---------------------------------------------------------------------------


class TestJoinValidation:
    def test_join_to_unknown_entity(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace(
                "products_stock: {from: sku, to: stock.sku, type: left}",
                "products_stock: {from: sku, to: ghost.sku, type: left}",
                1,
            )

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"ghost"):
            load_bundle(broken, catalog_db)

    def test_join_to_unknown_field(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace(
                "products_stock: {from: sku, to: stock.sku, type: left}",
                "products_stock: {from: sku, to: stock.not_a_col, type: left}",
                1,
            )

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"not_a_col"):
            load_bundle(broken, catalog_db)

    def test_join_from_unknown_field(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace(
                "products_stock: {from: sku, to: stock.sku, type: left}",
                "products_stock: {from: nope, to: stock.sku, type: left}",
                1,
            )

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"'from' field 'nope'"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


class TestExampleValidation:
    def test_filter_key_not_filterable(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace('        nominal_size: "2x6"', '        name: "junk"', 1)

        broken = _clone_bundle(tmp_path, {"examples.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"filter key 'name'"):
            load_bundle(broken, catalog_db)

    def test_filter_value_not_in_allowed_values(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace('nominal_size: "2x6"', 'nominal_size: "9x9"', 1)

        broken = _clone_bundle(tmp_path, {"examples.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"9x9"):
            load_bundle(broken, catalog_db)

    def test_assumption_field_not_matchable(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace("- field: treatment", "- field: sale_unit", 1)

        broken = _clone_bundle(tmp_path, {"examples.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"assumption field 'sale_unit'"):
            load_bundle(broken, catalog_db)

    def test_assumption_value_not_in_allowed_values(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace(
                "value: pressure_treated_above_ground",
                "value: not_a_treatment_value",
                1,
            )

        broken = _clone_bundle(tmp_path, {"examples.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"not_a_treatment_value"):
            load_bundle(broken, catalog_db)

    def test_missing_sku_rejected(self, tmp_path: Path) -> None:
        # Only the THIRD example SKU is absent — the error must name it AND
        # say "not found", proving (a) the validator iterates every example
        # and (b) missing vs inactive produce different error messages.
        db = _make_catalog_db(tmp_path, missing_skus=frozenset({"LUM-2X10-18-SPF-2"}))
        with pytest.raises(SemanticLayerError, match=r"LUM-2X10-18-SPF-2.*not found"):
            load_bundle(_BUNDLE_DIR, db)

    def test_inactive_sku_rejected(self, tmp_path: Path) -> None:
        # Same shape, inactive variant: the error must name the SKU AND say
        # "inactive". Confirms "exists + active" with a distinct message.
        db = _make_catalog_db(tmp_path, inactive_skus=frozenset({"LUM-2X10-18-SPF-2"}))
        with pytest.raises(SemanticLayerError, match=r"LUM-2X10-18-SPF-2.*inactive"):
            load_bundle(_BUNDLE_DIR, db)


# ---------------------------------------------------------------------------
# Entity key / table-not-found
# ---------------------------------------------------------------------------


class TestEntityShape:
    def test_key_field_not_declared_in_fields(self, tmp_path: Path, catalog_db: Path) -> None:
        # Replace the products.yaml key with a bogus list that doesn't
        # reference any declared field.
        def mutate(s: str) -> str:
            return s.replace("key: sku", "key: [nope_not_a_field]", 1)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"nope_not_a_field"):
            load_bundle(broken, catalog_db)

    def test_table_not_found_in_catalog_db(self, tmp_path: Path, catalog_db: Path) -> None:
        # Point products.yaml at a table that doesn't exist in the DB.
        def mutate(s: str) -> str:
            return s.replace("table: products", "table: ghost_table", 1)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(
            SemanticLayerError, match=r"table 'ghost_table' not found in catalog\.db"
        ):
            load_bundle(broken, catalog_db)

    def test_empty_key_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # Pydantic Field(min_length=1) on Entity.key catches `key: []`.
        def mutate(s: str) -> str:
            return s.replace("key: sku", "key: []", 1)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"(?s)products\.yaml.*key"):
            load_bundle(broken, catalog_db)

    def test_empty_description_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # Entity.description must be non-empty (min_length=1).
        def mutate(s: str) -> str:
            return s.replace(
                "description: Sellable catalog items with price per sale unit.",
                'description: ""',
                1,
            )

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"(?s)products\.yaml.*description"):
            load_bundle(broken, catalog_db)

    def test_key_as_string_with_unknown_field(self, tmp_path: Path, catalog_db: Path) -> None:
        # Legacy `key: <single string>` shorthand is coerced to [single]
        # by the loader; the validator still catches the undeclared field
        # and names it.
        def mutate(s: str) -> str:
            return s.replace("key: sku", "key: not_a_field", 1)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"not_a_field"):
            load_bundle(broken, catalog_db)

    def test_table_typo_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # Classic typo: singular `product` vs plural `products`. The
        # column-match stage must name the offending table verbatim.
        def mutate(s: str) -> str:
            return s.replace("table: products", "table: product", 1)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"table 'product' not found in catalog\.db"):
            load_bundle(broken, catalog_db)

    def test_field_description_empty_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # FieldDef.description is optional, but when present it must be
        # non-empty (min_length=1).
        def mutate(s: str) -> str:
            return s.replace(
                '"'
                "Product category. Determines which conversion dimension and sanity "
                "range applies."
                '"',
                '""',
                1,
            )

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"(?s)products\.yaml.*description"):
            load_bundle(broken, catalog_db)

    def test_table_identifier_bad_chars_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # Safe-identifier regex rejects quotes / semicolons / spaces, so
        # a malicious YAML can't inject SQL into query_builder.py (Phase 4).
        def mutate(s: str) -> str:
            return s.replace("table: products", 'table: "products; DROP TABLE x"', 1)

        broken = _clone_bundle(tmp_path, {"products.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"(?s)products\.yaml.*table"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Section-type isinstance checks on top-level YAML
# ---------------------------------------------------------------------------


class TestSectionTypes:
    def test_dimensions_as_list_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(_s: str) -> str:
            # Replace the whole file with `dimensions: []` so the top-level
            # `dimensions` key exists but is the wrong type.
            return "dimensions:\n  - length\n  - area\n"

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"'dimensions' must be a mapping"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# Sale-unit uniqueness across dimensions
# ---------------------------------------------------------------------------


class TestSaleUnitUniqueness:
    def test_ea_in_two_dimensions_is_allowed(self, tmp_path: Path, catalog_db: Path) -> None:
        # EA is the one sale_unit the loader exempts — it legitimately
        # appears in BOTH length (lineal-feet input) AND board_measure
        # (board-feet input) because the same 2x4x8 can be ordered either
        # way. The shipped bundle already has EA in both; this test is a
        # positive assertion that the exemption is wired.
        bundle = load_bundle(_BUNDLE_DIR, catalog_db)
        dims_with_ea = {dim.name for dim in bundle.dimensions if "EA" in dim.sale_units}
        assert dims_with_ea == {"length", "board_measure"}

    def test_sht_in_both_dimensions_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # Add SHT to the length dimension so it appears in BOTH length
        # AND area (where it legitimately lives). Validator must reject.
        def mutate(s: str) -> str:
            return s.replace(
                "    sale_units:\n      EA:\n        function: length_to_pieces\n"
                "        uses_fields: [length_ft]",
                "    sale_units:\n      EA:\n        function: length_to_pieces\n"
                "        uses_fields: [length_ft]\n"
                "      SHT:\n        function: length_to_pieces\n"
                "        uses_fields: [length_ft]",
                1,
            )

        broken = _clone_bundle(tmp_path, {"conversions.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"sale_unit 'SHT' appears in both"):
            load_bundle(broken, catalog_db)


# ---------------------------------------------------------------------------
# named_queries: sql_ref + params signature
# ---------------------------------------------------------------------------


class TestNamedQueries:
    def test_unknown_sql_ref_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        def mutate(s: str) -> str:
            return s.replace("sql_ref: stock_all_branches", "sql_ref: ghost_query", 1)

        broken = _clone_bundle(tmp_path, {"named_queries.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"ghost_query"):
            load_bundle(broken, catalog_db)

    def test_param_mismatch_rejected(self, tmp_path: Path, catalog_db: Path) -> None:
        # stock_all_branches signature is ["sku"]; swap to ["branch_id"].
        def mutate(s: str) -> str:
            return s.replace("params: [sku]", "params: [branch_id]", 1)

        broken = _clone_bundle(tmp_path, {"named_queries.yaml": mutate})
        with pytest.raises(SemanticLayerError, match=r"params.*don't match"):
            load_bundle(broken, catalog_db)
