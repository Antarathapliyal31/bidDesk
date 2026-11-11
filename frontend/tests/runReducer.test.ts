// @vitest-environment node
/**
 * Exhaustive tests for `runReducer` (SPEC §11.3). Grouped by what
 * each test proves.
 */
import { describe, expect, it } from "vitest";

import type { RunEvent } from "@/api";

import { initialRunViewState, runReducer, type RunViewState } from "../src/lib/runReducer";

function reduceAll(events: RunEvent[], start = initialRunViewState): RunViewState {
  return events.reduce((s, e) => runReducer(s, e), start);
}

const EB = {
  run_id: "run_1",
  ts: "2026-11-11T00:00:00Z",
  agent: null,
  span_id: null,
  parent_span_id: null,
} as const;

function runStarted(id: number): RunEvent {
  return {
    ...EB,
    id,
    type: "run.started",
    payload: {
      input_kind: "pdf",
      input_name: "rfp_messy.pdf",
      page_count: 8,
      branch_id: "BR-NJ-01",
    },
  };
}

function spanStarted(
  id: number,
  spanId: string,
  parentId: string | null,
  agent: RunEvent["agent"],
  name: string,
  ts = "2026-11-11T00:00:01Z",
): RunEvent {
  return {
    ...EB,
    id,
    ts,
    agent,
    span_id: spanId,
    parent_span_id: parentId,
    type: "span.started",
    payload: { name, label: null },
  };
}

function spanFinished(
  id: number,
  spanId: string,
  agent: RunEvent["agent"],
  durationMs = 100,
): RunEvent {
  return {
    ...EB,
    id,
    agent,
    span_id: spanId,
    type: "span.finished",
    payload: { name: "", duration_ms: durationMs, summary: null },
  };
}

function spanFailed(
  id: number,
  spanId: string,
  agent: RunEvent["agent"],
  will_retry: boolean,
  error_kind: "timeout" | "validation" | "transport" | "injected" | "budget" = "timeout",
): RunEvent {
  return {
    ...EB,
    id,
    agent,
    span_id: spanId,
    type: "span.failed",
    payload: { name: "", duration_ms: 100, error_kind, message: "boom", will_retry },
  };
}

// ─── Idempotency ───────────────────────────────────────────────────

describe("runReducer — idempotency", () => {
  it("ignores events whose id <= lastEventId", () => {
    const after = runReducer(initialRunViewState, runStarted(5));
    expect(after.lastEventId).toBe(5);
    const re = runReducer(after, runStarted(3));
    expect(re).toBe(after);
  });

  it("ignores a replayed event with the same id", () => {
    const e = runStarted(1);
    const after = runReducer(initialRunViewState, e);
    expect(runReducer(after, e)).toBe(after);
  });

  it("accepts strictly increasing ids", () => {
    const state = reduceAll([runStarted(1), runStarted(2), runStarted(3)]);
    expect(state.lastEventId).toBe(3);
  });
});

// ─── Unknown event (point 1) ───────────────────────────────────────

describe("runReducer — unknown event type", () => {
  it("advances lastEventId, doesn't throw, leaves the rest of state unchanged", () => {
    const after = runReducer(initialRunViewState, runStarted(1));
    const unknown = {
      ...EB,
      id: 2,
      type: "some.future_event",
      payload: { foo: "bar" },
    } as unknown as RunEvent;
    const next = runReducer(after, unknown);
    expect(next.lastEventId).toBe(2);
    const { lastEventId: _a, ...restNext } = next;
    const { lastEventId: _b, ...restAfter } = after;
    void _a;
    void _b;
    expect(restNext).toStrictEqual(restAfter);
  });
});

// ─── run.started / run.status (point 6) ────────────────────────────

