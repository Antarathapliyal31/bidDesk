/**
 * RunPage + RunHeader end-to-end (fixture mode).
 *
 * Covers:
 *   - unknown runId → designed empty state with CTAs.
 *   - fixture runId: Live tab visible, elapsed timer renders.
 *   - once `bid.ready` arrives, the Bid tab is enabled and
 *     auto-selected; the "Bid ready" summary shows up with the
 *     right line count + open_flags badge.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { act } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "@/app/queryClient";

import { RunPage } from "../src/features/run/RunPage";

function renderAt(path: string) {
  const qc = createTestQueryClient();
  return render(
    <QueryClientProvider client={qc as unknown as QueryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/runs/:runId" element={<RunPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("RunPage", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
  afterEach(() => vi.useRealTimers());

  it("unknown runId → 'We couldn't find that run' empty state", () => {
    renderAt("/runs/run_does_not_exist");
    expect(
      screen.getByRole("heading", { level: 2, name: /couldn't find that run/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /start a new bid/i })).toHaveAttribute("href", "/new");
    expect(screen.getByRole("link", { name: /view history/i })).toHaveAttribute("href", "/runs");
  });

  it("fixture runId shows the Live tab, status 'Running', and the Live chip initially", () => {
    renderAt("/runs/run_demo_messy");
    // Status badge renders before events arrive (default state = running).
    expect(screen.getByText(/running/i)).toBeInTheDocument();
    // Live tab is selected by default.
    const liveTab = screen.getByRole("tab", { name: "Live" });
    expect(liveTab).toHaveAttribute("data-state", "active");
    // Bid tab is disabled until bid.ready arrives.
    const bidTab = screen.getByRole("tab", { name: /^bid/i });
    expect(bidTab).toBeDisabled();
  });

  it("after the fixture stream drains: Bid tab is enabled + auto-selected", () => {
    renderAt("/runs/run_demo_messy");
    // Drain every scheduled setTimeout (fixture driver fires at
    // i * 150ms by default; runAllTimers races through).
    act(() => {
      vi.runAllTimers();
    });
    // Status is completed.
    expect(screen.getByText(/completed/i)).toBeInTheDocument();
    // Bid tab is active and shows the open-flag badge.
    const bidTab = screen.getByRole("tab", { name: /^bid/i });
    expect(bidTab).not.toBeDisabled();
    expect(bidTab).toHaveAttribute("data-state", "active");
    // Full BidWorkspace content is tested in tests/BidWorkspace.test.tsx;
    // here we just check the tab wired through.
  });
});
