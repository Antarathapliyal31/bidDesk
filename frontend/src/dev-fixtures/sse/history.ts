/**
 * Synthetic event streams for the history fixture rows
 * (`FIXTURE_RUNS`). Clicking an existing run in History / Sidebar
 * lands on `/runs/<row.id>`; useRunStream drains this list
 * synchronously so the page shows that row's final state
 * instantly — its name, its status, its line and flag counts, no
 * live-replay animation.
 *
 * Each builder below targets a specific terminal state. The event
 * list is intentionally small: just enough events to reach the
 * right counts through the reducer. In Phase 5 the backend's
 * `GET /api/runs/{id}/events?after_id=0` replaces all of this; the
 * useRunStream initializer still drains that into the reducer, so
 * the reducer contract doesn't move.
 */
import type { FxRunSummary } from "../types";
import type { RunEvent } from "@/api";

const RUN_STARTED_SECOND = 0;

/** Add `secondsFromStart` to `base` and return an ISO string. */
function addSeconds(baseIso: string, seconds: number): string {
  const d = new Date(baseIso);
  d.setUTCSeconds(d.getUTCSeconds() + seconds);
  return d.toISOString();
}

type IdGen = { nextId: () => number };
function idGen(): IdGen {
  let i = 0;
  return { nextId: () => ++i };
}

/**
 * Minimal viable item.extracted payload. Description / quantity
 * are intentionally generic — the history row's counts are what
 * matters; the descriptions here are only shown if the user
 * expands the items list on a historical run.
 */
function extractedEvent(
  gen: IdGen,
  runId: string,
  ts: string,
  itemId: string,
  description: string,
  qty: string,
): RunEvent {
  return {
    id: gen.nextId(),
    run_id: runId,
    ts,
    agent: "document_analyst",
    span_id: "s_extract",
    parent_span_id: null,
    type: "item.extracted",
    payload: {
      item: {
        id: itemId,
        kind: "material",
        description,
        quantity_text: qty,
        unit_raw: "pcs",
        source: { page: 1, quote: null, read_via: "text", verified: true },
        notes: [],
      },
    },
  };
}

function pricedEvent(
  gen: IdGen,
  runId: string,
  ts: string,
  itemId: string,
): RunEvent {
  return {
    id: gen.nextId(),
    run_id: runId,
    ts,
    agent: "pricing_agent",
    span_id: "s_pricing",
    parent_span_id: "s_orch",
    type: "item.priced",
    payload: {
      line: {
        item_id: itemId,
        status: "matched",
        sku: null,
        product_name: null,
        sale_qty: "10",
        sale_unit: "pcs",
        unit_price_cents: 1250,
        extended_cents: 12_500,
        confidence: "high",
        confidence_reasons: [],
        shortfall: null,
        is_substitute: false,
        substitute_reason: null,
        conversion_path: null,
      },
    },
  };
}

function flaggedEvent(gen: IdGen, runId: string, ts: string, itemId: string): RunEvent {
  return {
    id: gen.nextId(),
    run_id: runId,
    ts,
    agent: "orchestrator",
    span_id: "s_orch",
    parent_span_id: null,
    type: "item.flagged",
    payload: {
      item_id: itemId,
      reason_code: "quantity_not_in_quote",
      message: null,
    },
  };
}

function branchIdFromName(name: string): string {
  if (name.includes("North Jersey")) return "BR-NJ-01";
  if (name.includes("Long Island")) return "BR-NY-01";
  if (name.includes("Lehigh")) return "BR-PA-01";
  return "BR-NJ-01";
}

/**
 * Build the event list that, when drained through `runReducer`,
 * produces the terminal state described by this history row.
 * One function, one switch on `status` — easier to read and
 * audit than a chain of helpers per status.
 */
