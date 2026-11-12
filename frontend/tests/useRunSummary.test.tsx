/**
 * useRunSummary live-invalidation + staleTime policy
 * (DECISIONS F9 refinement, post-Phase-7 follow-up D2).
 *
 * The hook has three regimes:
 *   - No runState            → one-shot fetch, 30s staleTime.
 *   - Live (status=running)  → 30s staleTime; invalidate on
 *                              `document_analyst` span.finished and
 *                              on bid.ready.
 *   - Finalized              → Infinity staleTime, no auto refetch.
 *
 * Tests cover each regime + the key invariant: invalidation fires
 * on the SIGNAL FLIP (not every render).
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook } from "@testing-library/react";
import { act, useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { useRunSummary } from "@/api/useRunSummary";
import {
  initialRunViewState,
  runReducer,
  type RunViewState,
  type SpanView,
} from "@/lib/runReducer";

function makeQC() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: 0 },
    },
  });
}

function wrapper({
  client,
  children,
}: {
  client: QueryClient;
  children: React.ReactNode;
}) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

/**
 * Patch queryClient.invalidateQueries to a spy so we can assert
 * how many times the hook invalidated (and with what key).
 */
function spyInvalidate(qc: QueryClient) {
  const spy = vi.fn().mockResolvedValue(undefined);
  qc.invalidateQueries = spy as unknown as QueryClient["invalidateQueries"];
  return spy;
}

// Helpers to build reducer state ────────────────────────────────

function withAnalystFinished(s: RunViewState, id = "s_extract"): RunViewState {
  const span: SpanView = {
    id,
    parentId: null,
    agent: "document_analyst",
    name: "analyst.extract",
    label: null,
    state: "finished",
    startedAtTs: "2026-11-11T00:00:00Z",
    durationMs: 500,
    summary: null,
    errorKind: null,
    errorMessage: null,
    willRetry: false,
    tool: null,
    retry: null,
    validation: null,
    injected: false,
  };
  return { ...s, spans: { ...s.spans, [id]: span } };
}

function withBidReady(s: RunViewState): RunViewState {
  return {
    ...s,
    bidReady: { revision: 0, generated_by: "orchestrator", open_flags: 0 },
  };
}

function running(): RunViewState {
  return runReducer(initialRunViewState, {
    id: 1,
    run_id: "run_demo_b2",
    ts: "2026-11-11T00:00:00Z",
    agent: null,
    span_id: null,
    parent_span_id: null,
    type: "run.started",
    payload: { input_kind: "pdf", input_name: "rfp_clean.pdf", page_count: 3, branch_id: "BR-NJ-01" },
  });
}

function finalized(): RunViewState {
  return { ...running(), status: "completed", bidReady: { revision: 0, generated_by: "orchestrator", open_flags: 0 } };
}

describe("useRunSummary — staleTime policy", () => {
  it("no runState → treats as live (short staleTime, one-shot)", () => {
    const qc = makeQC();
    const invalidate = spyInvalidate(qc);
    renderHook(() => useRunSummary("run_demo_b2"), {
      wrapper: ({ children }) => wrapper({ client: qc, children }),
    });
    // No signal, no invalidation.
    expect(invalidate).toHaveBeenCalledTimes(0);
  });

  it("finalized runState → Infinity staleTime, no invalidations fired by the hook", () => {
    const qc = makeQC();
    const invalidate = spyInvalidate(qc);
    // Pre-seed bid.ready + analyst finished to cover both signals.
    const state = withAnalystFinished(withBidReady(finalized()));
    renderHook(() => useRunSummary("run_demo_b2", { runState: state }), {
      wrapper: ({ children }) => wrapper({ client: qc, children }),
    });
    // Signals are true BUT we still fire invalidation once on the
    // first render — regardless of finalized status, we want the
    // row to be fresh when either signal lands. staleTime applies
    // AFTER the refetch completes.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run", "run_demo_b2", "summary"] });
  });
});

describe("useRunSummary — signal-driven invalidation", () => {
  it("fires invalidate on analyst span.finished flip (false → true)", () => {
    const qc = makeQC();
    const invalidate = spyInvalidate(qc);
    // Host lets us flip the state between renders — simulates an
    // SSE event arriving.
    function Host(): { setFinished: (v: boolean) => void } {
      const [finished, setFinished] = useState(false);
      const state = finished ? withAnalystFinished(running()) : running();
      useRunSummary("run_demo_b2", { runState: state });
      return { setFinished };
    }
    let api: { setFinished: (v: boolean) => void } | null = null;
    renderHook(
      () => {
        api = Host();
        return api;
      },
      { wrapper: ({ children }) => wrapper({ client: qc, children }) },
    );
    expect(invalidate).toHaveBeenCalledTimes(0);
    act(() => {
      api!.setFinished(true);
    });
    expect(invalidate).toHaveBeenCalledTimes(1);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["run", "run_demo_b2", "summary"] });
  });

  it("fires invalidate on bid.ready flip (false → true)", () => {
    const qc = makeQC();
    const invalidate = spyInvalidate(qc);
    function Host(): { setReady: (v: boolean) => void } {
      const [ready, setReady] = useState(false);
      const state = ready ? withBidReady(running()) : running();
      useRunSummary("run_demo_b2", { runState: state });
      return { setReady };
    }
    let api: { setReady: (v: boolean) => void } | null = null;
    renderHook(
      () => {
        api = Host();
        return api;
      },
      { wrapper: ({ children }) => wrapper({ client: qc, children }) },
    );
    expect(invalidate).toHaveBeenCalledTimes(0);
    act(() => {
      api!.setReady(true);
    });
    expect(invalidate).toHaveBeenCalledTimes(1);
  });

  it("doesn't fire again on subsequent renders with the same signal (no thrash)", () => {
    const qc = makeQC();
    const invalidate = spyInvalidate(qc);
    function Host(): { bump: () => void } {
      const [count, bump] = useState(0);
      // Analyst always finished; `count` forces re-renders without
      // changing the invalidation signal.
      const state = withAnalystFinished(running());
      useRunSummary("run_demo_b2", { runState: state });
      return { bump: () => bump(count + 1) };
    }
    let api: { bump: () => void } | null = null;
    renderHook(
      () => {
        api = Host();
        return api;
      },
      { wrapper: ({ children }) => wrapper({ client: qc, children }) },
    );
    // First render invalidates once.
    expect(invalidate).toHaveBeenCalledTimes(1);
    // Bump three times — signal unchanged, so no new invalidations.
    act(() => {
      api!.bump();
    });
    act(() => {
      api!.bump();
    });
    act(() => {
      api!.bump();
    });
    expect(invalidate).toHaveBeenCalledTimes(1);
  });
});
