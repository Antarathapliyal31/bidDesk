# BidDesk

Atreyus AI second-round take-home. An LBM distributor's estimator uploads an
RFP (PDF or pasted list), picks a branch, and watches three agents produce a
priced, editable bid with every line traceable to its source page.

Core principle: **the LLM decides, the code guarantees.** Prices, totals,
unit conversions, confidence, SKU existence, quote verification — all
deterministic code. The models never set a number.

The authoritative spec is [`SPEC.md`](./SPEC.md). Process rules are in
[`CLAUDE.md`](./CLAUDE.md). The delivery checklist is in
[`docs/CHECKLIST.md`](./docs/CHECKLIST.md).

---

## Quickstart

Prerequisites: Python 3.11+ and Node 24.15+ (`.nvmrc` pins the current
version — `nvm install && nvm use` works).

```bash
cp .env.example .env
# paste your ANTHROPIC_API_KEY into .env
make setup      # creates backend/.venv, installs backend[dev] + frontend deps
make seed       # builds backend/data/catalog.db from catalog/seed.py
make samples    # generates samples/*.pdf (clean, messy, scanned)
make test       # runs backend pytest + frontend vitest
make dev        # backend on :8000, frontend on :5173 (proxies /api -> :8000)
```

Visit `http://localhost:5173`. The frontend runs in **fixture mode** by
default (no network calls), so you can explore the UI without a running
backend. `make dev` wires the proxy so real `/api/*` calls land on the
Python server; the Phase-5 merge swaps every `useXxx` hook's fixture
`queryFn` for a real `fetch`.

Catalog prices in the seeded DB are **synthetic**. No real brand names
are used; in production this seed is replaced by the supplier's
ERP / pricebook feed.

### After pulling a schema change

The runtime run DB (`backend/data/app.db`) is created with `CREATE TABLE IF NOT EXISTS`, which does **not** retrofit new CHECK constraints onto an existing file. If a pull adds a status value or a new CHECK clause (see git log for touches to `backend/app/db/schema.sql`), delete the run DB so it rebuilds from the current schema:

```bash
make clean-db
```

The seeded catalog (`backend/data/catalog.db`) is unaffected — rebuild that with `make seed` only when `app/catalog/seed.py` or `app/catalog/schema.sql` changes.

---

## Architecture

```
┌─────────────┐          ┌──────────────────────────────────────────┐
│  New-bid    │ POST PDF │          FastAPI (:8000)                 │
│  / paste    │─────────▶│  api/runs.py                             │
└─────────────┘          │     │                                    │
                         │     ▼                                    │
                         │  runtime/runner.py  ──┐                  │
                         │     │                 │                  │
                         │     ▼                 │                  │
                         │  delegate() ──▶ Document Analyst         │
                         │     │                 ▼                  │
                         │     │           ▶ Pricing Agent          │
                         │     ▼                                    │
                         │  bid/assemble.py → SQLite (app.db)       │
                         │     │                 │                  │
                         └─────┼─────────────────┼──────────────────┘
                     SSE│    events         GET /api/runs/{id}
                        ▼                        │
                 ┌──────────────┐                │
                 │  useRunStream│                │
                 │  → reducer   │                ▼
                 │  → RunPage   │         ┌──────────────┐
                 └──────────────┘         │  BidWorkspace│
                                          │  (PATCH ops) │
                                          └──────────────┘
```

Three agents (document_analyst · orchestrator · pricing_agent) delegate
via a hand-written in-process runtime (no LangGraph, no CrewAI). Every
agent-to-agent call crosses a typed Pydantic boundary; the runtime
retries, times out, and traces every call. Events hit a SQLite
`events` table + an in-memory bus that the SSE endpoint multiplexes to
browser clients.

See `docs/WALKTHROUGH.md` for a file-by-file tour of what each module
owns; see `docs/API_CONTRACT.md` for the HTTP shapes.

### Delegation sequence (one run)

1. `POST /api/runs` → `runtime/runner.py` persists the file,
   starts a background task.