export function historyRunEvents(run: FxRunSummary): RunEvent[] {
  const gen = idGen();
  const events: RunEvent[] = [];
  const base = run.created_at;
  const ts = (secondsFromStart: number): string => addSeconds(base, secondsFromStart);

  // Every run has a `run.started`.
  events.push({
    id: gen.nextId(),
    run_id: run.id,
    ts: ts(RUN_STARTED_SECOND),
    agent: null,
    span_id: null,
    parent_span_id: null,
    type: "run.started",
    payload: {
      input_kind: run.input_kind,
      input_name: run.input_name,
      // No project_name here — the backend can't know it at the
      // ingest event (analyst hasn't read the PDF). The RunHeader
      // sources project_name from `useRunSummary(runId)` instead
      // (GET /api/runs/:id in prod, FIXTURE_RUNS lookup in fixture
      // mode). See DECISIONS F9.
      page_count: run.input_kind === "pdf" ? 8 : 0,
      branch_id: branchIdFromName(run.branch_name),
    },
  });

  // Failed run — analyst couldn't open the PDF. Short-circuit the
  // rest.
  if (run.status === "failed") {
    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(1),
      agent: "document_analyst",
      span_id: "s_extract",
      parent_span_id: null,
      type: "span.started",
      payload: { name: "analyst.extract", label: "Reading pages" },
    });
    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(2),
      agent: "document_analyst",
      span_id: "s_extract",
      parent_span_id: null,
      type: "span.failed",
      payload: {
        name: "analyst.extract",
        duration_ms: 1_000,
        error_kind: "transport",
        message: "Could not read PDF (corrupt).",
        will_retry: false,
      },
    });
    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(3),
      agent: null,
      span_id: null,
      parent_span_id: null,
      type: "run.finished",
      payload: {
        status: run.status,
        outcome: run.outcome ?? "error",
        outcome_detail: null,
      },
    });
    return events;
  }

  // Analyst span open.
  events.push({
    id: gen.nextId(),
    run_id: run.id,
    ts: ts(1),
    agent: "document_analyst",
    span_id: "s_extract",
    parent_span_id: null,
    type: "span.started",
    payload: {
      name: "analyst.extract",
      label: run.input_kind === "pdf" ? "Reading pages" : "Parsing list",
    },
  });

  const totalItems =
    run.status === "running"
      ? Math.max(1, run.priced_lines) // show some items mid-run
      : run.priced_lines + run.open_flags;

  // Emit the items the analyst extracted.
  for (let i = 1; i <= totalItems; i++) {
    events.push(
      extractedEvent(gen, run.id, ts(2), `h_${run.id}_item_${i}`, `Item ${i}`, "10"),
    );
  }

  // Analyst finishes unless the run is still "running".
  if (run.status !== "running") {
    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(3),
      agent: "document_analyst",
      span_id: "s_extract",
      parent_span_id: null,
      type: "span.finished",
      payload: {
        name: "analyst.extract",
        duration_ms: 2_000,
        summary: `${totalItems} items`,
      },
    });
  }

  // Orchestrator + pricing. "running" runs stop before this.
  if (run.status !== "running") {
    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(4),
      agent: "orchestrator",
      span_id: "s_orch",
      parent_span_id: null,
      type: "span.started",
      payload: { name: "orchestrator.loop", label: "Triage + price" },
    });
    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(5),
      agent: "pricing_agent",
      span_id: "s_pricing",
      parent_span_id: "s_orch",
      type: "span.started",
      payload: { name: "pricing_agent.batch", label: `Pricing ${run.priced_lines} lines` },
    });

    // Price the first N items to match row.priced_lines.
    for (let i = 1; i <= run.priced_lines; i++) {
      events.push(pricedEvent(gen, run.id, ts(5), `h_${run.id}_item_${i}`));
    }

    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(6),
      agent: "pricing_agent",
      span_id: "s_pricing",
      parent_span_id: "s_orch",
      type: "span.finished",
      payload: {
        name: "pricing_agent.batch",
        duration_ms: 2_000,
        summary: `${run.priced_lines} lines priced`,
      },
    });

    // Open flags on the remaining items.
    for (let i = 1; i <= run.open_flags; i++) {
      events.push(
        flaggedEvent(
          gen,
          run.id,
          ts(7),
          `h_${run.id}_item_${run.priced_lines + i}`,
        ),
      );
    }

    // Close orchestrator span unless interrupted (SPEC §10.5: the
    // interrupted lifespan arrives as run.finished with status
    // "interrupted"; the reducer closes still-running spans itself).
    if (run.status !== "interrupted") {
      events.push({
        id: gen.nextId(),
        run_id: run.id,
        ts: ts(8),
        agent: "orchestrator",
        span_id: "s_orch",
        parent_span_id: null,
        type: "span.finished",
        payload: {
          name: "orchestrator.loop",
          duration_ms: 4_000,
          summary: "triaged + priced",
        },
      });
    }

    // bid.ready only when a bid was actually produced (completed or
    // partial — not failed/interrupted/no_materials-outcome).
    if (
      (run.status === "completed" && run.outcome === "bid_ready") ||
      run.status === "partial"
    ) {
      events.push({
        id: gen.nextId(),
        run_id: run.id,
        ts: ts(9),
        agent: null,
        span_id: null,
        parent_span_id: null,
        type: "bid.ready",
        payload: {
          revision: 0,
          generated_by: run.status === "partial" ? "fallback" : "orchestrator",
          open_flags: run.open_flags,
        },
      });
    }

    events.push({
      id: gen.nextId(),
      run_id: run.id,
      ts: ts(10),
      agent: null,
      span_id: null,
      parent_span_id: null,
      type: "run.finished",
      payload: {
        status: run.status,
        outcome: run.outcome ?? null,
        outcome_detail: null,
      },
    });
  }

  return events;
}