describe("runReducer — run.started + run.status", () => {
  it("captures input, page count, branch, startedAtTs", () => {
    const state = runReducer(initialRunViewState, runStarted(1));
    expect(state.inputKind).toBe("pdf");
    expect(state.inputName).toBe("rfp_messy.pdf");
    expect(state.pageCount).toBe(8);
    expect(state.branchId).toBe("BR-NJ-01");
    expect(state.startedAtTs).toBe("2026-11-11T00:00:00Z");
  });

  it("run.status with current_step: null stores null, not the string 'null'", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "run.status",
        payload: { status: "running", current_step: null },
      },
    ]);
    expect(state.status).toBe("running");
    expect(state.currentStep).toBe(null);
  });

  it("captures run.status with a current_step string", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "run.status",
        payload: { status: "running", current_step: "price" },
      },
    ]);
    expect(state.currentStep).toBe("price");
  });
});

// ─── Span lifecycle (same-agent) ───────────────────────────────────

describe("runReducer — span lifecycle (same-agent)", () => {
  it("starts a root span and tracks the lane as working on it", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "document_analyst", "extract"),
    ]);
    expect(state.rootSpanIds).toEqual(["s1"]);
    expect(state.spans["s1"]?.state).toBe("running");
    expect(state.agents.document_analyst.state).toBe("working");
    expect(state.agents.document_analyst.activeSpanId).toBe("s1");
  });

  it("span.finished returns the lane to idle when no ancestor remains", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "document_analyst", "extract"),
      spanFinished(3, "s1", "document_analyst"),
    ]);
    expect(state.spans["s1"]?.state).toBe("finished");
    expect(state.agents.document_analyst.state).toBe("idle");
    expect(state.agents.document_analyst.activeSpanId).toBe(null);
  });

  it("nested span.finished restores the lane to its running parent (no flicker)", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "parent", null, "document_analyst", "extract"),
      spanStarted(3, "child", "parent", "document_analyst", "verify"),
      spanFinished(4, "child", "document_analyst"),
    ]);
    expect(state.spans["parent"]?.state).toBe("running");
    expect(state.spans["child"]?.state).toBe("finished");
    expect(state.agents.document_analyst.state).toBe("working");
    expect(state.agents.document_analyst.activeSpanId).toBe("parent");
  });

  it("retryable span.failed (will_retry=true) leaves the lane working", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "pricing_agent", "batch"),
      spanFailed(3, "s1", "pricing_agent", /*will_retry*/ true),
    ]);
    expect(state.agents.pricing_agent.state).toBe("working");
    expect(state.agents.pricing_agent.errorKind).toBe(null);
    expect(state.spans["s1"]?.state).toBe("failed");
    expect(state.spans["s1"]?.willRetry).toBe(true);
  });
});

// ─── Terminal child failure with running ancestor (point 2 + 5) ─────

describe("runReducer — terminal child failure (same agent)", () => {
  it("running parent exists → lane stays 'working', records errorKind", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "parent", null, "pricing_agent", "batch"),
      spanStarted(3, "child", "parent", "pricing_agent", "query_catalog"),
      spanFailed(4, "child", "pricing_agent", /*will_retry*/ false, "transport"),
    ]);
    expect(state.spans["parent"]?.state).toBe("running");
    expect(state.spans["child"]?.state).toBe("failed");
    expect(state.agents.pricing_agent.state).toBe("working");
    expect(state.agents.pricing_agent.activeSpanId).toBe("parent");
    expect(state.agents.pricing_agent.errorKind).toBe("transport");
  });

  it("then parent finishes → lane idle, errorKind KEPT (finished with errors)", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "parent", null, "pricing_agent", "batch"),
      spanStarted(3, "child", "parent", "pricing_agent", "query_catalog"),
      spanFailed(4, "child", "pricing_agent", /*will_retry*/ false, "transport"),
      spanFinished(5, "parent", "pricing_agent"),
    ]);
    expect(state.agents.pricing_agent.state).toBe("idle");
    expect(state.agents.pricing_agent.activeSpanId).toBe(null);
    expect(state.agents.pricing_agent.errorKind).toBe("transport");
  });

  it("no running ancestor → lane flips to 'failed'", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "pricing_agent", "batch"),
      spanFailed(3, "s1", "pricing_agent", /*will_retry*/ false, "timeout"),
    ]);
    expect(state.agents.pricing_agent.state).toBe("failed");
    expect(state.agents.pricing_agent.errorKind).toBe("timeout");
    expect(state.agents.pricing_agent.activeSpanId).toBe(null);
  });
});

