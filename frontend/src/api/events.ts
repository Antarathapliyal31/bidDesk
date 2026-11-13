/**
 * Run-event + embedded view types. Sourced from SPEC §10.3 and the
 * backend `contracts/events.py` / `contracts/items.py` /
 * `contracts/pricing.py` (Phase 2+; not on this branch yet).
 *
 * **Hand-synced until Phase 5 generates the TS types from the
 * backend schema.** The source of truth is backend Pydantic; this
 * file mirrors it. When the backend contract files land in Phase 2,
 * add an identical "hand-synced" note to each of them so any future
 * contributor knows these two shapes must move together until
 * `make gen-api` is wired up.
 *
 * Call sites (features/, lib/runReducer.ts, dev-fixtures/sse/*)
 * import from `@/api`, never from here directly.
 */

import type { InputKind, Outcome, RunStatus } from "./useRuns";

export type AgentName = "orchestrator" | "document_analyst" | "pricing_agent";

export type PageRoute = "text" | "vision" | "text+vision" | "unreadable";

export type ItemKind = "material" | "labor" | "exclusion" | "note" | "unreadable";

/** SPEC §9.1 — the one source-citation record for every quoted claim. */
export type SourceRef = {
  page: number | null;
  quote: string | null;
  read_via: "text" | "vision" | null;
  verified: boolean;
};

/**
 * Payload of `item.extracted` (SPEC §10.3 / §6.4). `source: SourceRef`
 * replaces the old loose `page / verified / read_via` fields.
 *
 * `notes` current enumeration (SPEC §6.4):
 *   - `quote_not_found`           — bullet 1; item kept, `source.verified = false`.
 *   - `quantity_not_in_quote`     — bullet 2; item kept, flagged for review.
 *   - `unknown_unit`              — bullet 3; `unit_raw` present but not recognised.
 *   - `unit_pint_convertible`     — bullet 3; pricing will convert via pint.
 *   - `invalid_spec:<field>`      — bullet 5; `<field>` is the dropped spec name.
 *
 * Only `add_requirements` entries and `flag_conflict` sides with
 * failed quote verification are rejected server-side (→
 * `rejected_claims`); items themselves survive with
 * `source.verified = false` and the `quote_not_found` note so the
 * estimator can accept or correct them.
 */
export type ItemView = {
  id: string;
  kind: ItemKind;
  description: string;
  quantity_text: string | null;
  unit_raw: string | null;
  source: SourceRef;
  notes: string[];
};

/** SPEC §8.3 — transfer suggestions when a line has a shortfall at the chosen branch. */
export type TransferOption = {
  branch_id: string;
  branch_name: string;
  available_qty: number;
  covers_shortfall: boolean;
};

/**
 * Payload of `item.priced` (SPEC §10.3 + §9.1 BidLine match sub-shape).
 * `is_substitute` + `substitute_reason` ride along so the Live view's
 * substitute badge appears as soon as the pricing agent commits —
 * waiting until `bid.ready` would hide that status for minutes on
 * a messy run.
 */
export type PricedLineView = {
  item_id: string;
  status:
    | "matched"
    | "substitute"
    | "no_match"
    | "failed"
    | "timeout"
    | "skipped"
    | "needs_review";
  sku: string | null;
  product_name: string | null;
  sale_qty: string | null; // Decimal as string per SPEC §9.1
  sale_unit: string | null;
  unit_price_cents: number | null;
  extended_cents: number | null;
  conversion_path: string | null;
  confidence: "high" | "medium" | "low" | null;
  confidence_reasons: string[];
  shortfall: number | null;
  is_substitute: boolean;
  substitute_reason: string | null;
};

/** One side of a `conflict.flagged` payload. */
export type ConflictSide = {
  item_id: string | null;
  source: SourceRef;
};

export type ConflictView = {
  id: string;
  type: "size_mismatch" | "excluded_but_listed" | "spec_vs_list" | "stock_vs_delivery";
  side_a: ConflictSide;
  side_b: ConflictSide;
  explanation: string;
};

