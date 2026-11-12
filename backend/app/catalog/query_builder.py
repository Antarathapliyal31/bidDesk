"""Safe catalog query builder (SPEC §8.2).

The pricing agent can call `query_catalog(filters, text_search,
limit)` through the tool surface. This module turns that typed
request into parameterized SQL — identifiers come from the
semantic bundle allowlist, values are always bound parameters.
The model NEVER writes SQL.

Guardrails:
  - Only `entity="products"` today. Other entities would need
    their own allowlist pass.
  - Keys in `filters` must be declared as `filterable: true` in
    `products.yaml`.
  - Enum values are checked against the YAML enum.
  - `active = 1` is appended unconditionally; the model can never
    see inactive SKUs.
  - `stock` is **always** LEFT-JOINed for the run's branch_id.
    The agent cannot see any other branch's stock (SPEC §8.1 —
    stock.branch_id is not filterable; transfers go through the
    code-only `stock_all_branches` named query instead).
  - `limit` capped at 20 (SPEC §8.2).
  - `text_search` runs LIKE against fields marked `searchable:
    true`; the LIKE pattern is bound, not interpolated.

Named queries (SPEC §8.2): `stock_all_branches` is code-only.
`run_named_query(name, params, catalog_db)` is the only path to
these; the agent tool surface does NOT expose them.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.catalog.semantic_layer import Entity, SemanticBundle


class QueryBuilderError(Exception):
    """Raised when the request would violate the allowlist or the
    enum constraints. The message is suitable for feeding back to
    the pricing agent as a tool_result error."""


class CatalogQuery(BaseModel):
    """Pricing agent's input to `query_catalog`. Pinned narrow so
    the SQL builder has nothing to interpret (SPEC §8.2)."""

    entity: Literal["products"]
    filters: dict[str, str | int | bool] = Field(default_factory=dict)
    text_search: str | None = Field(default=None, max_length=60)
    limit: int = Field(default=20, ge=1, le=20)


# ---------------------------------------------------------------------------
# Filter validation
# ---------------------------------------------------------------------------


def _check_type(key: str, field_type: str, value: Any) -> None:
    """Reject a filter value whose runtime type doesn't match the
    semantic-layer declared type. The nuance:
      - `bool` fields accept ONLY `bool` (int values like 0/1 are
        rejected; the YAML type is the contract).
      - `int` fields accept `int` but NOT `bool` — `isinstance(True,
        int)` is True in Python, so the usual check is a trap.
      - `text` / `enum` fields accept `str` only. Enum membership
        is checked separately via `field.values`.
    """

    if field_type == "bool":
        if not isinstance(value, bool):
            raise QueryBuilderError(
                f"filter {key!r} expects bool (declared type='bool'), "
                f"got {type(value).__name__}={value!r}"
            )
        return
    if field_type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise QueryBuilderError(
                f"filter {key!r} expects int (declared type='int'), "
                f"got {type(value).__name__}={value!r}"
            )
        return
    if field_type in ("text", "enum"):
        if not isinstance(value, str):
            raise QueryBuilderError(
                f"filter {key!r} expects str (declared type={field_type!r}), "
                f"got {type(value).__name__}={value!r}"
            )
        return
    # Unknown field type — defensive, should never happen because
    # FieldDef pins type to a Literal.
    raise QueryBuilderError(
        f"filter {key!r} declared unknown type {field_type!r}"
    )  # pragma: no cover


def _validate_filters(entity: Entity, entity_name: str, filters: dict[str, Any]) -> None:
    for key, value in filters.items():
        field = entity.fields.get(key)
        if field is None:
            raise QueryBuilderError(f"unknown field {key!r} on entity {entity_name!r}")
        if not field.filterable:
            raise QueryBuilderError(
                f"field {key!r} is not filterable (not declared filterable=true)"
            )
        _check_type(key, field.type, value)
        # Enum value check — only enforced when the field declares values.
        if field.values and value not in field.values:
            raise QueryBuilderError(
                f"value {value!r} is not an allowed value for {key!r}; "
                f"allowed: {sorted(field.values, key=str)[:8]}"
            )


def _searchable_fields(entity: Entity) -> list[str]:
    return [name for name, f in entity.fields.items() if f.searchable]


def _returnable_fields(entity: Entity) -> list[str]:
    """Columns the model gets to see. Includes the composite key +
    fields marked returnable OR filterable (so the model can see
    WHY a row matched)."""

    result = list(entity.key)  # composite key first
    for name, f in entity.fields.items():
        if f.returnable or f.filterable or f.searchable or f.matchable:
            result.append(name)
    # Dedup while preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for name in result:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


# ---------------------------------------------------------------------------
# SQL build
# ---------------------------------------------------------------------------


_SUPPORTED_ENTITIES: frozenset[str] = frozenset({"products"})


def build_sql(entity: Entity, query: CatalogQuery, *, branch_id: str) -> tuple[str, list[Any]]:
    """Return (sql, params). Caller executes against catalog.db.

    Always:
      - prefixes product columns with `p.`
      - LEFT-JOINs stock for the run's `branch_id` (`s.on_hand`)
      - appends `p.active = 1` as the first WHERE clause

    Entity-level guard: anything other than `products` is rejected
    even if a caller bypassed the Pydantic Literal — the SQL
    skeleton (joins, `p.active = 1`) is products-specific.
    """

    if query.entity not in _SUPPORTED_ENTITIES:
        raise QueryBuilderError(
            f"entity {query.entity!r} is not supported (only {sorted(_SUPPORTED_ENTITIES)})"
        )

    _validate_filters(entity, query.entity, query.filters)

    cols = _returnable_fields(entity)
    # products.* with `p.` prefix; stock's on_hand surfaces as a bare
    # column so the agent can see current-branch stock without caring
    # about the join.
    col_sql = ", ".join(f"p.{c}" for c in cols) + ", s.on_hand AS on_hand"

    where: list[str] = ["p.active = 1"]
    # branch_id is a bound parameter — never interpolated.
    params: list[Any] = [branch_id]

    # Explicit filters
    for key, value in query.filters.items():
        where.append(f"p.{key} = ?")
        params.append(value)

    # Text search over searchable fields — one OR'd LIKE per field.
    if query.text_search:
        search_fields = _searchable_fields(entity)
        if search_fields:
            like_clauses = " OR ".join(f"p.{f} LIKE ?" for f in search_fields)
            where.append(f"({like_clauses})")
            like_pattern = f"%{query.text_search}%"
            params.extend(like_pattern for _ in search_fields)

    sql = (
        f"SELECT {col_sql} FROM {entity.table} p "
        f"LEFT JOIN stock s ON s.sku = p.sku AND s.branch_id = ? "
        f"WHERE {' AND '.join(where)} "
        f"LIMIT {int(query.limit)}"
    )
    return sql, params


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def query_catalog(
    bundle: SemanticBundle,
    query: CatalogQuery,
    *,
    catalog_db_path: Path,
    branch_id: str,
) -> list[dict[str, Any]]:
    """Open catalog.db read-only, run the built SQL, return row
    dicts. Used by the Pricing Agent's tool handler. `branch_id`
    comes from the run and is always bound into the LEFT JOIN on
    stock — the model can't see any other branch."""

    entity = bundle.entities.get(query.entity)
    if entity is None:
        raise QueryBuilderError(f"entity {query.entity!r} is not supported")
    sql, params = build_sql(entity, query, branch_id=branch_id)
    conn = sqlite3.connect(f"file:{catalog_db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Named queries — code only (SPEC §8.2)
# ---------------------------------------------------------------------------

_NAMED_SQL: dict[str, str] = {
    # Returns {branch_id, branch_name, city, state, on_hand} for every
    # branch that stocks `sku`. Phase 4 transfers.py calls this when
    # a priced line has shortfall > 0.
    "stock_all_branches": (
        "SELECT b.id AS branch_id, b.name AS branch_name, b.city, b.state, "
        "       s.on_hand "
        "FROM stock s "
        "JOIN branches b ON b.id = s.branch_id "
        "WHERE s.sku = ? "
        "ORDER BY b.id"
    ),
}


def run_named_query(
    name: str,
    params: list[Any],
    *,
    catalog_db_path: Path,
) -> list[dict[str, Any]]:
    """Code-only entry point for named queries. The pricing agent
    cannot reach this — the only caller today is
    `agents/pricing/transfers.py`."""

    sql = _NAMED_SQL.get(name)
    if sql is None:
        raise QueryBuilderError(f"unknown named query {name!r}")
    conn = sqlite3.connect(f"file:{catalog_db_path}?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
