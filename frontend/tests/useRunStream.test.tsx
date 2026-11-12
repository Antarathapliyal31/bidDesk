/**
 * `useRunStream` — fixture-aware live-run hook.
 *
 * - Known fixture runId → connection flips to "live", reducer
 *   drains through to `completed`.
 * - Unknown runId → connection "closed", state untouched.
 */
import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useRunStream } from "../src/api/useRunStream";
import { RFP_MESSY_RUN_ID } from "../src/dev-fixtures/sse";

describe("useRunStream", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
  afterEach(() => vi.useRealTimers());

  it("fixture runId: connection goes live, reducer eventually reaches 'completed'", () => {
    const { result } = renderHook(() =>
      useRunStream(RFP_MESSY_RUN_ID, { intervalMs: 1 }),
    );
    expect(result.current.connection).toBe("live");
    // Initial state — events haven't fired yet.
    expect(result.current.state.status).toBe("running");
    expect(result.current.state.itemOrder).toHaveLength(0);

    act(() => {
      vi.runAllTimers();
    });

    expect(result.current.state.status).toBe("completed");
    expect(result.current.state.outcome).toBe("bid_ready");
    expect(result.current.state.itemOrder).toHaveLength(26);
    expect(result.current.state.agents.orchestrator.state).toBe("done");
    expect(result.current.state.agents.document_analyst.state).toBe("done");
    expect(result.current.state.agents.pricing_agent.state).toBe("done");
  });

  it("unknown runId: connection is 'closed', state stays at initial", () => {
    const { result } = renderHook(() => useRunStream("run_does_not_exist"));
    expect(result.current.connection).toBe("closed");
    expect(result.current.state.itemOrder).toHaveLength(0);
    expect(result.current.state.lastEventId).toBe(0);
  });

  it("real branch: NAMED SSE events reach the reducer via addEventListener", () => {
    // Backend emits frames like `event: run.started\ndata: {...}`.
    // `EventSource.onmessage` never fires for a named event; the
    // hook must register one listener per type. This test proves
    // it does by dispatching named events through a mock
    // EventSource and asserting the reducer advanced.
    vi.useRealTimers();
    vi.stubEnv("VITE_FIXTURES", "");
    try {
      const listeners = new Map<string, Set<(ev: MessageEvent<string>) => void>>();
      const mock = {
        url: "",
        close: vi.fn(),
        addEventListener: (type: string, fn: EventListener) => {
          if (!listeners.has(type)) listeners.set(type, new Set());
          listeners.get(type)!.add(fn as (ev: MessageEvent<string>) => void);
        },
        removeEventListener: (type: string, fn: EventListener) => {
          listeners.get(type)?.delete(fn as (ev: MessageEvent<string>) => void);
        },
      } as unknown as EventSource;
      const fire = (type: string, data: unknown) => {
        const frame = new MessageEvent(type, { data: JSON.stringify(data) });
        act(() => {
          listeners.get(type)?.forEach((fn) => fn(frame));
        });
      };

      const { result } = renderHook(() =>
        useRunStream("run_abc", { eventSourceFactory: () => mock }),
      );
      expect(result.current.connection).toBe("connecting");

      fire("run.started", {
        id: 1,
        run_id: "run_abc",
        ts: "2026-11-12T12:00:00Z",
        agent: null,
        span_id: null,
        parent_span_id: null,
        type: "run.started",
        payload: {
          input_kind: "pdf",
          input_name: "run_abc.pdf",
          page_count: 5,
          branch_id: "BR-NJ-01",
        },
      });
      expect(result.current.state.lastEventId).toBe(1);

      fire("run.finished", {
        id: 2,
        run_id: "run_abc",
        ts: "2026-11-12T12:01:00Z",
        agent: null,
        span_id: null,
        parent_span_id: null,
        type: "run.finished",
        payload: { status: "completed", outcome: "bid_ready", outcome_detail: null },
      });
      // Reducer got the named event → the addEventListener wiring
      // worked (onmessage would never fire for a named frame).
      expect(result.current.state.status).toBe("completed");
      expect(result.current.state.lastEventId).toBe(2);
      // Hook closed the stream on run.finished.
      expect(mock.close).toHaveBeenCalled();
    } finally {
      vi.unstubAllEnvs();
      vi.useFakeTimers({ shouldAdvanceTime: false });
    }
  });

  it("unmounting stops the stream mid-flight (cleanup cancels pending timers)", () => {
    const { result, unmount } = renderHook(() =>
      useRunStream(RFP_MESSY_RUN_ID, { intervalMs: 100 }),
    );
    act(() => {
      vi.advanceTimersByTime(300); // fire ~3 events
    });
    const idAtUnmount = result.current.state.lastEventId;
    unmount();

    // Advance well past the whole stream — state must not grow.
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current.state.lastEventId).toBe(idAtUnmount);
  });
});
