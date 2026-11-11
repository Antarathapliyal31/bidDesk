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
- `tests/setup.ts` — loads `@testing-library/jest-dom/vitest`; Phase-6
  guarded jsdom shims + `afterEach(cleanup + localStorage.clear +
  history.replaceState)` for isolation.
- `tests/smoke.test.tsx` — Phase-6 smoke: full App renders, Primary
  nav + main landmarks reachable, `/` lands on New bid.

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

---

# Phase 6 (frontend-shell branch)

## docs/API_CONTRACT.md (new, temporary)
- Frontend-authored contract for Phase 5. Pins the JSON shapes the
  fixture hooks model (snake_case; integer cents; Decimal-as-string
  for quantities), the error envelope + canonical code enumeration,
  the SSE event table, and the Phase-8 `PATCH /api/runs/{id}/bid`
  op union. Deleted at Phase 5 merge when `make gen-api` lands.

## frontend/public/samples/README.md
- Vite serves `/samples/*`. The New-bid "Start from a sample" cards
  fetch PDFs here. Files land when backend `make samples` runs; the
  current fetch 404s gracefully with a toast.

## frontend/src/api/
- `ApiError.ts` — `ApiError { code, message, status, details }` +
  `apiErrorFromResponse(res)` (envelope parser; HTML/unknown →
  `server_error`; bare 413 → `too_large`). `network_error` for fetch
  rejections.
- `useBranches.ts` — `useQuery<Branch[]>` over fixtures; staleTime
  Infinity.
- `useSamples.ts` — `useQuery<Sample[]>`; Sample is a discriminated
  union on `kind` (pdf → `file_url`, paste → `text`).
- `useFaults.ts` — returns `{ enabled, faults }`; `enabled: false`
  hides DemoFaultControls.
- `useRuns.ts` — `useInfiniteQuery`; fixture paginates (page size
  5); exports `shouldPollRuns(pages)` → 5 s while any run is
  running.
- `useCreateRun.ts` — `useMutation<_, ApiError, CreateRunPayload>`;
  payload is file-XOR-text via `?: never`. Shared `toCreateRunFormData`
  builder (fixture exercises it; Phase-5 fetch reuses it); omits
  `faults` key when the list is empty.
- `index.ts` — single barrel for features.

## frontend/src/app/
- `platform.ts` — `IS_MAC`, `hasModKey`, mod-key labels.
- `theme.ts` — tracks `prefers-color-scheme`; toggles `.dark` +
  `color-scheme` on `<html>`.
- `queryClient.ts` — prod client (retry predicate: network + 5xx
  once, never 4xx) + `createTestQueryClient()`.
- `useDocumentTitle.ts` — per-route `<title>`.
- `CommandMenuContext.tsx` / `ShortcutsContext.tsx` — open-state
  containers. Shortcuts also owns the WCAG 2.1.4 single-key toggle
  (localStorage-persisted).
- `CommandMenuTrigger.tsx` — top-right clickable "Search" button.
- `CommandMenu.tsx` — cmdk menu. `onSelect` stashes an action in a
  ref + closes; Radix's `onCloseAutoFocus` runs it, moving focus to
  `<main>` or into the shortcuts dialog.
- `ShortcutsSheet.tsx` — `?` dialog, grouped (Global, New bid page);
  WCAG 2.1.4 switch.
- `useGlobalShortcuts.ts` — ⌘K, n, g h, ?. Suppresses letters in
  editable targets, while any modal dialog is open, or when the
  switch is off. ⌘K always fires.
- `AppShell.tsx` — two-pane layout. Skip link first; `<main
  id="main" tabIndex={-1}>`; on pathname change, main scrollTop
  resets + focus moves (preventScroll).
- `Sidebar.tsx` — brand + Primary nav (`aria-current` via NavLink,
  2 px primary indicator on active, `hover:bg-accent/60` on
  inactive) + "Keyboard shortcuts" footer (aria-haspopup=dialog).
- `router.tsx` — Routes (SPEC §11.1); `/` → `/new`, `/runs/:runId`
  is a Phase-7 placeholder.
- `Providers.tsx` — QueryClient → CommandMenu → Shortcuts →
  BrowserRouter → Toaster. Starts the theme sync.

## frontend/src/components/
- `StatusBadge.tsx` — dot + label; color paired with text.
- `ConfidencePill.tsx`, `OriginMarker.tsx` — Phase-8 stubs so the
  visual language is settled.
- `Money.tsx` — integer cents → USD; `null` → sr-only "No value"
  (never silently "$0.00").
- `Kbd.tsx` — keyboard-key pill (`text-xs` from the scale); optional
  `label` for symbol keys.
- `EmptyState.tsx` — solid border; `headingLevel` 2 or 3.
- `ErrorState.tsx` — designed error card with retry.
- `InlineError.tsx` — red + icon; `id` for `aria-describedby`.
- `skeletons.tsx` — `RowSkeleton` / `TableSkeleton` / `CardSkeleton`.

## frontend/src/components/ui/
- 15 shadcn primitives (owned source). `sonner.tsx` rewritten to
  read `.dark` from `<html>` (no `next-themes`) and tint toast icons
  with semantic `*-text` tokens.

## frontend/src/dev-fixtures/
- `README.md` + `index.ts` + typed fixtures:
  - `types.ts` (`Fx*` types incl. `FxSampleInput` discriminated union),
  - `branches.ts` (NJ / NY / PA with `tax_rate_ppm`, mirrors backend seed),
  - `samples.ts` (PDF `file_url` + paste `text`),
  - `faults.ts` (five fault specs),
  - `runs.ts` (8 fixtures covering every §11.4 state),
  - `delay.ts` (`sleep`, `withDelay`, `failAfter`).

