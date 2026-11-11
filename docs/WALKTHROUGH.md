# WALKTHROUGH

One line per important file: *what it is and why it exists*, in plain
English. Used by Antara during the live presentation to narrate the
code. Append entries as files land.

## Repo root
- `CLAUDE.md` — how I work on this repo (process, golden rules, phase gates).
- `SPEC.md` — single source of truth for what BidDesk does and why.
- `Makefile` — one command for every workflow (setup, test, dev, gen-api).
  Antara runs these live; keeps the demo reliable.
- `.env.example` — the full config surface. Copy to `.env`; `.env` is
  gitignored so secrets never ship.
- `.gitignore` — excludes secrets, venv, node_modules, seeded DBs,
  uploads, editor + OS files.

## docs/
- `DECISIONS.md` — ADR-lite. One entry per non-obvious decision so
  interviewers can ask "why this, not that" and I have the answer written
  down.
- `WALKTHROUGH.md` — this file.

## backend/
- `pyproject.toml` — Python package metadata, pinned dep ranges, ruff
  + pytest config. `requires-python = ">=3.11"` so async + PEP 604
  union types just work.
- `app/__init__.py` — package marker. Empty.
- `app/main.py` — FastAPI app factory. Phase 0 exposes `/api/health`;
  the real lifespan (DB init, mark interrupted runs) comes in Phase 2.
- `app/config.py` — `Settings` via `pydantic-settings`. Reads `.env`;
  `ANTHROPIC_API_KEY` is optional at import time so tests and
  `make setup` work without it. `require_anthropic_key()` is the choke
  point that fails loudly when a real call is attempted without a key.
- `tests/test_smoke.py` — Phase 0 smoke: the app boots, health returns
  ok, and the key-optional contract works.

