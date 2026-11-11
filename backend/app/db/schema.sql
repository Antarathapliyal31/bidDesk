-- App DB schema (SPEC §5.1 + plan adjustments).
--
-- One file applied on startup via `init.apply_schema`. Every CREATE is
-- IF NOT EXISTS so repeat startups are idempotent.
--
-- PRAGMAs (WAL, foreign_keys=ON) are set per-connection by
-- `init.configure_pragmas` and `open_app_db`. They are NOT part of the
-- schema file.
--
-- Trigger policy:
--   - items + item_requirements + bid_edits: insert-only. UPDATE and
--     DELETE both ABORT.
--   - bids where kind IN ('agent','final'): UPDATE and DELETE both
--     ABORT. Drafts are mutable (repo manages the revision bump
--     inside a PATCH transaction).
--   - events.id is strict monotonic via INTEGER PRIMARY KEY
--     AUTOINCREMENT — no trigger needed.
--
-- Every CHECK is named with `CONSTRAINT <table>_<rule>_ck` so error
-- strings pin the exact rule that fired. Tests match on the name.
--
-- Foreign keys:
--   - All per-run child tables (documents, pages, conflicts, events,
--     bids, bid_edits) reference runs(id).
--   - item_triage / item_requirements / price_results reference the
--     composite (run_id, id) of items — can't carry data about an
--     item that doesn't exist.

CREATE TABLE IF NOT EXISTS runs (
  id             TEXT PRIMARY KEY,                                    -- "run_" + short uuid
  created_at     TEXT NOT NULL,
  finished_at    TEXT,
  status         TEXT NOT NULL,
  outcome        TEXT,
  outcome_detail TEXT,
  input_kind     TEXT NOT NULL,
  input_name     TEXT,
  project_name   TEXT,
  branch_id      TEXT NOT NULL,
  current_step   TEXT,
  state_json     TEXT NOT NULL DEFAULT '{}',
  tokens_in      INTEGER NOT NULL DEFAULT 0,
  tokens_out     INTEGER NOT NULL DEFAULT 0,
  faults_json    TEXT,
  CONSTRAINT runs_status_ck     CHECK (status IN ('running','completed','partial','failed','interrupted')),
  CONSTRAINT runs_outcome_ck    CHECK (outcome IS NULL OR outcome IN ('bid_ready','no_materials','invalid_input','budget_exhausted','error')),
  CONSTRAINT runs_input_kind_ck CHECK (input_kind IN ('pdf','text'))
);

CREATE TABLE IF NOT EXISTS documents (
  run_id       TEXT PRIMARY KEY REFERENCES runs(id),
  filename     TEXT,
  sha256       TEXT NOT NULL,
  page_count   INTEGER NOT NULL,
  stored_path  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
  run_id       TEXT NOT NULL REFERENCES runs(id),
  page_no      INTEGER NOT NULL,
  route        TEXT NOT NULL,
  text         TEXT,
  text_source  TEXT,
  table_issue  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, page_no),
  CONSTRAINT pages_route_ck       CHECK (route IN ('text','vision','text+vision','unreadable')),
  CONSTRAINT pages_text_source_ck CHECK (text_source IS NULL OR text_source IN ('pdf_text_layer','vision_transcription'))
);

