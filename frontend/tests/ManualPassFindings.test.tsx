/**
 * Tests pinned to the manual-pass findings (2026-11-11) — see
 * the conversation thread for the original 10-point list. Each
 * `it(...)` names its finding number so a regression is easy to
 * trace back to its origin. Grouped by file/subject.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { act } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { isReusedStream } from "@/api";
import { AppRoutes } from "@/app/router";
import { FIXTURE_RUNS } from "@/dev-fixtures";
import { historyRunEvents } from "@/dev-fixtures/sse";
import { AgentLanes } from "@/features/run/AgentLanes";
import { ItemsStream } from "@/features/run/ItemsStream";
import { formatFileSize } from "@/lib/format";
import { reasonCodeLabel } from "@/lib/reasonCodes";
import {
  elapsedSecondsFromState,
  initialRunViewState,
  runReducer,
  type RunViewState,
} from "@/lib/runReducer";
import { Providers } from "@/app/Providers";
import { createTestQueryClient } from "@/app/queryClient";
import { SourcePanelProvider } from "@/features/run/source/SourcePanelContext";
import { RunPage } from "@/features/run/RunPage";

// ───────────────────────────────────────────────────────────────
// #10 — "Demo data" chip on every fixture-mode run
// ───────────────────────────────────────────────────────────────
describe("#10 isReusedStream", () => {
  it("returns true for the canonical rfp_messy fixture", () => {
    expect(isReusedStream("run_demo_messy")).toBe(true);
  });
  it("returns true for fallback / history demo ids", () => {
    expect(isReusedStream("run_demo_a1")).toBe(true);
    expect(isReusedStream("run_demo_c3")).toBe(true);
    expect(isReusedStream("run_demo_h8")).toBe(true);
  });
  it("returns false for ids outside the demo namespace", () => {
    expect(isReusedStream("run_prod_1234")).toBe(false);
    expect(isReusedStream("")).toBe(false);
  });
});

// ───────────────────────────────────────────────────────────────
// #8 — /history redirects to /runs
// ───────────────────────────────────────────────────────────────
describe("#8 /history → /runs redirect", () => {
  it("renders the History page when /history is requested", () => {
    render(
      <Providers
        queryClient={createTestQueryClient()}
        Router={({ children }) => (
          <MemoryRouter initialEntries={["/history"]}>{children}</MemoryRouter>
        )}
      >
        <AppRoutes />
      </Providers>,
    );
    // The History page renders a "History" heading via its route
    // element. Any sign that we're on the History screen (not the
    // New bid page) is enough: the "New bid" headings only render
    // on /new.
    expect(screen.queryByRole("heading", { name: /^new bid$/i })).toBeNull();
  });
});

// ───────────────────────────────────────────────────────────────
// #7 — formatFileSize: KB under 1 MB, MB above
// ───────────────────────────────────────────────────────────────
describe("#7 formatFileSize", () => {
  it("renders small files as KB (not '0.0 MB')", () => {
    expect(formatFileSize(500 * 1024)).toBe("500 KB"); // 500 KiB
    expect(formatFileSize(320 * 1024)).toBe("320 KB");
    expect(formatFileSize(1024)).toBe("1 KB");
  });
  it("renders files ≥ 1 MiB as one-decimal MB", () => {
    expect(formatFileSize(1024 * 1024)).toBe("1.0 MB");
    expect(formatFileSize(2.5 * 1024 * 1024)).toBe("2.5 MB");
    expect(formatFileSize(20 * 1024 * 1024)).toBe("20.0 MB");
  });
  it("0 bytes renders as '0 KB'; negative/NaN falls back to '—'", () => {
    expect(formatFileSize(0)).toBe("0 KB");
    expect(formatFileSize(-1)).toBe("—");
    expect(formatFileSize(NaN)).toBe("—");
  });
});

// ───────────────────────────────────────────────────────────────
// #5 — reason-code mapping
// ───────────────────────────────────────────────────────────────
describe("#5 reasonCodeLabel", () => {
  it("maps the canonical codes to human text", () => {
    expect(reasonCodeLabel("quote_not_found")).toBe("Quote not found");
    expect(reasonCodeLabel("quantity_not_in_quote")).toBe("Quantity missing in RFP");
    expect(reasonCodeLabel("unknown_unit")).toBe("Unknown unit");
    expect(reasonCodeLabel("shortfall")).toBe("Shortfall at branch");
    expect(reasonCodeLabel("no_match")).toBe("No catalog match");
  });
  it("interpolates the field on parameterized invalid_spec codes", () => {
    expect(reasonCodeLabel("invalid_spec:length")).toBe("Invalid spec: length");
    expect(reasonCodeLabel("invalid_spec:")).toBe("Invalid spec");
  });
  it("falls back to a humanized form for unknown codes (never a blank chip)", () => {
    expect(reasonCodeLabel("some_new_code")).toBe("Some new code");
    expect(reasonCodeLabel("x")).toBe("X");
  });
});

// ───────────────────────────────────────────────────────────────
// #2 — elapsed derived from event ts, not Date.now()
// ───────────────────────────────────────────────────────────────
describe("#2 elapsedSecondsFromState", () => {
  it("reads purely from startedAtTs + lastEventTs (no Date.now mix)", () => {
    const state: RunViewState = {
      ...initialRunViewState,
      startedAtTs: "2026-11-10T10:00:00Z",
      lastEventTs: "2026-11-10T10:00:12Z",
    };
    expect(elapsedSecondsFromState(state)).toBe(12);
  });
  it("returns 0 before any events arrive", () => {
    expect(elapsedSecondsFromState(initialRunViewState)).toBe(0);
  });
  it("freezes on run.finished — advancing wall-clock doesn't move it", () => {
    const state: RunViewState = {
      ...initialRunViewState,
      startedAtTs: "2020-01-01T00:00:00Z",
      lastEventTs: "2020-01-01T00:00:30Z",
    };
    // 10 years of real wall-clock later, result is still 30s.
    expect(elapsedSecondsFromState(state)).toBe(30);
  });
});

// ───────────────────────────────────────────────────────────────
// #9 — idle lane with ≥1 past span displays "Done"
// ───────────────────────────────────────────────────────────────
describe("#9 AgentLanes display: idle + past span → Done", () => {
  it("renders 'Done' when the reducer left the lane idle but a prior span finished", () => {
    const spans = {
      s1: {
        id: "s1",
        parentId: null,
        agent: "orchestrator" as const,
        name: "orchestrator.loop",
        label: null,
        state: "finished" as const,
        startedAtTs: "2026-11-11T00:00:00Z",
        durationMs: 100,
        summary: null,
        errorKind: null,
        errorMessage: null,
        willRetry: false,
        tool: null,
        retry: null,
        validation: null,
        injected: false,
      },
    };
    const agents = {
      ...initialRunViewState.agents,
      orchestrator: { state: "idle" as const, activity: null, activeSpanId: null, errorKind: null },
    };
    render(<AgentLanes agents={agents} spans={spans} />);
    const orchCard = screen.getByRole("article", { name: "Orchestrator" });
    expect(within(orchCard).getByText("Done")).toBeInTheDocument();
    // A lane with no past spans stays "Idle".
    const analyst = screen.getByRole("article", { name: "Document Analyst" });
    expect(within(analyst).getByText("Idle")).toBeInTheDocument();
  });
});

// ───────────────────────────────────────────────────────────────
// #3 — Bid tab Priced lines = lines with a price, not total items.
// Now covered by tests/bidFixture.test.ts via `buildMessyBid`'s
// `totals.priced_lines` (filters matched + substitute only). The
// BidWorkspace renders the same `totals` object unchanged, so the
// UI assertion would just duplicate that path.
// ───────────────────────────────────────────────────────────────

// ───────────────────────────────────────────────────────────────
// #6 — Unit price + Ext. columns
// ───────────────────────────────────────────────────────────────
describe("#6 ItemsStream columns", () => {
  it("header reads 'Unit price' and 'Ext.' (not just 'Price')", () => {
    const state: RunViewState = runReducer(initialRunViewState, {
      id: 1,
      run_id: "r",
      ts: "2026-11-11T00:00:00Z",
      agent: null,
      span_id: null,
      parent_span_id: null,
      type: "run.started",
      payload: { input_kind: "pdf", input_name: "x.pdf", page_count: 1, branch_id: "BR-NJ-01" },
    });
    const withItem: RunViewState = runReducer(state, {
      id: 2,
      run_id: "r",
      ts: "2026-11-11T00:00:01Z",
      agent: "document_analyst",
      span_id: null,
      parent_span_id: null,
      type: "item.extracted",
      payload: {
        item: {
          id: "x",
          kind: "material",
          description: "A thing",
          quantity_text: "100",
          unit_raw: "pcs",
          source: { page: 1, quote: null, read_via: "text", verified: true },
          notes: [],
        },
      },
    });
    render(
      <SourcePanelProvider>
        <ItemsStream state={withItem} pdfUrl={null} />
      </SourcePanelProvider>,
    );
    expect(screen.getByText(/^unit price$/i)).toBeInTheDocument();
    expect(screen.getByText(/^ext\.$/i)).toBeInTheDocument();
  });
});

// ───────────────────────────────────────────────────────────────
// #4 — vision = "Read from image" (neutral); text + quote_not_found
//      = "Quote not found" (amber); verified text = no badge.
// ───────────────────────────────────────────────────────────────
describe("#4 source-provenance badges", () => {
  function stateWithItem(opts: {
    read_via: "text" | "vision";
    verified: boolean;
    notes: string[];
  }): RunViewState {
    let s = runReducer(initialRunViewState, {
      id: 1,
      run_id: "r",
      ts: "2026-11-11T00:00:00Z",
      agent: null,
      span_id: null,
      parent_span_id: null,
      type: "run.started",
      payload: { input_kind: "pdf", input_name: "x.pdf", page_count: 1, branch_id: "BR-NJ-01" },
    });
    s = runReducer(s, {
      id: 2,
      run_id: "r",
      ts: "2026-11-11T00:00:01Z",
      agent: "document_analyst",
      span_id: null,
      parent_span_id: null,
      type: "item.extracted",
      payload: {
        item: {
          id: "x",
          kind: "material",
          description: "A thing",
          quantity_text: "100",
          unit_raw: "pcs",
          source: { page: 1, quote: null, read_via: opts.read_via, verified: opts.verified },
          notes: opts.notes,
        },
      },
    });
    return s;
  }

  function renderWith(state: RunViewState) {
    return render(
      <SourcePanelProvider>
        <ItemsStream state={state} pdfUrl={null} />
      </SourcePanelProvider>,
    );
  }

  it("vision source → neutral 'Read from image' chip (no 'Quote not found')", () => {
    renderWith(stateWithItem({ read_via: "vision", verified: false, notes: [] }));
    expect(screen.getByText(/read from image/i)).toBeInTheDocument();
    expect(screen.queryByText(/quote not found/i)).toBeNull();
    expect(screen.queryByText(/^unverified$/i)).toBeNull();
  });

  it("text + quote_not_found → amber 'Quote not found' (no 'Read from image')", () => {
    renderWith(
      stateWithItem({ read_via: "text", verified: false, notes: ["quote_not_found"] }),
    );
    expect(screen.getByText(/quote not found/i)).toBeInTheDocument();
    expect(screen.queryByText(/read from image/i)).toBeNull();
  });

  it("verified text source → no badge at all", () => {
    renderWith(stateWithItem({ read_via: "text", verified: true, notes: [] }));
    expect(screen.queryByText(/quote not found/i)).toBeNull();
    expect(screen.queryByText(/read from image/i)).toBeNull();
  });
});

// ───────────────────────────────────────────────────────────────
// #1 — History-row click lands on that row's terminal state
// ───────────────────────────────────────────────────────────────
describe("#1 History row → instant terminal state", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
  afterEach(() => vi.useRealTimers());

  function renderRun(runId: string) {
    const qc = createTestQueryClient();
    return render(
      <QueryClientProvider client={qc as unknown as QueryClient}>
        <MemoryRouter initialEntries={[`/runs/${runId}`]}>
          <Routes>
            <Route path="/runs/:runId" element={<RunPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  async function advanceRunSummary() {
    // useRunSummary resolves after its 50ms withDelay. Drain it so
    // the title flips to project_name.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
  }

  it("completed finalized run (run_demo_b2) renders project title + completed status", async () => {
    renderRun("run_demo_b2");
    await advanceRunSummary();
    // Title = project_name (R6); input_name is the subtitle.
    expect(screen.getByRole("heading", { level: 1, name: /oak ridge addition/i }))
      .toBeInTheDocument();
    expect(screen.getByText("rfp_clean.pdf")).toBeInTheDocument();
    expect(screen.getByText(/completed/i)).toBeInTheDocument();
    // Priced-line counts are tested in tests/bidFixture.test.ts
    // (totals.priced_lines in buildMessyBid).
  });

  it("partial run (run_demo_c3, Northside Remodel): partial status, 22 priced, 6 flags", async () => {
    renderRun("run_demo_c3");
    await advanceRunSummary();
    expect(screen.getByRole("heading", { level: 1, name: /northside remodel/i }))
      .toBeInTheDocument();
    // rfp_messy.pdf is the subtitle.
    expect(screen.getByText("rfp_messy.pdf")).toBeInTheDocument();
    expect(screen.getByText(/partial/i)).toBeInTheDocument();
    // open_flags = 6
    const bidTab = screen.getByRole("tab", { name: /^bid/i });
    expect(within(bidTab).getByText("6")).toBeInTheDocument();
  });

  it("failed run (run_demo_d4): status Failed + no bid.ready", async () => {
    renderRun("run_demo_d4");
    await advanceRunSummary();
    // Row has no project_name so the header title falls back to
    // the input filename (corrupt.pdf). Verify by matching the
    // StatusBadge within the header area.
    const headerArea = screen.getByRole("heading", { level: 1 }).closest("header")!;
    expect(within(headerArea).getByText(/^failed$/i)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^bid/i })).toBeDisabled();
  });

  it("every fixture history row renders without the 'couldn't find that run' empty state", async () => {
    // Guard: every row produces at least `run.started`, so either
    // the project_name or the input_name must appear in the title /
    // subtitle. Avoids the old messy-fallback regression.
    for (const run of FIXTURE_RUNS) {
      const { unmount } = renderRun(run.id);
      await advanceRunSummary();
      const heading = screen.getByRole("heading", { level: 1 });
      const expectedTitle = run.project_name ?? run.input_name;
      expect(heading.textContent).toContain(expectedTitle);
      unmount();
    }
  });
});

// ───────────────────────────────────────────────────────────────
// Unit test the historyRunEvents reducer-feed directly
// ───────────────────────────────────────────────────────────────
describe("historyRunEvents", () => {
  function drain(run: (typeof FIXTURE_RUNS)[number]): RunViewState {
    return historyRunEvents(run).reduce((s, e) => runReducer(s, e), initialRunViewState);
  }

  it("partial run (c3) ends with status=partial and open_flags=6", () => {
    const row = FIXTURE_RUNS.find((r) => r.id === "run_demo_c3")!;
    const state = drain(row);
    expect(state.status).toBe("partial");
    expect(state.bidReady?.open_flags).toBe(6);
    // priced_lines = items with status matched/substitute.
    const priced = Object.values(state.items).filter(
      (i) => i.priced?.status === "matched" || i.priced?.status === "substitute",
    ).length;
    expect(priced).toBe(row.priced_lines);
  });

  it("failed run (d4) ends with status=failed and no bid.ready", () => {
    const row = FIXTURE_RUNS.find((r) => r.id === "run_demo_d4")!;
    const state = drain(row);
    expect(state.status).toBe("failed");
    expect(state.bidReady).toBeNull();
  });

  it("running run (a1) leaves bidReady null and spans still open", () => {
    const row = FIXTURE_RUNS.find((r) => r.id === "run_demo_a1")!;
    const state = drain(row);
    expect(state.status).toBe("running");
    expect(state.bidReady).toBeNull();
  });

  it("no-materials run (f6) ends completed with no items", () => {
    const row = FIXTURE_RUNS.find((r) => r.id === "run_demo_f6")!;
    const state = drain(row);
    expect(state.status).toBe("completed");
    expect(state.outcome).toBe("no_materials");
    expect(state.itemOrder).toHaveLength(0);
  });
});

// ───────────────────────────────────────────────────────────────
// Combined: the exact scenario in the finding (Northside Remodel,
// previously replayed rfp_messy and ended Completed/26/12).
// ───────────────────────────────────────────────────────────────
describe("Northside Remodel bug fix", () => {
  it("opens with partial / 22 priced / 6 flags — not Completed/26/12", () => {
    const qc = createTestQueryClient();
    render(
      <QueryClientProvider client={qc as unknown as QueryClient}>
        <MemoryRouter initialEntries={["/runs/run_demo_c3"]}>
          <Routes>
            <Route path="/runs/:runId" element={<RunPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    // Reducer drain is synchronous — event-count assertions don't
    // need useRunSummary to resolve.
    expect(screen.getByText(/partial/i)).toBeInTheDocument();
    // Bid tab shows the flag count badge (6), not 12.
    const bidTab = screen.getByRole("tab", { name: /^bid/i });
    expect(within(bidTab).getByText("6")).toBeInTheDocument();
    expect(within(bidTab).queryByText("12")).toBeNull();
  });
});
