"""Load and validate the OKF-style semantic bundle at startup.

The bundle lives under `app/catalog/semantic/` and is the single source of
truth for entity fields, unit conversions, synonyms, playbook strings,
few-shot examples, sanity ranges, and named queries (SPEC §8.1).

`load_bundle(bundle_dir, catalog_db)` reads every YAML, validates every
cross-reference, and returns a frozen `SemanticBundle`. Any failure raises
`SemanticLayerError` with a message that names the offending file,
entity/dimension/category, and field so a developer can fix it in one
read. The only tolerated failure mode is a missing catalog.db — handled
with the SPEC-mandated "Catalog not built — run make seed" message.

Pydantic models are configured with strict=True so YAML's native types pass
through unchanged (no `"100"` → 100 coercion, no `true` → 1).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, ValidationError, model_validator

from app.catalog.units import UNIT_CONVERSIONS, Dimension, SaleUnitSpec, Unit, canonicalize


class SemanticLayerError(RuntimeError):
    """Raised when the semantic bundle fails any startup validation."""


class _DuplicateKey(Exception):
    """Internal — carried out of the YAML loader, re-raised as SemanticLayerError."""

    def __init__(self, key: Any, line: int) -> None:
        super().__init__(f"duplicate key '{key}' at line {line}")
        self.key = key
        self.line = line


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    PyYAML's default silently keeps the last of duplicate keys, which hides
    typos like declaring `r_value` twice under `fields:`. Overriding
    `construct_mapping` makes that a hard error.
    """

    def construct_mapping(self, node: yaml.Node, deep: bool = False) -> dict[Any, Any]:
        if not isinstance(node, yaml.MappingNode):
            raise yaml.constructor.ConstructorError(
                None, None, f"expected a mapping node, got {node.id}", node.start_mark
            )
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise _DuplicateKey(key, key_node.start_mark.line + 1)
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


# ---------------------------------------------------------------------------
# Pydantic data models (strict=True everywhere — YAML already provides native
# types; disabling coercion catches surprises like `SQ: true` meant as int).
# ---------------------------------------------------------------------------


class FieldDef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["text", "int", "bool", "enum"]
    values: list[Any] | None = None
    filterable: bool = False
    matchable: bool = False
    returnable: bool = False
    searchable: bool = False
    description: str | None = Field(default=None, min_length=1)
    synonyms: dict[str, Any] = Field(default_factory=dict)