// ─── Cross-agent nesting (point 2) ─────────────────────────────────

describe("runReducer — cross-agent nesting", () => {
  it("pricing child finished under orchestrator parent → pricing idle, orchestrator still working on parent", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "o1", null, "orchestrator", "triage"),
      spanStarted(3, "p1", "o1", "pricing_agent", "price_items"),
      spanFinished(4, "p1", "pricing_agent"),
    ]);
    expect(state.agents.pricing_agent.state).toBe("idle");
    expect(state.agents.pricing_agent.activeSpanId).toBe(null);
    expect(state.agents.orchestrator.state).toBe("working");
    expect(state.agents.orchestrator.activeSpanId).toBe("o1");
  });

  it("pricing child terminally failed under orchestrator parent → pricing 'failed', orchestrator still working", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "o1", null, "orchestrator", "triage"),
      spanStarted(3, "p1", "o1", "pricing_agent", "price_items"),
      spanFailed(4, "p1", "pricing_agent", /*will_retry*/ false, "timeout"),
    ]);
    // Ancestor o1 is a DIFFERENT agent → pricing has no running
    // same-agent ancestor → lane goes "failed".
    expect(state.agents.pricing_agent.state).toBe("failed");
    expect(state.agents.pricing_agent.errorKind).toBe("timeout");
    expect(state.agents.pricing_agent.activeSpanId).toBe(null);
    expect(state.agents.orchestrator.state).toBe("working");
    expect(state.agents.orchestrator.activeSpanId).toBe("o1");
  });
});

// ─── Retry then success (point 4) ─────────────────────────────────

describe("runReducer — retry then success", () => {
  it("s1 fail (will_retry=true) → s2 success → lane idle, errorKind null", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "pricing_agent", "batch"),
      spanFailed(3, "s1", "pricing_agent", /*will_retry*/ true, "transport"),
      spanStarted(4, "s2", null, "pricing_agent", "batch"),
      spanFinished(5, "s2", "pricing_agent"),
    ]);
    expect(state.agents.pricing_agent.state).toBe("idle");
    expect(state.agents.pricing_agent.activeSpanId).toBe(null);
    expect(state.agents.pricing_agent.errorKind).toBe(null);
  });
});

// ─── agent.activity / tool / retry / validation ─────────────────────

describe("runReducer — activity, tool, retry, validation", () => {
  it("updates agent activity string", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        agent: "orchestrator",
        type: "agent.activity",
        payload: { text: "Triaging items" },
      },
    ]);
    expect(state.agents.orchestrator.activity).toBe("Triaging items");
  });

  it("tool.called + tool.result attach to the same span", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "orchestrator", "tool_loop"),
      {
        ...EB,
        id: 3,
        agent: "orchestrator",
        span_id: "s1",
        type: "tool.called",
        payload: { tool: "classify_items", args_preview: "{...}" },
      },
      {
        ...EB,
        id: 4,
        agent: "orchestrator",
        span_id: "s1",
        type: "tool.result",
        payload: { tool: "classify_items", ok: true, result_preview: "ok" },
      },
    ]);
    expect(state.spans["s1"]?.tool?.name).toBe("classify_items");
    expect(state.spans["s1"]?.tool?.ok).toBe(true);
    expect(state.spans["s1"]?.tool?.resultPreview).toBe("ok");
  });

  it("retry.scheduled + validation.failed attach to the span", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "pricing_agent", "batch"),
      {
        ...EB,
        id: 3,
        agent: "pricing_agent",
        span_id: "s1",
        type: "retry.scheduled",
        payload: { attempt: 1, delay_s: 1, reason: "429" },
      },
      {
        ...EB,
        id: 4,
        agent: "pricing_agent",
        span_id: "s1",
        type: "validation.failed",
        payload: {
          boundary: "pricing_agent→orchestrator",
          errors: ["bad qty"],
          will_retry: true,
        },
      },
    ]);
    expect(state.spans["s1"]?.retry?.attempt).toBe(1);
    expect(state.spans["s1"]?.validation?.errors).toEqual(["bad qty"]);
  });
});