## frontend/src/features/new-bid/
- `limits.ts` — re-export shim to `@/lib/limits`.
- `validation.ts` — `validatePdfFile` (extension / empty / size
  with ceil-to-0.1-MB) + `validatePastedText` + `codePointLength`
  (matches Python `len()`) + optional `hasPdfMagicBytes`.
- `PdfDropzone.tsx` — drag/drop + click; dashed border;
  focus-ring via `has-[:focus-visible]`; multi-file / wrong-ext /
  oversize / empty errors; focus handoff Remove↔input; polite
  live-region announcement.
- `PasteListEditor.tsx` — sr-only `<label>`, `aria-invalid`,
  FIXED over-limit error text (doesn't change per keystroke).
- `BranchSelect.tsx` — hook-backed combobox with `aria-required`;
  `isError` → InlineError + Try again (refetch).
- `SampleInputs.tsx` — hook-backed cards; `aria-busy` skeletons
  pending; error → Try again; on pick: announce + focus Submit via
  the passed ref.
- `DemoFaultControls.tsx` — hidden when `useFaults().data.enabled
  === false`; `aria-controls` on the toggle; each row's label is
  the title only (description via `aria-describedby`).
- `SubmitBar.tsx` — sticky; **never native-disabled** (focusable);
  `aria-disabled` + onClick guard; `aria-describedby` wires the
  server-error / disabled-reason / ⌘↵ hint. Violet styling + "Run
  with N injected faults" when faults are selected.
- `NewBidPage.tsx` — owns form state; ⌘/Ctrl+Enter submits from
  anywhere (suppressed while a modal is open); window-level
  dragover/drop are `preventDefault`-ed; sample pick fetches
  `file_url` → `File`, defaults branch to BR-NJ-01 if unset, focuses
  Submit; input changes clear the server error; errors map through
  `messageForError`.

## frontend/src/features/history/
- `RunRow.tsx` — link-shaped row with sr-only metric labels and a
  visible short date (full ISO in `title`).
- `RunsTable.tsx` — j/k navigation (suppressed in editable targets).
- `HistoryPage.tsx` — loading skeleton / mapped error with retry
  that also clears `?error=1` / empty with CTA / list +
  `hasNextPage ? "Load more" : null`.

## frontend/src/features/not-found/
- `NotFoundPage.tsx` — designed 404.

## frontend/src/features/run/
- `RunPagePlaceholder.tsx` — designed card for `/runs/:runId`
  (Phase-7 live view replaces this).

## frontend/src/lib/
- `utils.ts` — `cn` (clsx + tailwind-merge).
- `format.ts` — `formatTaxRate` (ppm → %, zero-trim) and
  `formatShortDate` (en-US, UTC — deterministic tests).
- `limits.ts` — `MAX_UPLOAD_MB / BYTES / PAGES / TEXT_CHARS`;
  mirrors backend `Settings`; binary MB convention pinned.
- `apiMessages.ts` — `messageForError(err)` maps codes to UI
  copy; `too_large` / `too_many_pages` / `text_too_long` interpolate
  details fields (server limit preferred, client fallback);
  `invalid_input` prefers server message; non-ApiError →
  `console.error` + generic.

## frontend/src/styles/tokens.css
- SPEC §11.5 tokens: Inter, zinc scale, indigo accent (indigo-600
  in both modes), semantic colors with `-text` variants so AA
  passes, dark via `.dark` class, shadcn slots via `@theme inline`,
  `prefers-reduced-motion` guard. 55 contrast assertions.

## frontend/eslint.config.js
- `no-restricted-imports` scopes `@/dev-fixtures/*` (alias + any
  relative path) to `src/api/**` + `tests/**` only. Features
  consume typed hooks from `@/api` — the Phase-5 swap is a one-line
  change per hook.

## frontend/components.json
- Hand-written shadcn config (Tailwind v4, `tailwind.config = ""`,
  tokens.css path, tsx true, rsc false, baseColor zinc,
  cssVariables true, lucide).

## frontend/index.html
- `<meta name="color-scheme">` + inline FOUC guard that sets
  `.dark` + `color-scheme` before React mounts.

## frontend/tests/ (20 files, 165 assertions)
- `renderWithProviders.tsx` — per-test QueryClient + MemoryRouter
  + UI contexts.
- `tokens-contrast.test.ts` — parses tokens.css (comment-strips so
  doc-comment examples don't match block selectors), asserts AA
  for every fg/bg pair + semantic text on every surface + non-text
  3:1 for the active indicator. Self-test proves failing is possible.
- `fixtures-import-rule.test.ts` — runs the real ESLint config
  against synthetic files.
- `money.test.tsx`, `format.test.ts`, `validation.test.ts`,
  `ApiError.test.ts`, `apiMessages.test.ts`, `useCreateRun.test.ts`,
  `useRunsPolling.test.ts`, `queryClient.test.ts` — pure-logic
  tests.
- `CommandMenuTrigger.test.tsx`, `useGlobalShortcuts.test.tsx`,
  `Sidebar.test.tsx`, `ShortcutsSheet.test.tsx`,
  `CommandMenu.test.tsx` — shell interactions.
- `PdfDropzone.test.tsx`, `PasteListEditor.test.tsx`,
  `BranchSelect.test.tsx`, `SampleInputs.test.tsx` — form pieces.
- `HistoryPage.test.tsx` — pagination (5 → 8 rows + Load-more
  disappears), empty state with CTA, error state with mapped
  ApiError message + retry.