2. Analyst `extract` span: page-route (text / vision / text+vision) →
   per-page `extract_items` LLM call → `verify.py` checks every quote
   against the page → `item.extracted` events.
3. Orchestrator `loop` span: `classify_items` → `check_gap_rules` →
   `add_requirements` → `delegate("pricing_agent", PriceLines)`.
4. Pricing `batch` span (nested under orchestrator): per line,
   `search_sku` → `verify_pricing` → `item.priced` events with status
   and confidence.
5. Orchestrator reviews, emits `conflict.flagged` / `item.flagged`
   events, calls `submit_bid` → `assemble.py` validates and writes
   `bid` at `kind="agent", revision=0`.
6. `bid.ready` event lands; UI flips the tab and the editable draft
   starts at `kind="draft", revision=1`.

### State on the frontend

- **Server cache**: TanStack Query. Keys `['runs']`,
  `['run', id, 'summary']`, `['bid', id]`.
- **Live run**: `useRunStream(runId)` pipes SSE events into a
  pure `runReducer`. Idempotent: `event.id ≤ lastEventId` is a
  noop, so reconnect + replay converge.
- **Bid editing**: `useBidDraft(runId)` holds `serverDraft`,
  `localDraft`, `agentBid`, `pendingOps[]`, `inFlightOps[]`.
  Edits apply optimistically; autosave debounces 800 ms → PATCH
  with `base_revision`; server totals win on reconcile.
