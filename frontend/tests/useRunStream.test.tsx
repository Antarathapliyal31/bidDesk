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