// ─── Items, pages, flags, conflicts (incl. point 3) ────────────────

describe("runReducer — items, pages, flags, conflicts", () => {
  it("page.routed indexes pages by number", () => {
    const state = reduceAll([
      runStarted(1),
      { ...EB, id: 2, type: "page.routed", payload: { page_no: 7, route: "text+vision" } },
    ]);
    expect(state.pagesByNo[7]?.route).toBe("text+vision");
  });

  it("item.extracted stores an item and preserves order; item.priced merges the line", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "item.extracted",
        payload: {
          item: {
            id: "item_1",
            kind: "material",
            description: "2x4 x 8' SPF #2",
            quantity_text: "100",
            unit_raw: "pcs",
            source: { page: 2, quote: "(100) 2x4 x 8'", read_via: "text", verified: true },
            notes: [],
          },
        },
      },
      {
        ...EB,
        id: 3,
        type: "item.priced",
        payload: {
          line: {
            item_id: "item_1",
            status: "matched",
            sku: "LUM-2X4-8-SPF-2",
            product_name: "SPF #2 2x4x8",
            sale_qty: "100",
            sale_unit: "EA",
            unit_price_cents: 325,
            extended_cents: 32500,
            conversion_path: "100 pcs → 100 EA (same unit)",
            confidence: "high",
            confidence_reasons: [],
            shortfall: 0,
            is_substitute: false,
            substitute_reason: null,
          },
        },
      },
    ]);
    expect(state.itemOrder).toEqual(["item_1"]);
    expect(state.items["item_1"]?.description).toBe("2x4 x 8' SPF #2");
    expect(state.items["item_1"]?.priced?.sku).toBe("LUM-2X4-8-SPF-2");
    expect(state.items["item_1"]?.verified).toBe(true);
  });

  it("item with a failed quote (verified=false, notes includes 'quote_not_found') is stored (SPEC §6.4)", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "item.extracted",
        payload: {
          item: {
            id: "item_x",
            kind: "material",
            description: "2x6 PT",
            quantity_text: "40",
            unit_raw: "pcs",
            source: {
              page: 3,
              quote: "(40) 2x6 PT",
              read_via: "text",
              verified: false,
            },
            notes: ["quote_not_found"],
          },
        },
      },
    ]);
    const item = state.items["item_x"];
    expect(item).toBeDefined();
    expect(item?.verified).toBe(false);
    expect(item?.notes).toEqual(["quote_not_found"]);
    expect(state.itemOrder).toEqual(["item_x"]);
  });

  it("item.extracted dedups by id", () => {
    const extract = {
      ...EB,
      id: 2,
      type: "item.extracted" as const,
      payload: {
        item: {
          id: "item_1",
          kind: "material" as const,
          description: "x",
          quantity_text: "1",
          unit_raw: "EA",
          source: { page: 1, quote: "x", read_via: "text" as const, verified: true },
          notes: [],
        },
      },
    };
    const state = reduceAll([runStarted(1), extract, { ...extract, id: 3 }]);
    expect(state.itemOrder).toEqual(["item_1"]);
  });

  it("item.priced is ignored when the item isn't yet seen", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "item.priced",
        payload: {
          line: {
            item_id: "item_nope",
            status: "matched",
            sku: null,
            product_name: null,
            sale_qty: null,
            sale_unit: null,
            unit_price_cents: null,
            extended_cents: null,
            conversion_path: null,
            confidence: null,
            confidence_reasons: [],
            shortfall: null,
            is_substitute: false,
            substitute_reason: null,
          },
        },
      },
    ]);
    expect(state.items["item_nope"]).toBeUndefined();
  });

  it("item.flagged pushes to flags + per-item flags", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "item.extracted",
        payload: {
          item: {
            id: "item_1",
            kind: "material",
            description: "x",
            quantity_text: null,
            unit_raw: null,
            source: { page: null, quote: null, read_via: null, verified: false },
            notes: [],
          },
        },
      },
      {
        ...EB,
        id: 3,
        type: "item.flagged",
        payload: { item_id: "item_1", reason_code: "quantity_not_in_quote", message: null },
      },
    ]);
    expect(state.flags).toHaveLength(1);
    expect(state.items["item_1"]?.flags[0]?.reason_code).toBe("quantity_not_in_quote");
  });

  it("conflict.flagged pushes to conflicts", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "conflict.flagged",
        payload: {
          conflict: {
            id: "c1",
            type: "size_mismatch",
            side_a: {
              item_id: null,
              source: { page: 3, quote: "7/16 OSB", read_via: "text", verified: true },
            },
            side_b: {
              item_id: null,
              source: { page: 8, quote: "1/2 OSB", read_via: "text", verified: true },
            },
            explanation: "OSB thickness conflict",
          },
        },
      },
    ]);
    expect(state.conflicts).toHaveLength(1);
    expect(state.conflicts[0]?.type).toBe("size_mismatch");
  });
});