CREATE TABLE IF NOT EXISTS items (
  id                      TEXT NOT NULL,
  run_id                  TEXT NOT NULL REFERENCES runs(id),
  kind                    TEXT NOT NULL,
  description             TEXT NOT NULL,
  quantity_text           TEXT,
  quantity                TEXT,
  unit_raw                TEXT,
  unit                    TEXT,
  specs_json              TEXT NOT NULL DEFAULT '{}',
  source_page             INTEGER,
  source_quote            TEXT,
  read_via                TEXT,
  verified                INTEGER NOT NULL DEFAULT 0,
  verification_notes_json TEXT NOT NULL DEFAULT '[]',
  created_at              TEXT NOT NULL,
  PRIMARY KEY (run_id, id),
  CONSTRAINT items_kind_ck     CHECK (kind IN ('material','labor','exclusion','note','unreadable')),
  CONSTRAINT items_read_via_ck CHECK (read_via IS NULL OR read_via IN ('text','vision')),
  -- verified=True promises a text-match citation (SourceRef invariant):
  -- read_via='text' + a page + a non-empty quote.
  CONSTRAINT items_verified_ck CHECK (
    verified = 0
    OR (
      read_via = 'text'
      AND source_page IS NOT NULL
      AND trim(coalesce(source_quote, '')) <> ''
    )
  ),
  -- read_via='vision' requires a page, forbids verified=True; quote
  -- is optional. Encoded as a three-way IS-NULL/'='/'=' so a NULL
  -- read_via or 'text' just skips this rule.
  CONSTRAINT items_vision_ck CHECK (
    read_via IS NULL
    OR read_via = 'text'
    OR (read_via = 'vision' AND source_page IS NOT NULL AND verified = 0)
  )
);
CREATE TRIGGER IF NOT EXISTS items_no_update
  BEFORE UPDATE ON items
  BEGIN SELECT RAISE(ABORT, 'items are immutable'); END;
CREATE TRIGGER IF NOT EXISTS items_no_delete
  BEFORE DELETE ON items
  BEGIN SELECT RAISE(ABORT, 'items are immutable'); END;

CREATE TABLE IF NOT EXISTS item_triage (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL,
  item_id    TEXT NOT NULL,
  category   TEXT NOT NULL,
  reason     TEXT NOT NULL,
  created_at TEXT NOT NULL,
  CONSTRAINT item_triage_category_ck CHECK (category IN ('price','labor','exclusion','ambiguous','ignore_note')),
  FOREIGN KEY (run_id, item_id) REFERENCES items(run_id, id)
);

