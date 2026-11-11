# API_CONTRACT (frontend-driven notes)

**TEMPORARY** — once the `frontend-shell` branch lands at the
**Phase 5 merge**, the backend emits `openapi.json`, `make gen-api`
produces `src/api/schema.gen.ts`, and this file is deleted. Until
then, these are the response shapes the `frontend-shell` branch's
fixture-backed hooks model, written down so the Phase-5 backend can
match them 1:1 with no call-site churn.

**Conventions.** JSON field names are **snake_case** (matches
Pydantic defaults in the backend). The frontend `Fx*` types and the
hook return types mirror the server shapes verbatim. Dates are
ISO-8601 UTC strings. Money is integer cents (`*_cents: int`) — SPEC
§2, CLAUDE.md §3. Decimal quantities (`sale_qty`, `extended_cents`
intermediate) cross the wire as strings so JS doesn't float them.

When a shape here disagrees with `SPEC.md`, **SPEC wins** — raise
the question, don't silently rewrite the fixtures. Sections that
intentionally **deviate from SPEC** are called out inline; those are
negotiation items for the Phase 5 merge, not stealth fiat.

---

## Common error envelope — SPEC §13

Every error response, every status except 2xx:

```json
{
  "error": {
    "code": "pdf_encrypted",
    "message": "This PDF is password-protected.",
    "details": { ... }   // optional, kind-specific
  }
}
```

**Code enumeration** (canonical):

| code                | HTTP | When                                                                       |
| ------------------- | ---- | -------------------------------------------------------------------------- |
| `invalid_input`     | 422  | Catch-all for ingest validation (e.g. neither `file` nor `text` set, both set, missing `branch_id`). Server message should describe the specific problem — the UI prefers `error.message` here over the static one. |
| `pdf_encrypted`     | 422  | Password-protected PDF.                                                    |
| `pdf_corrupt`       | 422  | Magic bytes mismatch or parser raised.                                     |
| `too_large`         | 422 or 413 | Over `max_upload_mb`. `details.size_mb: number` carries the actual size; `details.limit_mb: number` the server's current ceiling. Also synthesized client-side from a bare HTTP 413 with no JSON envelope. |
| `too_many_pages`    | 422  | Over `max_pages`. `details.page_count: number` + `details.max_pages: number`. |
| `empty_input`       | 422  | 0-byte file or `text.trim() == ""`.                                        |
| `text_too_long`     | 422  | Pasted text over `max_text_chars` (counted as code points, matches `len()`). `details.count: number` + `details.limit: number`. |
| `unsupported_media` | 415  | Request body isn't one of the accepted content types.                      |
| `invalid_branch`    | 422  | `branch_id` not in the catalog.                                            |
| `run_not_found`     | 404  | Run id unknown or its uploads were purged.                                 |
| `stale_revision`    | 409  | `draft_revision` doesn't match server (SPEC §9.4). `details.current` carries the server's current Bid so the UI can reconcile. |
| `open_flags`        | 422  | Finalize blocked by unresolved review lines (SPEC §9.6). `details.open_line_ids: string[]`. |
| `network_error`     |  0   | Client-synthesized when `fetch` rejects (no response). Never sent by the server; constructed locally with `new ApiError("network_error", …, 0)`. |
| `server_error`      | varies | Client fallback when the response isn't a well-formed envelope (HTML 500, unknown `code`). `details.raw_code: string \| null` carries the original unknown code so the server team can patch the enum. |

---

## Shared view types

Referenced from the endpoint shapes below. Most mirror SPEC §9.1
verbatim — this block is the single place the field sets are
spelled out.

### `SourceRef` — SPEC §9.1

```json
{
  "page":     3 | null,
  "quote":    "(40) 2x6x16 PT" | null,
  "read_via": "text | vision" | null,
  "verified": true
}
```

### `ItemView` — payload of `item.extracted` (SPEC §10.3)