export type BudgetNotice = { budget: "turns" | "tokens" | "deadline"; detail: string };

type EventBase = {
  id: number;
  run_id: string;
  ts: string;
  agent: AgentName | null;
  span_id: string | null;
  parent_span_id: string | null;
};

export type RunEvent =
  | (EventBase & {
      type: "run.started";
      payload: {
        input_kind: InputKind;
        input_name: string;
        page_count: number | null;
        branch_id: string;
      };
    })
  | (EventBase & {
      type: "run.status";
      payload: { status: RunStatus; current_step: string | null };
    })
  | (EventBase & { type: "span.started"; payload: { name: string; label: string | null } })
  | (EventBase & {
      type: "span.finished";
      payload: { name: string; duration_ms: number; summary: string | null };
    })
  | (EventBase & {
      type: "span.failed";
      payload: {
        name: string;
        duration_ms: number;
        error_kind: "timeout" | "validation" | "transport" | "injected" | "budget";
        message: string;
        will_retry: boolean;
      };
    })
  | (EventBase & { type: "agent.activity"; payload: { text: string } })
  | (EventBase & { type: "tool.called"; payload: { tool: string; args_preview: string } })
  | (EventBase & {
      type: "tool.result";
      payload: { tool: string; ok: boolean; result_preview: string };
    })
  | (EventBase & {
      type: "retry.scheduled";
      payload: { attempt: number; delay_s: number; reason: string };
    })
  | (EventBase & {
      type: "validation.failed";
      payload: { boundary: string; errors: string[]; will_retry: boolean };
    })
  | (EventBase & { type: "page.routed"; payload: { page_no: number; route: PageRoute } })
  | (EventBase & { type: "item.extracted"; payload: { item: ItemView } })
  | (EventBase & {
      type: "item.flagged";
      payload: { item_id: string; reason_code: string; message: string | null };
    })
  | (EventBase & { type: "item.priced"; payload: { line: PricedLineView } })
  | (EventBase & { type: "conflict.flagged"; payload: { conflict: ConflictView } })
  | (EventBase & {
      type: "budget.exhausted";
      payload: { budget: "turns" | "tokens" | "deadline"; detail: string };
    })
  | (EventBase & {
      type: "bid.ready";
      payload: {
        revision: number;
        generated_by: "orchestrator" | "fallback";
        open_flags: number;
      };
    })
  | (EventBase & {
      type: "run.finished";
      payload: {
        status: RunStatus;
        outcome: Outcome | null;
        outcome_detail: string | null;
      };
    });

export type RunEventType = RunEvent["type"];

/**
 * Every event-type string the backend may emit on `/stream`.
 * Enumerated so `useRunStream` can `addEventListener` per name —
 * the backend sends NAMED SSE frames (`event: run.started`, etc.)
 * and `EventSource.onmessage` only fires for the unnamed default
 * event. One shared handler dispatches each parsed frame into
 * the reducer. Keep this list in sync with the `RunEvent` union
 * above (compile-time check in `satisfiesAll` below).
 */
export const RUN_EVENT_TYPES: readonly RunEventType[] = [
  "run.started",
  "run.status",
  "span.started",
  "span.finished",
  "span.failed",
  "agent.activity",
  "tool.called",
  "tool.result",
  "retry.scheduled",
  "validation.failed",
  "page.routed",
  "item.extracted",
  "item.flagged",
  "item.priced",
  "conflict.flagged",
  "budget.exhausted",
  "bid.ready",
  "run.finished",
] as const;

// Compile-time proof the list above covers every RunEventType.
// If a new case joins the union without a matching entry here,
// `_Missing` resolves to that case name and the assignment fails.
type _Missing = Exclude<RunEventType, (typeof RUN_EVENT_TYPES)[number]>;
const _RUN_EVENT_TYPES_EXHAUSTIVE: _Missing extends never ? true : _Missing = true;
void _RUN_EVENT_TYPES_EXHAUSTIVE;
