-- Catalog DB schema (SPEC §5.2).
-- Single source of truth: both app/catalog/seed.py and the
-- test_semantic_layer.py fixture load this file.

CREATE TABLE branches (
  id           TEXT PRIMARY KEY,
  name         TEXT,
  city         TEXT,
  state        TEXT,
  tax_rate_ppm INTEGER NOT NULL
);

CREATE TABLE products (
  sku                TEXT PRIMARY KEY,
  category           TEXT NOT NULL,
  nominal_size       TEXT,
  length_ft          INTEGER,
  thickness_in       TEXT,
  width_in           INTEGER,
  height_in          INTEGER,
  grade              TEXT,
  species            TEXT,
  treatment          TEXT NOT NULL DEFAULT 'none',
  fire_rating        TEXT NOT NULL DEFAULT 'none',
  moisture_resistant INTEGER NOT NULL DEFAULT 0,
  r_value            INTEGER,
  sale_unit          TEXT NOT NULL,
  pack_qty           INTEGER,
  coverage_sqft      INTEGER,
  unit_price_cents   INTEGER NOT NULL,
  price_updated_at   TEXT NOT NULL,
  active             INTEGER NOT NULL DEFAULT 1,
  name               TEXT NOT NULL,
  description        TEXT NOT NULL,
  CHECK (unit_price_cents > 0),
  CHECK (coverage_sqft IS NULL OR coverage_sqft > 0),
  CHECK (pack_qty IS NULL OR pack_qty > 0)
);

CREATE TABLE stock (
  sku               TEXT    NOT NULL REFERENCES products(sku),
  branch_id         TEXT    NOT NULL REFERENCES branches(id),
  on_hand           INTEGER NOT NULL,
  next_receipt_date TEXT,
  next_receipt_qty  INTEGER,
  PRIMARY KEY (sku, branch_id),
  CHECK (on_hand >= 0)
);