```json
{
  "id":             "item_1",
  "kind":           "material | labor | exclusion | note | unreadable",
  "description":   "2x6x16 PT",
  "quantity_text": "40" | null,
  "unit_raw":      "pcs" | null,
  "page":          3 | null,
  "verified":      true,
  "notes":         ["quote_not_found", "quantity_not_in_quote", "unknown_unit", "unit_pint_convertible"]
}
```

### `PricedLineView` — payload of `item.priced` (SPEC §10.3)

```json
{
  "item_id":          "item_12",
  "status":           "matched | substitute | no_match | failed | timeout | skipped",
  "sku":              "LUM-2X6-16-SYP-2-PTAG" | null,
  "name":             "SYP #2 2x6x16 pressure-treated above ground" | null,
  "sale_qty":         "40" | null,              // Decimal as string (SPEC §9.1)
  "sale_unit":        "EA" | null,
  "unit_price_cents": 1575 | null,
  "confidence":       "high | medium | low" | null,
  "reasons":          ["..."],
  "shortfall":        0 | null
}
```

### `ConflictView` — SPEC §9.1 + §5.1

```json
{
  "id":    "conf_1",
  "type":  "size_mismatch | excluded_but_listed | spec_vs_list | stock_vs_delivery",
  "side_a": {
    "item_id":  "item_5" | null,
    "page":     4,
    "quote":    "all plates on concrete to be PT",
    "verified": true
  },
  "side_b": {
    "item_id":  null,
    "page":     7,
    "quote":    "2x6 SPF, 40 pcs",
    "verified": true
  },
  "explanation": "..."
}
```

### `AssumptionView` — SPEC §9.2

```json
{
  "item_id": "item_7",
  "text":    "Grade not specified — assumed #2"
}
```

### `ContractorQuestionView` — SPEC §9.2

```json
{
  "text":  "Confirm whether sill plates on concrete should be pressure-treated.",
  "basis": {
    "kind":   "gap_rule | conflict | flagged_line",
    "ref_id": "gap_pt_plates_on_concrete"
  }
}
```

### `BidDiff` — SPEC §9.7

```json
{
  "changed":  2,
  "added":    1,
  "deleted":  0,
  "accepted": 3
}
```

---

## Totals + tax — SPEC §9.3

Totals and tax are computed **server-side only** (`bid/totals.py`
per SPEC). The frontend never recomputes — it displays what the
server returns.

- Per line: `extended_cents = round_half_up(Decimal(sale_qty) × unit_price_cents)`.
- `subtotal_cents = sum(extended_cents)` over priced lines.
- `tax_cents = round_half_up(subtotal_cents × tax_rate_ppm / 1_000_000)` — computed **once per bid**, not per line.
- `total_cents = subtotal_cents + tax_cents`.
- "Half-up to cents" means `ROUND_HALF_UP` on `Decimal` values before casting to `int`.

---

## Phase 5 endpoints

### `POST /api/runs` — start a run — SPEC §13, §6.1

Multipart body. **Exactly one** of `file` or `text` must be set
(both missing or both present → `invalid_input`).