CREATE TABLE IF NOT EXISTS item_requirements (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id         TEXT NOT NULL,
  item_id        TEXT NOT NULL,
  field          TEXT NOT NULL,
  -- `value_json` is JSON. Per RequirementView, a requirement carries
  -- either `value` (scalar: str/int/bool) OR `value_in` (non-empty
  -- list). We store the shape verbatim so a read-back can
  -- discriminate by Python type (`isinstance(parsed, list)`).
  value_json     TEXT NOT NULL,
  reason         TEXT NOT NULL,
  source_page    INTEGER,
  source_quote   TEXT,
  read_via       TEXT,
  quote_verified INTEGER NOT NULL DEFAULT 0,
  created_by     TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  CONSTRAINT item_requirements_read_via_ck   CHECK (read_via IS NULL OR read_via IN ('text','vision')),
  CONSTRAINT item_requirements_created_by_ck CHECK (created_by IN ('orchestrator','user')),
  -- quote_verified=True requires a text read + page + non-empty
  -- quote. Requirements/conflicts that would rest on an unverifiable
  -- quote never land here — they go to rejected_claims (DECISIONS #17).
  CONSTRAINT item_requirements_quote_verified_ck CHECK (
    quote_verified = 0
    OR (
      read_via = 'text'
      AND source_page IS NOT NULL
      AND trim(coalesce(source_quote, '')) <> ''
    )
  ),
  -- Same vision rule as items.
  CONSTRAINT item_requirements_vision_ck CHECK (
    read_via IS NULL
    OR read_via = 'text'
    OR (read_via = 'vision' AND source_page IS NOT NULL AND quote_verified = 0)
  ),
  FOREIGN KEY (run_id, item_id) REFERENCES items(run_id, id)
);
CREATE TRIGGER IF NOT EXISTS item_requirements_no_update
  BEFORE UPDATE ON item_requirements
  BEGIN SELECT RAISE(ABORT, 'item_requirements are insert-only'); END;
CREATE TRIGGER IF NOT EXISTS item_requirements_no_delete
  BEFORE DELETE ON item_requirements
  BEGIN SELECT RAISE(ABORT, 'item_requirements are insert-only'); END;

CREATE TABLE IF NOT EXISTS price_results (
  id                         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id                     TEXT NOT NULL,
  item_id                    TEXT NOT NULL,
  attempt                    INTEGER NOT NULL,
  status                     TEXT NOT NULL,
  sku                        TEXT,
  product_name               TEXT,
  sale_unit                  TEXT,
  sale_qty                   TEXT,
  conversion_path            TEXT,
  unit_price_cents           INTEGER,
  branch_on_hand             INTEGER,
  shortfall                  INTEGER,
  transfer_options_json      TEXT NOT NULL DEFAULT '[]',
  assumptions_json           TEXT NOT NULL DEFAULT '[]',
  attribute_mismatches_json  TEXT NOT NULL DEFAULT '[]',
  verify_notes_json          TEXT NOT NULL DEFAULT '[]',
  is_substitute              INTEGER NOT NULL DEFAULT 0,
  substitute_reason          TEXT,
  confidence                 TEXT,
  confidence_reasons_json    TEXT NOT NULL DEFAULT '[]',
  searched                   TEXT,
  created_at                 TEXT NOT NULL,
  CONSTRAINT price_results_status_ck     CHECK (status IN ('matched','substitute','no_match','failed','timeout','skipped')),
  CONSTRAINT price_results_confidence_ck CHECK (confidence IS NULL OR confidence IN ('high','medium','low')),
  FOREIGN KEY (run_id, item_id) REFERENCES items(run_id, id)
);

CREATE TABLE IF NOT EXISTS conflicts (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  type          TEXT NOT NULL,
  side_a_json   TEXT NOT NULL,
  side_b_json   TEXT NOT NULL,
  explanation   TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  CONSTRAINT conflicts_type_ck CHECK (type IN ('size_mismatch','excluded_but_listed','spec_vs_list','stock_vs_delivery'))
);

CREATE TABLE IF NOT EXISTS rejected_claims (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL REFERENCES runs(id),
  kind         TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  reason       TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

-- Events: trace + SSE source of truth. `id` is both the primary key
-- and the SSE event id.
CREATE TABLE IF NOT EXISTS events (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          TEXT NOT NULL REFERENCES runs(id),
  ts              TEXT NOT NULL,
  type            TEXT NOT NULL,
  agent           TEXT,
  span_id         TEXT,
  parent_span_id  TEXT,
  payload_json    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run_id  ON events(run_id, id);
CREATE INDEX IF NOT EXISTS events_type_ts ON events(type, ts);

CREATE TABLE IF NOT EXISTS bids (
  id            TEXT PRIMARY KEY,
  run_id        TEXT NOT NULL REFERENCES runs(id),
  kind          TEXT NOT NULL,
  revision      INTEGER NOT NULL,
  data_json     TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  finalized_at  TEXT,
  UNIQUE (run_id, kind, revision),
  CONSTRAINT bids_kind_ck          CHECK (kind IN ('agent','draft','final')),
  CONSTRAINT bids_revision_ck      CHECK (revision >= 0),
  -- (kind='agent') ⇔ (revision=0). Encoded as equality of two
  -- boolean subexpressions so neither side can drift.
  CONSTRAINT bids_agent_revision_ck CHECK ((kind = 'agent') = (revision = 0))
);
CREATE TRIGGER IF NOT EXISTS bids_agent_final_no_update
  BEFORE UPDATE ON bids
  WHEN OLD.kind IN ('agent','final')
  BEGIN SELECT RAISE(ABORT, 'agent/final bids are immutable'); END;
CREATE TRIGGER IF NOT EXISTS bids_agent_final_no_delete
  BEFORE DELETE ON bids
  WHEN OLD.kind IN ('agent','final')
  BEGIN SELECT RAISE(ABORT, 'agent/final bids are immutable'); END;

CREATE TABLE IF NOT EXISTS bid_edits (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL REFERENCES runs(id),
  revision   INTEGER NOT NULL,
  op_json    TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS bid_edits_no_update
  BEFORE UPDATE ON bid_edits
  BEGIN SELECT RAISE(ABORT, 'bid_edits are insert-only'); END;
CREATE TRIGGER IF NOT EXISTS bid_edits_no_delete
  BEFORE DELETE ON bid_edits
  BEGIN SELECT RAISE(ABORT, 'bid_edits are insert-only'); END;