class JoinDef(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    type: Literal["left", "inner"]


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    table: str
    key: list[str] = Field(min_length=1)
    agent_visible: bool = True
    description: str = Field(min_length=1)
    fields: dict[str, FieldDef] = Field(min_length=1)
    joins: dict[str, JoinDef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _key_fields_must_be_declared(self) -> Entity:
        missing = [k for k in self.key if k not in self.fields]
        if missing:
            raise ValueError(
                f"key references undeclared field(s): {missing}; "
                f"declared fields: {sorted(self.fields)}"
            )
        return self


class _SaleUnitInput(BaseModel):
    """Intermediate form used by the dimensions loader.

    The loader converts this into the frozen `SaleUnitSpec` dataclass that
    `units.py` defines. Wrapping the YAML entry in Pydantic first lets us
    enforce min_length=1 on `uses_fields` and extra=forbid on the entry
    shape at parse time.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    function: str
    uses_fields: list[str] = Field(min_length=1)


class _DimensionInput(BaseModel):
    """Intermediate form used by the dimensions loader.

    Pydantic enforces non-empty units + sale_units. The loader keeps the
    explicit `rounding == "ceil"` check outside Pydantic so the error
    message names the offending value instead of a generic Literal failure.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    base_unit: str
    units: dict[str, PositiveInt] = Field(min_length=1)
    sale_units: dict[str, _SaleUnitInput] = Field(min_length=1)
    rounding: str


class SanityRange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    unit: str
    max_qty: PositiveInt


class NamedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    params: list[str]
    sql_ref: str
    description: str | None = None


class ExampleQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    entity: Literal["products"]
    filters: dict[str, Any] = Field(default_factory=dict)
    text_search: str | None = None


class ExampleAssumption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    field: str
    value: Any
    reason: str


class ExampleMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sku: str | None
    is_substitute: bool = False
    substitute_reason: str | None = None
    assumptions: list[ExampleAssumption] = Field(default_factory=list)
    searched: str


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request: str
    query: ExampleQuery
    match: ExampleMatch


@dataclass(frozen=True)
class SemanticBundle:
    entities: dict[str, Entity]
    dimensions: list[Dimension]
    sanity_ranges: dict[str, list[SanityRange]]
    playbooks: dict[str, str]
    examples: list[Example]
    named_queries: dict[str, NamedQuery]


# ---------------------------------------------------------------------------
# Per-file loaders (raw YAML → typed objects; Pydantic validation errors are
# wrapped so the YAML filename shows up in the message)
# ---------------------------------------------------------------------------

_ENTITY_FILES: tuple[str, ...] = ("products", "stock", "branches")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SemanticLayerError(f"missing bundle file: {path.name}") from exc
    except UnicodeDecodeError as exc:
        raise SemanticLayerError(f"{path.name}: not valid UTF-8: {exc}") from exc
    except OSError as exc:
        raise SemanticLayerError(f"{path.name}: could not read file: {exc}") from exc
    try:
        data = yaml.load(text, Loader=_StrictSafeLoader)
    except _DuplicateKey as exc:
        raise SemanticLayerError(
            f"{path.name}: duplicate key '{exc.key}' at line {exc.line}"
        ) from exc
    except yaml.YAMLError as exc:
        raise SemanticLayerError(f"{path.name}: YAML parse error: {exc}") from exc
    if data is None:
        raise SemanticLayerError(f"{path.name}: file is empty")
    if not isinstance(data, dict):
        raise SemanticLayerError(f"{path.name}: top level must be a mapping")
    return data


def _validate_or_raise(model: type[BaseModel], data: Any, where: str) -> Any:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise SemanticLayerError(f"{where}: {exc}") from exc


def _load_entity(path: Path) -> Entity:
    # Accept legacy "key: sku" shorthand and coerce to list form so entity
    # YAMLs can keep their single-key simplicity without breaking the
    # list[str] contract.
    raw = _read_yaml(path)
    if isinstance(raw.get("key"), str):
        raw["key"] = [raw["key"]]
    return _validate_or_raise(Entity, raw, path.name)


def _load_dimensions(path: Path) -> list[Dimension]:
    raw = _read_yaml(path)
    if "dimensions" not in raw:
        raise SemanticLayerError(f"{path.name}: missing top-level 'dimensions' key")
    out: list[Dimension] = []
    for name, spec in raw["dimensions"].items():
        where = f"{path.name}: dimension {name!r}"
        # Rounding check runs before Pydantic so the error message names
        # the offending value verbatim ("rounding='floor'" vs the generic
        # Literal diagnostic).
        if not isinstance(spec, dict) or spec.get("rounding") != "ceil":
            got = spec.get("rounding") if isinstance(spec, dict) else None
            raise SemanticLayerError(
                f"{where} has rounding={got!r}; only 'ceil' is supported "
                "(units.py always ceils the final quotient)"
            )
        validated = _validate_or_raise(_DimensionInput, spec, where)
        if validated.base_unit not in validated.units:
            raise SemanticLayerError(
                f"{where}: base_unit {validated.base_unit!r} is not declared in units "
                f"({sorted(validated.units)})"
            )
        if validated.units[validated.base_unit] != 1:
            raise SemanticLayerError(
                f"{where}: base_unit {validated.base_unit!r} must have factor 1 in units, "
                f"got {validated.units[validated.base_unit]}"
            )
        sale_units: dict[str, SaleUnitSpec] = {
            su_name: SaleUnitSpec(function=su.function, uses_fields=tuple(su.uses_fields))
            for su_name, su in validated.sale_units.items()
        }
        out.append(
            Dimension(
                name=name,
                base_unit=validated.base_unit,
                units=dict(validated.units),
                sale_units=sale_units,
            )
        )
    return out


def _load_sanity_ranges(path: Path) -> dict[str, list[SanityRange]]:
    raw = _read_yaml(path)
    if "sanity_ranges" not in raw:
        raise SemanticLayerError(f"{path.name}: missing top-level 'sanity_ranges' key")
    out: dict[str, list[SanityRange]] = {}
    for category, entries in raw["sanity_ranges"].items():
        if not isinstance(entries, list):
            raise SemanticLayerError(
                f"{path.name}: category {category!r} must map to a list of "
                "{unit, max_qty} entries"
            )
        if not entries:
            raise SemanticLayerError(
                f"{path.name}: category {category!r} must have at least one entry"
            )
        out[category] = [
            _validate_or_raise(SanityRange, e, f"{path.name}: {category}") for e in entries
        ]
    return out


def _load_playbooks(path: Path) -> dict[str, str]:
    raw = _read_yaml(path)
    if "playbooks" not in raw:
        raise SemanticLayerError(f"{path.name}: missing top-level 'playbooks' key")
    out: dict[str, str] = {}
    for name, body in raw["playbooks"].items():
        if not isinstance(body, str):
            raise SemanticLayerError(f"{path.name}: playbook {name!r} must be a string")
        out[name] = body.strip()
    return out


def _load_examples(path: Path) -> list[Example]:
    raw = _read_yaml(path)
    if "examples" not in raw:
        raise SemanticLayerError(f"{path.name}: missing top-level 'examples' key")
    return [
        _validate_or_raise(Example, e, f"{path.name}[{i}]") for i, e in enumerate(raw["examples"])
    ]


def _load_named_queries(path: Path) -> dict[str, NamedQuery]:
    raw = _read_yaml(path)
    if "named_queries" not in raw:
        raise SemanticLayerError(f"{path.name}: missing top-level 'named_queries' key")
    return {
        name: _validate_or_raise(NamedQuery, spec, f"{path.name}: {name}")
        for name, spec in raw["named_queries"].items()
    }


# ---------------------------------------------------------------------------
# Cross-reference validation
# ---------------------------------------------------------------------------


def _allowed_values(field: FieldDef) -> set[Any]:
    """Values a synonym target or filter value may legally take."""
    if field.type == "bool":
        return {True, False}
    if field.type in ("enum", "int"):
        return set(field.values or [])
    return set()


_UNIT_ENUM_VALUES: set[str] = {u.value for u in Unit}


def _validate_dimensions(dimensions: list[Dimension], products: Entity) -> None:
    product_fields = set(products.fields.keys())
    sale_unit_field = products.fields.get("sale_unit")
    if sale_unit_field is None or not sale_unit_field.values:
        raise SemanticLayerError(
            "products.yaml: sale_unit must be an enum field with values; "
            "conversions.yaml depends on it"
        )
    product_sale_units = set(sale_unit_field.values)
    for dim in dimensions:
        if dim.base_unit not in _UNIT_ENUM_VALUES:
            raise SemanticLayerError(
                f"conversions.yaml: dimension {dim.name!r} base_unit {dim.base_unit!r} "
                f"is not a Unit enum value"
            )
        for u in dim.units:
            if u not in _UNIT_ENUM_VALUES:
                raise SemanticLayerError(
                    f"conversions.yaml: dimension {dim.name!r} units key {u!r} "
                    f"is not a Unit enum value"
                )
        for sale_unit_name, spec in dim.sale_units.items():
            if sale_unit_name not in product_sale_units:
                raise SemanticLayerError(
                    f"conversions.yaml: dimension {dim.name!r} sale_unit "
                    f"{sale_unit_name!r} is not in products.sale_unit values "
                    f"{sorted(product_sale_units)}"
                )
            if spec.function not in UNIT_CONVERSIONS:
                raise SemanticLayerError(
                    f"conversions.yaml: dimension {dim.name!r} sale_unit "
                    f"{sale_unit_name!r}: function {spec.function!r} is not registered "
                    f"in units.UNIT_CONVERSIONS (registered: {sorted(UNIT_CONVERSIONS)})"
                )
            for f in spec.uses_fields:
                if f not in product_fields:
                    raise SemanticLayerError(
                        f"conversions.yaml: dimension {dim.name!r} sale_unit "
                        f"{sale_unit_name!r}: uses_fields references {f!r} which is "
                        f"not a declared field on products"
                    )


def _validate_synonyms(entities: dict[str, Entity]) -> None:
    for entity_name, entity in entities.items():
        for field_name, field in entity.fields.items():
            if not field.synonyms:
                continue
            allowed = _allowed_values(field)
            if not allowed:
                raise SemanticLayerError(
                    f"{entity_name}.yaml: field {field_name!r} has type {field.type!r} "
                    "and no enumerated values but declares synonyms; "
                    "synonyms are only meaningful for enum/bool/values-restricted int fields"
                )
            for syn_key, syn_target in field.synonyms.items():
                canon = canonicalize(syn_key)
                if syn_key != canon:
                    raise SemanticLayerError(
                        f"{entity_name}.yaml: field {field_name!r}: synonym key "
                        f"{syn_key!r} is not canonical (canonicalize() returned "
                        f"{canon!r}); store keys in canonical form only"
                    )
                if syn_target not in allowed:
                    raise SemanticLayerError(
                        f"{entity_name}.yaml: field {field_name!r}: synonym "
                        f"{syn_key!r} maps to {syn_target!r} which is not an allowed "
                        f"value {sorted(allowed, key=str)}"
                    )


def _validate_sanity_ranges(sanity_ranges: dict[str, list[SanityRange]], products: Entity) -> None:
    sale_unit_field = products.fields.get("sale_unit")
    category_field = products.fields.get("category")
    if sale_unit_field is None or sale_unit_field.type != "enum" or not sale_unit_field.values:
        raise SemanticLayerError(
            "products.yaml: sale_unit must be an enum field with values; "
            "sanity_ranges.yaml depends on it"
        )
    if category_field is None or category_field.type != "enum" or not category_field.values:
        raise SemanticLayerError(
            "products.yaml: category must be an enum field with values; "
            "sanity_ranges.yaml depends on it"
        )
    allowed_units = set(sale_unit_field.values)
    allowed_categories = set(category_field.values)
    for category, entries in sanity_ranges.items():
        if category not in allowed_categories:
            raise SemanticLayerError(
                f"sanity_ranges.yaml: category {category!r} is not in "
                f"products.category values {sorted(allowed_categories)}"
            )
        seen: set[str] = set()
        for entry in entries:
            if entry.unit not in allowed_units:
                raise SemanticLayerError(
                    f"sanity_ranges.yaml: category {category!r}: unit {entry.unit!r} "
                    f"is not in the sale_unit enum {sorted(allowed_units)}"
                )
            if entry.unit in seen:
                raise SemanticLayerError(
                    f"sanity_ranges.yaml: category {category!r}: duplicate entry for "
                    f"unit {entry.unit!r}"
                )
            seen.add(entry.unit)


def _validate_joins(entities: dict[str, Entity]) -> None:
    for entity_name, entity in entities.items():
        for join_name, join in entity.joins.items():
            if join.from_ not in entity.fields:
                raise SemanticLayerError(
                    f"{entity_name}.yaml: join {join_name!r}: 'from' field "
                    f"{join.from_!r} is not declared on {entity_name}"
                )
            if "." not in join.to:
                raise SemanticLayerError(
                    f"{entity_name}.yaml: join {join_name!r}: 'to' must be "
                    f"'entity.field' (got {join.to!r})"
                )
            target_entity, target_field = join.to.split(".", 1)
            if target_entity not in entities:
                raise SemanticLayerError(
                    f"{entity_name}.yaml: join {join_name!r}: 'to' references "
                    f"unknown entity {target_entity!r}"
                )
            if target_field not in entities[target_entity].fields:
                raise SemanticLayerError(
                    f"{entity_name}.yaml: join {join_name!r}: 'to' references "
                    f"field {target_field!r} which is not declared on {target_entity}"
                )


def _db_columns(catalog_db: Path, table: str) -> set[str]:
    conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return {row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def _validate_columns_match_db(entities: dict[str, Entity], catalog_db: Path) -> None:
    for entity_name, entity in entities.items():
        actual = _db_columns(catalog_db, entity.table)
        if not actual:
            raise SemanticLayerError(
                f"table '{entity.table}' not found in catalog.db (referenced by {entity_name}.yaml)"
            )
        declared = set(entity.fields.keys())
        if declared == actual:
            continue
        undeclared = sorted(actual - declared)
        missing = sorted(declared - actual)
        lines = [
            f"{entity_name}.yaml fields do not match catalog.db columns for table {entity.table!r}:"
        ]
        if undeclared:
            lines.append(f"  columns in DB not declared in YAML: {undeclared}")
        if missing:
            lines.append(f"  fields in YAML not present in DB: {missing}")
        raise SemanticLayerError("\n".join(lines))


def _all_and_active_skus(catalog_db: Path) -> tuple[set[str], set[str]]:
    """Return (all_skus, active_skus) from one connection."""
    conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    try:
        cur = conn.execute("SELECT sku, active FROM products")
        rows = cur.fetchall()
    finally:
        conn.close()
    all_skus = {row[0] for row in rows}
    active_skus = {row[0] for row in rows if row[1] == 1}
    return all_skus, active_skus


def _validate_examples(examples: list[Example], products: Entity, catalog_db: Path) -> None:
    filterable_fields = {n: f for n, f in products.fields.items() if f.filterable}
    matchable_fields = {n: f for n, f in products.fields.items() if f.matchable}
    all_skus, active_skus = _all_and_active_skus(catalog_db)
    for i, ex in enumerate(examples):
        where = f"examples.yaml[{i}] ({ex.request!r})"
        for key, value in ex.query.filters.items():
            if key not in filterable_fields:
                raise SemanticLayerError(
                    f"{where}: filter key {key!r} is not a filterable products field "
                    f"(filterable: {sorted(filterable_fields)})"
                )
            allowed = _allowed_values(filterable_fields[key])
            if allowed and value not in allowed:
                raise SemanticLayerError(
                    f"{where}: filter {key}={value!r} is not in allowed values "
                    f"{sorted(allowed, key=str)}"
                )
        for a in ex.match.assumptions:
            if a.field not in matchable_fields:
                raise SemanticLayerError(
                    f"{where}: assumption field {a.field!r} is not a matchable "
                    f"products field (matchable: {sorted(matchable_fields)})"
                )
            allowed = _allowed_values(matchable_fields[a.field])
            if allowed and a.value not in allowed:
                raise SemanticLayerError(
                    f"{where}: assumption {a.field}={a.value!r} is not in allowed "
                    f"values {sorted(allowed, key=str)}"
                )
        if ex.match.sku is not None:
            if ex.match.sku not in all_skus:
                raise SemanticLayerError(f"{where}: SKU {ex.match.sku!r} not found in catalog")
            if ex.match.sku not in active_skus:
                raise SemanticLayerError(f"{where}: SKU {ex.match.sku!r} is inactive (active = 0)")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def load_bundle(bundle_dir: Path, catalog_db: Path) -> SemanticBundle:
    """Load the full semantic bundle and run every validation.

    Raises SemanticLayerError on any failure. The catalog-missing case
    produces the SPEC-mandated "Catalog not built — run make seed" message.
    """
    if not catalog_db.exists():
        raise SemanticLayerError("Catalog not built — run make seed")

    entities: dict[str, Entity] = {}
    for name in _ENTITY_FILES:
        entities[name] = _load_entity(bundle_dir / f"{name}.yaml")

    dimensions = _load_dimensions(bundle_dir / "conversions.yaml")
    sanity_ranges = _load_sanity_ranges(bundle_dir / "sanity_ranges.yaml")
    playbooks = _load_playbooks(bundle_dir / "playbooks.yaml")
    examples = _load_examples(bundle_dir / "examples.yaml")
    named_queries = _load_named_queries(bundle_dir / "named_queries.yaml")

    products = entities["products"]
    _validate_dimensions(dimensions, products)
    _validate_synonyms(entities)
    _validate_sanity_ranges(sanity_ranges, products)
    _validate_joins(entities)
    _validate_columns_match_db(entities, catalog_db)
    _validate_examples(examples, products, catalog_db)

    return SemanticBundle(
        entities=entities,
        dimensions=dimensions,
        sanity_ranges=sanity_ranges,
        playbooks=playbooks,
        examples=examples,
        named_queries=named_queries,
    )