| field       | required | type                | notes                                        |
| ----------- | -------- | ------------------- | -------------------------------------------- |
| `file`      | one-of   | PDF binary          | ≤ `max_upload_mb`; magic bytes must be `%PDF-` (SPEC §6.1). |
| `text`      | one-of   | string              | ≤ `max_text_chars` **code points** (matches Python `len`). |
| `branch_id` | yes      | string              | Must exist in `GET /api/branches`.           |
| `faults`    | no       | JSON array (keys)   | Absent or `[]` is always accepted; a non-empty list requires `demo_faults_enabled` server-side. Clients should OMIT the field entirely when the list is empty (don't send `"[]"`) — see §10.6.                               |

**Success** — `201`:

```json
{ "run_id": "run_8a2e" }
```

**Errors.** `422` with any of: `pdf_encrypted`, `pdf_corrupt`,
`too_large`, `too_many_pages`, `empty_input`, `text_too_long`,
`invalid_branch`, `invalid_input`.

### `GET /api/runs` — list runs — SPEC §13

Query params (all optional):

| param    | type     | notes                                                                            |
| -------- | -------- | -------------------------------------------------------------------------------- |
| `status` | enum     | `running \| completed \| partial \| failed \| interrupted`                       |
| `cursor` | opaque   | From a prior response's `next_cursor`. Opaque string — the client never decodes. |
| `limit`  | int      | Default **20**, max **100**. Server clamps; a larger request isn't an error.     |

Response body:

```json
{
  "items": [RunSummary, ...],
  "next_cursor": "opaque-str" | null
}
```

`RunSummary` (matches `src/api/useRuns.ts` `RunSummary`, which
re-exports `FxRunSummary` for now):

```json
{
  "id":            "run_...",
  "status":        "running | completed | partial | failed | interrupted",
  "outcome":       "bid_ready | no_materials | invalid_input | budget_exhausted | error" | null,
  "input_kind":    "pdf | text",
  "input_name":    "rfp_messy.pdf",
  "project_name":  "Maple St. Duplex" | null,
  "branch_name":   "BidDesk North Jersey",
  "priced_lines":  22,
  "open_flags":    6,
  "total_cents":   981450 | null,
  "created_at":    "2026-11-10T13:02:11Z",
  "finalized_at":  "2026-11-10T13:42:00Z" | null,
  "diff":          BidDiff | null
}
```

`null` `next_cursor` means the client has reached the end.

**Phase-5 mutation TODO.** After `POST /api/runs` succeeds, the
frontend calls `queryClient.invalidateQueries({ queryKey: ["runs"] })`
so the history re-fetches.

### `GET /api/runs/{id}` — run + bid detail — SPEC §13

**Deviation from SPEC §13.** SPEC lists a separate
`GET /api/runs/{id}/bid` returning `{ agent, draft, final, diff }`.
We collapse that into this one endpoint: the detail response carries
one `bid` key (the current authoritative state — draft while
editing, final once finalized) plus `agent_bid` and `diff` for
history views. Agree at Phase 5 merge or amend SPEC.

Response body (shape mirrors SPEC §9.1 `Bid` + runtime fields):

```json
{
  "id":            "run_...",
  "status":        "...",
  "outcome":       "...",
  "outcome_detail":"...",
  "input": {
    "kind":        "pdf | text",
    "name":        "...",
    "page_count":  8 | null
  },
  "branch":        { "id": "BR-NJ-01", "name": "BidDesk North Jersey", "tax_rate_ppm": 66250 },
  "project":       { "name": "..." | null, "due_date_text": "..." | null },
  "bid":           Bid,                 // current authoritative state
  "agent_bid":     Bid | null,          // immutable agent revision 0 (for diffing)
  "diff":          BidDiff | null,      // populated once a final exists
  "tokens_in":     12345,
  "tokens_out":    8901
}
```

`Bid` (SPEC §9.1):

```json
{
  "revision":      1,
  "kind":          "agent | draft | final",
  "generated_by":  "orchestrator | fallback",
  "partial_reasons": [ "..." ],
  "lines":         [ BidLine, ... ],
  "exclusions":    [ { "description":"...", "source": SourceRef } ],
  "labor":         [ { "description":"...", "quantity_text":"...", "unit_raw":"..." } ],
  "unreadable":    [ { "description":"Couldn't read this line (page 4)" } ],
  "conflicts":     [ ConflictView, ... ],
  "assumptions":   [ AssumptionView, ... ],
  "contractor_questions": [ ContractorQuestionView, ... ],
  "summary":       "...",
  "totals": {
    "subtotal_cents": 850000,
    "tax_rate_ppm":   66250,
    "tax_cents":      56313,
    "total_cents":    906313,
    "priced_lines":   22,
    "open_flags":     6
  }
}
```

`BidLine` (SPEC §9.1, abbreviated — fields are snake_case):

```json
{
  "line_id":                    "line_01",
  "item_id":                    "item_12" | null,
  "description":                "2x6x16 PT #2",
  "source":                     SourceRef,
  "requested_quantity_text":    "40",
  "requested_unit_raw":         "pcs",
  "sku":                        "LUM-2X6-16-SYP-2-PTAG" | null,
  "product_name":               "SYP #2 2x6x16 pressure-treated above ground" | null,
  "sale_qty":                   "40" | null,        // Decimal as string (SPEC §9.1)
  "sale_unit":                  "EA",
  "conversion_path":            "40 EA → 40 EA (same unit)" | null,
  "unit_price_cents":           1575 | null,
  "catalog_unit_price_cents":   1575 | null,
  "extended_cents":             63000 | null,
  "stock": {
    "on_hand":                  120,
    "shortfall":                0,
    "next_receipt":             { "date": "2026-12-22", "qty": 240 } | null,
    "transfer_options":         [ TransferOption, ... ]
  },
  "match": {
    "status":                   "matched | substitute | no_match | failed | timeout | skipped",
    "confidence":               "high | medium | low" | null,
    "confidence_reasons":       [ "..." ],
    "assumptions":              [ { "field":"grade", "value":"#2", "reason":"..." } ],
    "is_substitute":            false,
    "substitute_reason":        "..." | null,
    "verify_notes":             [ "sanity_range", "unit_pint_convertible" ]
  },
  "requirements":               [ { "field":"treatment", "value":"pressure_treated_above_ground", "reason":"...", "source": SourceRef } ],
  "review": {
    "state":                    "none | open | resolved",
    "reasons":                  [ "..." ],
    "resolved_by":              "edit | accept | delete" | null
  },
  "origins": {
    "quantity":                 "agent | user_filled | user_overridden",
    "unit_price":               "agent | user_filled | user_overridden",
    "sku":                      "agent | user_filled | user_overridden",
    "description":              "agent | user_filled | user_overridden"
  },
  "override_reason":            "..." | null
}
```

`TransferOption` — SPEC §8.3 `transfer_options_json`, with fields
pinned here:

```json
{
  "branch_id":        "BR-NY-01",
  "branch_name":      "BidDesk Long Island",
  "available_qty":    60,
  "covers_shortfall": true
}
```

Server **sorts** this list, never the client:
1. `covers_shortfall` descending (full-cover branches first).
2. `available_qty` descending.
3. `branch_id` ascending (stable tie-breaker).

### Example line — seed-realistic

A concrete `BidLine` snapshot the UI can render against the real
catalog once Phase 1 lands:

```json
{
  "line_id":                    "line_01",
  "item_id":                    "item_12",
  "description":                "2x6x16 PT #2",
  "source":                     { "page": 3, "quote": "(40) 2x6x16 PT #2", "read_via": "text", "verified": true },
  "requested_quantity_text":    "40",
  "requested_unit_raw":         "EA",
  "sku":                        "LUM-2X6-16-SYP-2-PTAG",
  "product_name":               "SYP #2 2x6x16 pressure-treated above ground",
  "sale_qty":                   "40",
  "sale_unit":                  "EA",
  "conversion_path":            "40 EA → 40 EA (same unit)",
  "unit_price_cents":           1575,
  "catalog_unit_price_cents":   1575,
  "extended_cents":             63000,
  "stock": {
    "on_hand":                  120,
    "shortfall":                0,
    "next_receipt":             { "date": "2026-12-22", "qty": 240 },
    "transfer_options":         []
  },
  "match": {
    "status":        "matched",
    "confidence":    "high",
    "confidence_reasons": [],
    "assumptions":   [],
    "is_substitute": false,
    "substitute_reason": null,
    "verify_notes":  []
  },
  "requirements":   [],
  "review":         { "state": "none", "reasons": [], "resolved_by": null },
  "origins":        { "quantity":"agent", "unit_price":"agent", "sku":"agent", "description":"agent" },
  "override_reason": null
}
```

Restock dates in fixtures are after **2026-12-15** so they stay in
the future for the demo window.

### `GET /api/runs/{id}/events` — persisted events — SPEC §10.3 + §13

Query `?after_id=N` (int). Response: `RunEvent[]` in ascending `id`
order.

Every event carries:

```json
{
  "id":             42,
  "run_id":         "run_...",
  "ts":             "2026-11-10T13:02:11.123Z",
  "type":           "...",
  "agent":          "orchestrator | document_analyst | pricing_agent" | null,
  "span_id":        "..." | null,
  "parent_span_id": "..." | null,
  "payload":        { ... }           // type-specific; table below
}
```

### `GET /api/runs/{id}/stream` — live SSE — SPEC §10.3

Server-Sent Events. **Each event line carries the same integer
`id:` field** used by `/events?after_id=` — so the browser's
built-in `Last-Event-ID` header lets the server resume from exactly
where the client left off (no dedup logic client-side beyond the
usual "ignore if id ≤ lastEventId"). Heartbeat `:` comment every
15 s. Closes after `run.finished`.

Event payloads (type-specific):

| `type`              | payload fields                                                                                                           |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `run.started`       | `input_kind`, `input_name`, `page_count`, `branch_id`                                                                    |
| `run.status`        | `status`, `current_step`                                                                                                 |
| `span.started`      | `name`, `label`                                                                                                          |
| `span.finished`     | `name`, `duration_ms`, `summary?`                                                                                        |
| `span.failed`       | `name`, `duration_ms`, `error_kind` (`timeout \| validation \| transport \| injected \| budget`), `message`, `will_retry` |
| `agent.activity`    | `text` (≤ 200 chars)                                                                                                     |
| `tool.called`       | `tool`, `args_preview` (≤ 300 chars)                                                                                     |
| `tool.result`       | `tool`, `ok`, `result_preview` (≤ 300 chars)                                                                             |
| `retry.scheduled`   | `attempt`, `delay_s`, `reason`                                                                                           |
| `validation.failed` | `boundary`, `errors` (≤ 5), `will_retry`                                                                                 |
| `page.routed`       | `page_no`, `route` (`text \| vision \| text+vision \| unreadable`)                                                       |
| `item.extracted`    | `item` (`ItemView`)                                                                                                      |
| `item.flagged`      | `item_id`, `reason_code`, `message`                                                                                      |
| `item.priced`       | `line` (`PricedLineView`)                                                                                                |
| `conflict.flagged`  | `conflict` (`ConflictView`)                                                                                              |
| `budget.exhausted`  | `budget` (turns / tokens / deadline), `detail`                                                                           |
| `bid.ready`         | `revision`, `generated_by`, `open_flags`                                                                                 |
| `run.finished`      | `status`, `outcome`, `outcome_detail`                                                                                    |

### `GET /api/branches` — `useBranches()` — **present**

Response: `Branch[]` (no wrapper). Static — `staleTime: Infinity`
client-side.

```json
[
  { "id": "BR-NJ-01", "name": "BidDesk North Jersey", "city": "Edison",    "state": "NJ", "tax_rate_ppm": 66250 },
  { "id": "BR-NY-01", "name": "BidDesk Long Island",  "city": "Islip",     "state": "NY", "tax_rate_ppm": 86250 },
  { "id": "BR-PA-01", "name": "BidDesk Lehigh Valley","city": "Allentown", "state": "PA", "tax_rate_ppm": 60000 }
]
```

### `GET /api/samples` — `useSamples()`

Response: `Sample[]` (no wrapper). Static; same caching rule.

### `GET /api/meta` — `useFaults()` (fault-controls subset) — SPEC §10.6, §13

Response body **includes** (there will be other fields):

```json
{
  "demo_faults_enabled": true,
  "faults": [
    {
      "key":        "pricing_timeout_once",
      "title":      "Pricing timeout (once)",
      "description":"First batch exceeds its 45 s timeout; retry succeeds."
    }
  ]
}
```

`useFaults()` returns `{ enabled, faults }`. When `enabled` is
`false`, `DemoFaultControls` renders nothing.

---

## Phase 8 endpoints (bid editing)

### `PATCH /api/runs/{id}/bid` — apply edit ops — SPEC §9.4

**Replaces SPEC's `PATCH /api/runs/{id}/bid/draft`.** Takes a
monotonic `draft_revision`; the server returns the new bid state
and a bumped revision.

```json
// request
{
  "draft_revision": 3,
  "ops": [ EditOp, ... ]
}
// 200 → { "bid": Bid }     (server's new authoritative state, same
//                           shape as GET /api/runs/{id}'s `bid`)
// 409 → stale_revision; details.current = Bid (the server's current
//                           draft so the client can reconcile).
// 422 → invalid_input, with details.ops: per-op error list.
```

`EditOp` is a discriminated union on `op` (snake_case). Full set:

```ts
| { op: "set_qty";          line_id: string; sale_qty: string /* Decimal, per SPEC §9.1 */ }
| { op: "accept_line";      line_id: string }
| { op: "delete_line";      line_id: string }
| { op: "set_unit_price";   line_id: string; unit_price_cents: number; override_reason: string }
| { op: "set_sku";          line_id: string; sku: string } // server re-prices from the catalog row
| { op: "set_description";  line_id: string; description: string }
| { op: "add_line";         description: string; sale_qty: string; sale_unit: string; unit_price_cents: number }
| { op: "apply_proposal";   line_id: string; proposal_id: string }
| { op: "dismiss_proposal"; line_id: string; proposal_id: string }
```

- `set_unit_price` **requires** a non-empty `override_reason`. The
  server records both the new value and the reason in
  `BidLine.override_reason`, flips `origins.unit_price` to
  `user_overridden`, and keeps `catalog_unit_price_cents` untouched
  so the audit trail survives.
- `set_sku` **re-prices server-side**: the server reads the new
  SKU's catalog row and rewrites `product_name`, `sale_unit`,
  `unit_price_cents`, `catalog_unit_price_cents`, `extended_cents`,
  and `stock` (shortfall + transfer options recomputed).
- `delete_line` removes the line from `lines` and from totals;
  `review.resolved_by = "delete"` isn't surfaced after the op (the
  line is gone) — the audit lives in `bid_edits`.