// ─── run.finished: completed / partial / failed (point 9) ──────────

describe("runReducer — run.finished", () => {
  it("completed: still-running spans → 'finished', working lanes → 'done'", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "orchestrator", "triage", "2026-11-11T00:00:00Z"),
      spanStarted(3, "s2", null, "pricing_agent", "batch", "2026-11-11T00:00:05Z"),
      {
        ...EB,
        id: 4,
        ts: "2026-11-11T00:00:10Z",
        type: "run.finished",
        payload: { status: "completed", outcome: "bid_ready", outcome_detail: null },
      },
    ]);
    expect(state.spans["s1"]?.state).toBe("finished");
    expect(state.spans["s2"]?.state).toBe("finished");
    expect(state.spans["s1"]?.durationMs).toBe(10_000);
    expect(state.spans["s2"]?.durationMs).toBe(5_000);
    expect(state.agents.orchestrator.state).toBe("done");
    expect(state.agents.pricing_agent.state).toBe("done");
    expect(state.status).toBe("completed");
    expect(state.outcome).toBe("bid_ready");
  });

  it("partial: still-running spans → 'interrupted', working lanes → 'interrupted' (durations computed)", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "orchestrator", "triage", "2026-11-11T00:00:00Z"),
      spanStarted(3, "s2", null, "pricing_agent", "batch", "2026-11-11T00:00:03Z"),
      {
        ...EB,
        id: 4,
        ts: "2026-11-11T00:00:10Z",
        type: "run.finished",
        payload: { status: "partial", outcome: "budget_exhausted", outcome_detail: null },
      },
    ]);
    expect(state.spans["s1"]?.state).toBe("interrupted");
    expect(state.spans["s1"]?.durationMs).toBe(10_000);
    expect(state.spans["s2"]?.state).toBe("interrupted");
    expect(state.spans["s2"]?.durationMs).toBe(7_000);
    expect(state.agents.orchestrator.state).toBe("interrupted");
    expect(state.agents.pricing_agent.state).toBe("interrupted");
    expect(state.status).toBe("partial");
  });

  it("failed: terminal span + lane states are 'interrupted' (matches partial path)", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "orchestrator", "triage", "2026-11-11T00:00:00Z"),
      {
        ...EB,
        id: 3,
        ts: "2026-11-11T00:00:02Z",
        type: "run.finished",
        payload: { status: "failed", outcome: "error", outcome_detail: "crash" },
      },
    ]);
    expect(state.spans["s1"]?.state).toBe("interrupted");
    expect(state.agents.orchestrator.state).toBe("interrupted");
  });

  it("idle lane that ran ≥1 span → 'done' on completed; lane that never ran → 'idle'", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "document_analyst", "extract"),
      spanFinished(3, "s1", "document_analyst"),
      spanStarted(4, "s2", null, "pricing_agent", "batch"),
      spanFinished(5, "s2", "pricing_agent"),
      {
        ...EB,
        id: 6,
        type: "run.finished",
        payload: { status: "completed", outcome: "bid_ready", outcome_detail: null },
      },
    ]);
    expect(state.agents.document_analyst.state).toBe("done");
    expect(state.agents.pricing_agent.state).toBe("done");
    // orchestrator never ran a span → still idle.
    expect(state.agents.orchestrator.state).toBe("idle");
  });

  it("idle lane that ran ≥1 span → 'done' even under partial; still-working lane → 'interrupted'", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "document_analyst", "extract"),
      spanFinished(3, "s1", "document_analyst"),
      spanStarted(4, "s2", null, "pricing_agent", "batch"),
      {
        ...EB,
        id: 5,
        type: "run.finished",
        payload: { status: "partial", outcome: "budget_exhausted", outcome_detail: null },
      },
    ]);
    expect(state.agents.document_analyst.state).toBe("done");
    expect(state.agents.pricing_agent.state).toBe("interrupted");
    expect(state.agents.orchestrator.state).toBe("idle");
  });

  it("idle lane that carries an errorKind → 'done' with errorKind kept", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "parent", null, "pricing_agent", "batch"),
      spanStarted(3, "child", "parent", "pricing_agent", "query_catalog"),
      spanFailed(4, "child", "pricing_agent", /*will_retry*/ false, "transport"),
      spanFinished(5, "parent", "pricing_agent"),
      {
        ...EB,
        id: 6,
        type: "run.finished",
        payload: { status: "completed", outcome: "bid_ready", outcome_detail: null },
      },
    ]);
    expect(state.agents.pricing_agent.state).toBe("done");
    expect(state.agents.pricing_agent.errorKind).toBe("transport");
  });

  it("preserves 'failed' lanes (and their errorKind) under a partial run.finished", () => {
    const state = reduceAll([
      runStarted(1),
      spanStarted(2, "s1", null, "pricing_agent", "batch"),
      spanFailed(3, "s1", "pricing_agent", /*will_retry*/ false, "timeout"),
      {
        ...EB,
        id: 4,
        type: "run.finished",
        payload: { status: "partial", outcome: "budget_exhausted", outcome_detail: null },
      },
    ]);
    expect(state.agents.pricing_agent.state).toBe("failed");
    expect(state.agents.pricing_agent.errorKind).toBe("timeout");
    expect(state.status).toBe("partial");
  });
});

// ─── bid.ready, budget.exhausted ───────────────────────────────────

describe("runReducer — bid.ready, budget.exhausted", () => {
  it("captures bid.ready", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "bid.ready",
        payload: { revision: 0, generated_by: "orchestrator", open_flags: 4 },
      },
    ]);
    expect(state.bidReady).toEqual({
      revision: 0,
      generated_by: "orchestrator",
      open_flags: 4,
    });
  });

  it("captures budget.exhausted", () => {
    const state = reduceAll([
      runStarted(1),
      {
        ...EB,
        id: 2,
        type: "budget.exhausted",
        payload: { budget: "tokens", detail: "over 400k" },
      },
    ]);
    expect(state.budget).toEqual({ budget: "tokens", detail: "over 400k" });
  });
});
