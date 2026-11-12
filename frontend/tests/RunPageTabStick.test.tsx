/**
 * R3 refinement (post-Phase-7 follow-up F2): a tab the user picked
 * BEFORE bid.ready must NOT auto-switch when bid.ready lands.
 *
 * Driving this through the fixture SSE driver would mean waiting
 * ~15 s of fake timers for all 100+ scripted events. Instead we
 * mock `useRunStream` directly so we can flip bid.ready between
 * the click and the assertion — the exact scenario from the manual
 * pass. The file is dedicated so the module-level mock stays
 * scoped.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect, useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "@/app/queryClient";
import { initialRunViewState, type RunViewState } from "@/lib/runReducer";

// A tiny observer bus the mock subscribes to. The Host component
// publishes its current reducer state; the mock reads the latest
// value on each render. Keeping this out of React state means the
// mock function body stays pure and the eslint `components-and-
// hooks-must-be-pure` rule is satisfied.
type Listener = () => void;
const stateBus = (() => {
  let current: RunViewState = initialRunViewState;
  const listeners = new Set<Listener>();
  return {
    get(): RunViewState {
      return current;
    },
    set(s: RunViewState): void {
      current = s;
      listeners.forEach((l) => l());
    },
    subscribe(l: Listener): () => void {
      listeners.add(l);
      return () => listeners.delete(l);
    },
  };
})();

vi.mock("@/api", async (orig) => {
  const actual = await orig<typeof import("@/api")>();
  // The mock reads the bus via useSyncExternalStore so the RunPage
  // re-renders whenever the Host publishes a new state.
  const { useSyncExternalStore } = await import("react");
  return {
    ...actual,
    useRunStream: () => {
      const state = useSyncExternalStore(stateBus.subscribe, stateBus.get, stateBus.get);
      return { state, connection: "live" as const };
    },
    useRunSummary: () => ({ data: null, isPending: false, isError: false, error: null }),
    isReusedStream: () => true,
  };
});

import { RunPage } from "@/features/run/RunPage";

function renderRun() {
  const qc = createTestQueryClient();
  // Reset before each render so test isolation holds.
  stateBus.set(initialRunViewState);
  return render(
    <QueryClientProvider client={qc as unknown as QueryClient}>
      <MemoryRouter initialEntries={["/runs/run_demo_messy"]}>
        <Routes>
          <Route path="/runs/:runId" element={<Host />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const RUN_STARTED: RunViewState = {
  ...initialRunViewState,
  startedAtTs: "2026-11-11T00:00:00Z",
  lastEventTs: "2026-11-11T00:00:05Z",
};
const BID_READY: RunViewState = {
  ...initialRunViewState,
  startedAtTs: "2026-11-11T00:00:00Z",
  lastEventTs: "2026-11-11T00:01:00Z",
  status: "completed",
  bidReady: { revision: 0, generated_by: "orchestrator", open_flags: 2 },
};

/**
 * Host publishes reducer states to the bus via useEffect on mount
 * (no reassignment in render body). A test button flips to the
 * "bid ready" state when clicked.
 */
function Host() {
  const [kind, setKind] = useState<"running" | "ready">("running");
  // Effect publishes on every state change — pure render, side
  // effect isolated.
  useEffect(() => {
    stateBus.set(kind === "running" ? RUN_STARTED : BID_READY);
  }, [kind]);
  return (
    <>
      <button type="button" data-testid="fire-bid-ready" onClick={() => setKind("ready")}>
        fire bid.ready
      </button>
      <RunPage />
    </>
  );
}

describe("R3 tab sticks when user picked a tab before bid.ready", () => {
  it("Trace clicked before bid.ready → Trace stays active after bid.ready arrives", async () => {
    const user = userEvent.setup();
    renderRun();
    // Phase 1: Live tab is the default.
    const traceTab = screen.getByRole("tab", { name: /^trace$/i });
    const bidTab = screen.getByRole("tab", { name: /^bid/i });
    expect(screen.getByRole("tab", { name: /^live$/i })).toHaveAttribute("data-state", "active");
    expect(bidTab).toBeDisabled();
    // Phase 2: user picks Trace.
    await user.click(traceTab);
    expect(traceTab).toHaveAttribute("data-state", "active");
    // Phase 3: fire bid.ready via the Host button.
    await user.click(screen.getByTestId("fire-bid-ready"));
    // Bid tab is no longer disabled.
    expect(bidTab).not.toBeDisabled();
    // Trace is STILL active — user's pick survived bid.ready.
    expect(traceTab).toHaveAttribute("data-state", "active");
    expect(bidTab).toHaveAttribute("data-state", "inactive");
  });

  it("No tab clicked before bid.ready → Bid auto-activates (default behavior)", async () => {
    const user = userEvent.setup();
    renderRun();
    // No click. Fire bid.ready directly.
    await user.click(screen.getByTestId("fire-bid-ready"));
    expect(screen.getByRole("tab", { name: /^bid/i })).toHaveAttribute("data-state", "active");
  });
});