- `apply_proposal` consumes a proposal returned by
  `POST /api/runs/{id}/bid/lines/{line_id}/reprice`.
- `dismiss_proposal` tells the server the user considered and
  rejected the proposal.

Ops apply in order; any single invalid op returns `422` and NO ops
are applied. Totals recompute server-side; the response is
authoritative.

### `POST /api/runs/{id}/bid/lines/{line_id}/reprice` — SPEC §9.5

```json
// request: {}
// 200
{
  "proposal_id": "prop_xyz",
  "before": PricedLineView,
  "after":  PricedLineView
}
```

Nothing applied until an `apply_proposal` op lands in a `PATCH`
body. `dismiss_proposal` is the no-op alternative.

### `POST /api/runs/{id}/bid/finalize` — SPEC §9.6

```json
// request
{ "draft_revision": 4 }
// 200 → { "bid": Bid }   (the final, immutable, same shape)
// 409 → stale_revision
// 422 → open_flags, with details.open_line_ids: ["line_02", …]
```

Idempotent: finalizing an already-finalized revision returns the
existing final.

---

## Resolved questions

Previously **OPEN**, now closed:

1. **Error code enumeration** → table above is canonical.
2. **Pagination** → opaque cursor, `limit` default **20** / max **100**.
3. **`POST /api/runs` file/text** → one-of (both or neither → `invalid_input`).
4. **Run detail envelope** → single `bid` key (current authoritative state) plus `agent_bid` + `diff` on the same response. Deviates from SPEC §13's `GET /api/runs/{id}/bid` — negotiation item at Phase 5 merge.
5. **`EditOp` discriminator strings** → snake_case, full op union pinned above (`set_qty`, `accept_line`, `delete_line`, `set_unit_price`, `set_sku`, `set_description`, `add_line`, `apply_proposal`, `dismiss_proposal`).
6. **Separate `GET /api/runs/{id}/bid`** → no. Everything lives under `GET /api/runs/{id}`.
7. **Tax formula** → half-up on `subtotal_cents × tax_rate_ppm / 1_000_000`, once per bid (see "Totals + tax" above).
8. **`transfer_options` field set + sort** → `{ branch_id, branch_name, available_qty, covers_shortfall }`, sorted by `covers_shortfall` desc → `available_qty` desc → `branch_id` asc.