- **No Redux / Zustand** (DECISIONS #2).

### Type-safety pipeline

`make gen-api` runs `python -m app.export_openapi` →
`frontend/openapi.json` → `npx openapi-typescript openapi.json
-o src/api/schema.gen.ts`. The frontend never hand-writes an
API shape. `make check-contract` fails when the generated files
are stale. TanStack Query hooks + Zod form schemas pin to the
generated types via `satisfies z.ZodType<Components['schemas']['X']>`.

Today's `frontend-phase7` branch is in a **transition state**:
contract types live in `src/api/{bid,events}.ts` as hand-synced
copies of the spec. The Phase-5 merge deletes them.

### Guardrail table

| Concern | Technique |
|---|---|
| LLM decides prices | ❌ never; `pricing_agent` returns SKU + quantity, `bid/totals.py` computes money (half-up tax) |
| Prompt injection | Spotlight-wrapped page text, escape sequences stripped, extract-tool-forced JSON schema, every quote re-verified against the source page |
| SQL injection from the model | No raw SQL anywhere; `catalog/semantic_layer.yaml` is the only query surface and the model sees allowlisted identifiers + always-parameterised values |
| Schema drift | OpenAPI → `schema.gen.ts`; `make check-contract` fails CI on drift |
| Hallucinated code sections | Analyst returns `(verify)` + `null` when uncertain; orchestrator never backfills |
| Money as floats | Integer cents everywhere, `Decimal` on quantities |
| Unverified citations | Analyst's `verify.py` compares every quote against the normalised page text; failed quotes are flagged, never silently dropped |

---

## Failure-injection demo

`SPEC §10.6`. Toggle on in the New-bid page under "Demo failure injection":

- **pricing_timeout_once** — the first pricing batch exceeds its 45s
  timeout; the orchestrator retries once and the batch succeeds the
  second time. You'll see the amber retry row in the trace timeline.
- **analyst_malformed_once** — the analyst's first `extract_items`
  call returns JSON that fails the Pydantic validator; the runtime
  surfaces the per-field errors, retries, the second call succeeds.
- **budget_exhaustion** — the orchestrator exhausts its step budget
  before `submit_bid`; the fallback assembler takes over and the
  bid comes back `kind="draft", generated_by="fallback"`, with
  `partial_reasons` populated.

These are shipping gates: the demo UI shows every error state
(retry note, validation detail, partial banner) because the
shipping code does.

---

## Trade-offs

| Choice | Why | What we give up |
|---|---|---|
| SQLite + in-memory event bus | Zero ops, single-binary deploy, trivial tests, deterministic | No horizontal scale; a prod install needs Postgres + Redis (or NATS) for events |
| In-process agent runtime | Fewest moving parts, easy to trace end-to-end | No job queue, no retry across process restarts; prod needs a real queue |
| Local uploads directory | Simple, no S3 bill | Not horizontally scalable; prod moves to S3 + signed URLs |
| Hand-written delegation loop | Explainable in an interview, every control-flow transition is a line you can point at | No off-the-shelf tracing / replay; we built our own |
| `react-pdf` + `customTextRenderer` for citation highlight | One hook, fits in a single lazy chunk | No cross-text-item highlight; see DECISIONS F6 |
| No auth, no tenancy | Take-home scope | Prod needs auth + multi-tenant run isolation |
| No real ERP catalog | Seeded synthetic prices | Prod reads the supplier pricebook (SPEC §16) |

---

## What I'd change for a paying customer

- **Postgres + a job queue** (Celery / Dramatiq / arq). SQLite is
  wonderful for tests; it isn't a shared-write system. The event
  bus becomes NATS / Redis Streams so `GET /events?after_id=N`
  can resume across process restarts.
- **S3 for uploads**. Pre-signed-URL ingest from the browser,
  server emits the run and never touches the raw bytes.
- **Supplier catalog feed** (ERP integration). Replace
  `catalog/seed.py` with a pull from the supplier's pricebook
  via the same semantic-layer loader. Rows missing fields a
  conversion needs (`coverage_sqft`, `pack_qty`, `length_ft`,
  `nominal_size`) flag the bid line as
  `unit_conversion_unavailable` instead of being guessed.
- **Auth + tenancy**. OIDC, per-tenant run isolation, branch
  roles.
- **OTel + Langfuse** for traces across agents + LLM calls.
  Our `trace_span` already produces the right shape; wiring an
  exporter is a few lines.
- **Eval in CI**. `python -m app.eval` already scores the
  golden set; CI publishes a markdown diff to `docs/EVAL.md`
  and blocks regressions.
- **Rate limiting** on `POST /api/runs`, upload size enforcement
  at the edge (we currently rely on the server).
- **OCR service** for heavy scans — the vision route falls back
  to Claude today; a document-AI service is cheaper + faster
  for the long tail.

---

## What's broken / not done

Honest list (also in `docs/CHECKLIST.md`):

- Playwright covers the happy path only (paste → bid →
  finalize). Reconnect / fault-injected / stale-revision aren't
  in the e2e yet.
- No dedicated `no_materials` empty-result card; the Bid-tab
  placeholder copy covers it.
- `BidDiff` ships as counts; there's no side-by-side final
  rendering yet.
- `StockCell` component is placeholder-only; the LinesTable
  shows `short` chips from `match.shortfall` instead.
- Stale-revision reload banner isn't wired to `FinalizeBar`
  (reducer handles 409 correctly; the UI toast is the fallback).
- Shortcuts sheet doesn't list the Bid-tab shortcuts yet.

---

## What was AI-assisted and how it was reviewed

Antara wrote this with Claude Code (CLI, Opus 4.7 fast mode).
The process rules in `CLAUDE.md` bind the assistant to:

- Read `SPEC.md` at every session start.
- Post a per-phase plan and wait for an explicit "go".
- Never commit or push without an explicit "yes".
- Never use `-i` git flags or `-A` adds.
- Prefer new commits over amends.
- Record every judgment call in `docs/DECISIONS.md`
  (12 engineering decisions + 9 frontend-only `F*` entries on
  this branch).

Each phase ends with a summary including a "How to explain
this" section — the three to six bullets Antara uses when
walking through the code live.

Code review was manual: every phase was browser-tested against
all four sample inputs, with a running "manual pass" list that
produced ~30 follow-up fixes (see
`tests/ManualPassFindings{,R}.test.tsx` for the pinned
behaviours).