## frontend/
- `package.json` — pinned deps (versions chosen for shadcn 4.23
  compatibility, see DECISIONS #3); `engines.node >= 22`.
- `.nvmrc` — pins Node 24.22.2 (satisfies jsdom@31's `>=24.15.0`); `engines.node >= 24.15`.
- `tsconfig.json` — strict TypeScript: `strict`, `noUnusedLocals`,
  `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`. Catches most
  bugs at `tsc`-time before they hit the UI.
- `vite.config.ts` — Vite + React + Tailwind v4 plugin. Dev server
  proxies `/api` to the backend on :8000; Vitest config lives here too.
- `eslint.config.js` — ESLint 9 flat config. Uses the `globals` package
  (not the old boolean form) for browser/node globals.
- `index.html` — Vite entry; mounts `#root`.
- `src/main.tsx` — React bootstrap; imports `tokens.css` so Tailwind
  loads on every page.
- `src/App.tsx` — Phase 0 placeholder. Real `AppShell` + router land in
  Phase 6.
- `src/styles/tokens.css` — Tailwind v4 entry (`@import "tailwindcss"`).
  Real design tokens (Inter, 4px grid, zinc scale, indigo accent) land
  in Phase 6 per SPEC §11.5.
- `tests/setup.ts` — loads `@testing-library/jest-dom/vitest` so
  `toBeInTheDocument()` and the other matchers work in every test.
- `tests/smoke.test.tsx` — Phase 0 smoke: App renders.

---

## Phase 1 — Data & samples

### What was built
- **Units + conversions** (`backend/app/catalog/units.py`): two-tier
  normalisation (our table, then pint allowlist), four pure Decimal
  conversion functions (`length_to_pieces`, `area_to_coverage_units`,
  `squares_to_bundles`, `board_feet_to_pieces`) registered in
  `UNIT_CONVERSIONS`, dimension-based selection + identity passthrough,
  word-boundary keyword helper, human-readable `conversion_path`. All
  rounding is ceil on the final quotient only.
- **OKF semantic bundle** (`backend/app/catalog/semantic/`): one YAML
  per entity (`products`, `stock`, `branches` — branches
  `agent_visible: false`) plus `conversions.yaml`,
  `sanity_ranges.yaml`, `playbooks.yaml` (incl. `assumption_discipline`),
  `examples.yaml`, `named_queries.yaml`.
- **Loader** (`backend/app/catalog/semantic_layer.py`): strict Pydantic
  models + startup validation (aborts with "Catalog not built — run
  make seed" when the DB is missing; checks function names, field
  references, synonym canonicalisation, column drift, SKU
  exist-and-active for every example).
- **Shared strict YAML loader** (`backend/app/common/yaml_io.py`):
  duplicate-key detection, UTF-8 check, empty-file check — used by the
  semantic bundle AND the gap-rule loader.
- **Deterministic seed** (`backend/app/catalog/seed.py`, DDL in
  `schema.sql`): hand-picked tables, no `random`, no wallclock. 240
  SKUs across all nine categories; per-category baseline stock + four
  stock scenarios (full-cover transfer, partial "N of M",
  zero-everywhere, no-run-branch). Atomic `.tmp` + `os.replace`.
- **Gap rules + matcher** (`backend/app/knowledge/`):
  15 rules (item-scope requirements, item-scope questions, bid-scope
  questions with `absent_match`), `context_all_of` for AND-of-OR
  keyword groups, two-pass evaluation (requirements first, questions
  see the applied state). Cross-validated against the products bundle
  at load.
- **Demo samples** (`samples/`): deterministic reportlab generator
  (`make_samples.py`, invariant mode, no wallclock); `rfp_clean.pdf`
  (3 pages, 14 items), `rfp_messy.pdf` (8 pages, 26 items + 2 real
  conflicts + 2 exclusions + 1 labor line + prompt-injection line +
  mid-phrase line wrap + merged-cell table), `rfp_scanned.pdf` (clean
  rasterised at 150 DPI), `list_paste.txt`. Four `expected/*.json`
  answer keys with items / exclusions / labor / conflicts /
  must_not_include / page_routes / expected_requirement_rule_ids /
  expected_question_rule_ids.

### How to run it
```
make setup       # one-time: venv + pip install + npm install
make seed        # builds backend/data/catalog.db (gitignored)
make samples     # regenerates samples/*.pdf + list_paste.txt
make test        # backend pytest (278 passing) + frontend vitest
make lint        # ruff check + ruff format --check + eslint
make check       # lint + test — the CI-equivalent sweep
```
`catalog.db` is gitignored; `samples/*.pdf` and `samples/expected/*.json`
ARE committed, and a test asserts the committed samples match a fresh
build so a stale checkout fails loudly with "run `make samples`."

### How to explain this (interview talking points)
- **Key idea: code owns the guarantees, YAML owns the knowledge.** The
  semantic bundle + gap rules are data the model reads for context; the
  loader rejects any typo or drift at startup so the agent can't rely
  on an undeclared field. Three test suites (`test_semantic_layer`,
  `test_gap_rules`, `test_seed`) encode that contract.
- **Determinism everywhere.** No `random`, no wallclock. Prices come
  from a deterministic Decimal formula with multipliers; catalog
  determinism is hashed via canonical JSON of the rows (not `.db`
  bytes, which SQLite doesn't guarantee stable). PDF determinism uses
  pdfplumber text per page (not PDF bytes); scanned-PDF determinism
  uses 150-DPI image hashes.
- **Main trade-off (PT framing)**: PT lumber is seeded only in SYP
  (East Coast realism). The ambiguous-PT example records a treatment
  assumption, not a species assumption — the `assumption_discipline`
  playbook says you only record an assumption when choosing between
  real catalog alternatives.
- **Likely interviewer question — "How do you prevent the model from
  pricing a hallucinated SKU?"** Three layers: (1) the semantic-layer
  loader fails at startup if YAML and `catalog.db` columns disagree,
  so "unknown field" can't exist at query time; (2) `query_catalog`
  identifiers come from the YAML allowlist and values are bound
  parameters; (3) `agents/pricing/verify.py` looks up the proposed SKU
  in the catalog and downgrades to `no_match` if it doesn't exist —
  logged to `rejected_claims` for the trace.
- **Likely interviewer question — "Why not just ship a bigger prompt
  with all the rules in it?"** Each gap rule has structured
  `missing_condition` + `requirement` fields so code can pre-filter
  candidate rules per item before the model ever sees them, and
  requirements are enforced deterministically (confidence drops to
  medium when PT is applied without the estimator naming the variant).
  A free-text prompt can't be tested or diffed; this can.

### Files added in Phase 1
Backend: `app/catalog/{__init__,units,semantic_layer,seed,schema.sql}.py`,
`app/catalog/semantic/*.yaml` × 8, `app/common/{__init__,yaml_io}.py`,
`app/knowledge/{__init__,matcher}.py`, `app/knowledge/gap_rules.yaml`.
Tests: `tests/catalog/{__init__,test_units,test_semantic_layer,test_seed}.py`,
`tests/knowledge/{__init__,test_gap_rules}.py`,
`tests/samples/{__init__,test_make_samples}.py`. Repo root: `samples/`
with generator, four PDFs/text, four expected JSONs.

