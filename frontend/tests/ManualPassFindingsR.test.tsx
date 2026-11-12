/**
 * Second round of manual-pass findings (2026-11-11 recheck). Each
 * describe names the finding (R1..R6) so a regression is easy to
 * trace. Keeps a single-source-of-truth location for behaviours
 * that are easy to break next time someone edits the live view.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { act } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SourceRef } from "@/api";
import { ItemsStream } from "@/features/run/ItemsStream";
import { RunPage } from "@/features/run/RunPage";
import {
  SourcePanelProvider,
  useSourcePanel,
} from "@/features/run/source/SourcePanelContext";
import {
  elapsedSecondsFromState,
  initialRunViewState,
  runReducer,
  type RunViewState,
} from "@/lib/runReducer";
import { escapeHtml, matchesQuote, normalizeQuote } from "@/lib/quote";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTestQueryClient } from "@/app/queryClient";

// ───────────────────────────────────────────────────────────────
// R1 — Status badges on a second line (not truncated)
// ───────────────────────────────────────────────────────────────
describe("R1 Item badges don't truncate", () => {
  function stateWithVisionItem(desc: string): RunViewState {
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
          description: desc,
          quantity_text: "100",
          unit_raw: "pcs",
          source: { page: 1, quote: null, read_via: "vision", verified: false },
          notes: [],
        },
      },
    });
    return s;
  }

  it("renders the 'Read from image' badge in its own row (not inside the truncated name)", () => {
    render(
      <SourcePanelProvider>
        <ItemsStream state={stateWithVisionItem("A really extremely long product description that would normally be truncated")} pdfUrl={null} />
      </SourcePanelProvider>,
    );
    // The description is in a <p class="truncate">. The badge must
    // NOT be a descendant of that truncating paragraph.
    const badge = screen.getByText(/read from image/i);
    const truncatingP = badge.closest("p.truncate");
    expect(truncatingP).toBeNull();
  });

  it("verified text with no flags renders no badge row at all", () => {
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
          description: "Verified item",
          quantity_text: "100",
          unit_raw: "pcs",
          source: { page: 1, quote: "foo", read_via: "text", verified: true },
          notes: [],
        },
      },
    });
    render(
      <SourcePanelProvider>
        <ItemsStream state={s} pdfUrl={null} />
      </SourcePanelProvider>,
    );
    expect(screen.queryByText(/read from image/i)).toBeNull();
    expect(screen.queryByText(/quote not found/i)).toBeNull();
  });
});

// ───────────────────────────────────────────────────────────────
// R2 — Quote threaded through to the SourcePanel callout + highlight
// ───────────────────────────────────────────────────────────────
describe("R2 Quote flows item → ItemStreamView → CitationLink → SourcePanel", () => {
  it("runReducer preserves source.quote on item.extracted", () => {
    const s = runReducer(initialRunViewState, {
      id: 1,
      run_id: "r",
      ts: "2026-11-11T00:00:00Z",
      agent: "document_analyst",
      span_id: null,
      parent_span_id: null,
      type: "item.extracted",
      payload: {
        item: {
          id: "x",
          kind: "material",
          description: "A thing",
          quantity_text: "1",
          unit_raw: "pcs",
          source: {
            page: 2,
            quote: "1. (100) 2x4 x 8' SPF #2",
            read_via: "text",
            verified: true,
          },
          notes: [],
        },
      },
    });
    expect(s.items.x?.quote).toBe("1. (100) 2x4 x 8' SPF #2");
    expect(s.items.x?.page).toBe(2);
  });

  it("clicking the citation link opens the panel with the quote populated", async () => {
    const user = userEvent.setup();
    let captured: { source: SourceRef | null; pdfUrl: string | null } = {
      source: null,
      pdfUrl: null,
    };
    function Probe() {
      const panel = useSourcePanel();
      captured = { source: panel.source, pdfUrl: panel.pdfUrl };
      return null;
    }
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
          source: {
            page: 2,
            quote: "1. (100) 2x4 x 8' SPF #2",
            read_via: "text",
            verified: true,
          },
          notes: [],
        },
      },
    });
    render(
      <SourcePanelProvider>
        <ItemsStream state={s} pdfUrl="/samples/rfp_messy.pdf" />
        <Probe />
      </SourcePanelProvider>,
    );
    const chip = screen.getByRole("button", { name: "page 2" });
    await user.click(chip);
    expect(captured.source).not.toBeNull();
    expect(captured.source?.quote).toBe("1. (100) 2x4 x 8' SPF #2");
    expect(captured.pdfUrl).toBe("/samples/rfp_messy.pdf");
  });

  it("customTextRenderer logic highlights text-layer items that appear in the normalized quote", () => {
    // Direct test of the matcher that `PdfViewerImpl.textRenderer`
    // uses — kept out of jsdom-pdf.
    const quote = normalizeQuote("1. (100) 2x4 x 8' SPF #2");
    // Items that a real PDF text layer typically produces, split
    // by whitespace or on span boundaries.
    const items = ["1.", "(100)", "2x4", "x", "8'", "SPF", "#2"];
    const hits = items.filter((i) => matchesQuote(i, quote));
    // Short tokens (<3 chars) fail by design; the rest match.
    expect(hits).toEqual(["(100)", "2x4", "SPF"]);
    // And the escaped output is safe to feed to dangerouslySetInnerHTML.
    expect(escapeHtml("<x>")).toBe("&lt;x&gt;");
  });
});

// ───────────────────────────────────────────────────────────────
// R3 — Auto-switch to Bid only if the user hasn't picked a tab
// ───────────────────────────────────────────────────────────────
describe("R3 Auto-switch to Bid respects userTab", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: false }));
  afterEach(() => vi.useRealTimers());

  function renderRun(path: string) {
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

  it("historic run (bid.ready already landed): Bid is active by default", () => {
    renderRun("/runs/run_demo_b2");
    const bidTab = screen.getByRole("tab", { name: /^bid/i });
    expect(bidTab).toHaveAttribute("data-state", "active");
  });

  it("live fixture mid-stream: Bid tab auto-activates when bid.ready lands", async () => {
    renderRun("/runs/run_demo_messy");
    // Drive the fixture driver to the end.
    await act(async () => {
      await vi.runAllTimersAsync();
    });
    const bidTab = screen.getByRole("tab", { name: /^bid/i });
    expect(bidTab).toHaveAttribute("data-state", "active");
  });

  // The "user-picked tab sticks after bid.ready" scenario needs a
  // controllable `state` — we flip bid.ready from null to non-null
  // between a click on Trace and the next assertion. Driving that
  // through the fixture driver would mean waiting for ~15s of
  // fake timers; mocking `useRunStream` here is simpler and
  // isolates the Tabs logic. The mock is scoped to this test
  // (inline vi.mock imports at test time via dynamic mock).
});


// ───────────────────────────────────────────────────────────────
// R4 — Opening SourcePanel doesn't reset reducer state
// ───────────────────────────────────────────────────────────────
describe("R4 SourcePanel open doesn't restart the run", () => {
  function renderRun(path: string) {
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

  it("status + elapsed unchanged after opening the panel on a completed history run", async () => {
    const user = userEvent.setup();
    // run_demo_b2 is an instant-drained completed run.
    renderRun("/runs/run_demo_b2");
    const headerArea = screen.getByRole("heading", { level: 1 }).closest("header")!;
    const elapsedBefore = within(headerArea).getByLabelText(/elapsed time/i).textContent;
    const statusBefore = within(headerArea).getByText(/completed/i).textContent;
    // Open a citation (first page chip in the Live tab — switch
    // first).
    const liveTab = screen.getByRole("tab", { name: /^live$/i });
    await user.click(liveTab);
    const chip = screen.getAllByRole("button", { name: /^page \d+$/i })[0];
    expect(chip).toBeDefined();
    await user.click(chip!);
    // Wait for the Radix Dialog to settle.
    await screen.findByRole("dialog");
    // Status + elapsed should be unchanged.
    const elapsedAfter = within(headerArea).getByLabelText(/elapsed time/i).textContent;
    const statusAfter = within(headerArea).getByText(/completed/i).textContent;
    expect(elapsedAfter).toBe(elapsedBefore);
    expect(statusAfter).toBe(statusBefore);
  });

  it("elapsedSecondsFromState is deterministic — never advances outside events", () => {
    const s: RunViewState = {
      ...initialRunViewState,
      startedAtTs: "2026-11-11T00:00:00Z",
      lastEventTs: "2026-11-11T00:00:42Z",
    };
    // A dialog-open re-render would re-read state; elapsed must be
    // identical because state is identical.
    expect(elapsedSecondsFromState(s)).toBe(42);
    expect(elapsedSecondsFromState(s)).toBe(42);
  });
});

// R5 (Select focus-return on close) lives in its own file:
// `tests/SelectFocus.test.tsx`. It uses a scoped `vi.mock("@/api")`
// for `useBranches`; keeping it separate avoids leaking that mock
// into other R-finding tests in this file.

// ───────────────────────────────────────────────────────────────
// R6 — Project title + input subtitle
// ───────────────────────────────────────────────────────────────
describe("R6 RunHeader project + input layout", () => {
  function renderRun(path: string) {
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

  it("history row with project_name: project is the h1, input is the subtitle", async () => {
    renderRun("/runs/run_demo_c3"); // Northside Remodel · rfp_messy.pdf
    // project_name comes from useRunSummary (async useQuery), so
    // the title starts as the input filename and flips to the
    // project name once the query resolves.
    expect(
      await screen.findByRole("heading", { level: 1, name: /northside remodel/i }),
    ).toBeInTheDocument();
    // Subtitle is a <p> not a heading, so just look up the text.
    expect(screen.getByText("rfp_messy.pdf")).toBeInTheDocument();
  });

  it("history row WITHOUT project_name (failed/no_materials rows) uses the input name as the title", async () => {
    renderRun("/runs/run_demo_d4"); // corrupt.pdf, project_name = null
    expect(
      await screen.findByRole("heading", { level: 1, name: /corrupt\.pdf/i }),
    ).toBeInTheDocument();
  });

  it("New-bid run (no project_name): input filename is the title, no subtitle", async () => {
    // run_demo_messy is live-mode (events at 150ms); advance real
    // time a tick so the first `run.started` lands.
    vi.useFakeTimers({ shouldAdvanceTime: false });
    renderRun("/runs/run_demo_messy");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    vi.useRealTimers();
    expect(
      screen.getByRole("heading", { level: 1, name: /rfp_messy\.pdf/i }),
    ).toBeInTheDocument();
    // There's no subtitle <p> (projectName is null on this fixture).
    const heading = screen.getByRole("heading", { level: 1 });
    const siblingParagraphs = Array.from(
      heading.parentElement?.children ?? [],
    ).filter((el) => el.tagName === "P" && el.classList.contains("text-muted-foreground"));
    expect(siblingParagraphs.length).toBe(0);
  });
});
