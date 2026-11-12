/**
 * LiveView: AgentLanes + ItemsStream. Driven by a RunViewState,
 * not the hook (fast; no timers). CitationLinks require a
 * SourcePanel context, so every render wraps LiveView with the
 * provider (same as RunPage does in prod).
 */
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { initialRunViewState, runReducer, type RunViewState } from "@/lib/runReducer";
import { RFP_MESSY_EVENTS } from "@/dev-fixtures/sse/rfp-messy";
import { SourcePanelProvider } from "@/features/run/source/SourcePanelContext";

import { LiveView } from "../src/features/run/LiveView";

function renderLive(state: RunViewState, pdfUrl: string | null = null) {
  return render(
    <SourcePanelProvider>
      <LiveView state={state} pdfUrl={pdfUrl} />
    </SourcePanelProvider>,
  );
}

function initialState(): RunViewState {
  return initialRunViewState;
}

function drainFixture(): RunViewState {
  return RFP_MESSY_EVENTS.reduce((s, e) => runReducer(s, e), initialRunViewState);
}

function stateAfterFirstItem(): RunViewState {
  // Drive events until the first item is extracted, no further.
  let s = initialRunViewState;
  for (const e of RFP_MESSY_EVENTS) {
    s = runReducer(s, e);
    if (e.type === "item.extracted") break;
  }
  return s;
}

describe("LiveView — AgentLanes", () => {
  it("renders all three agent cards labelled by role", () => {
    renderLive(initialState());
    expect(screen.getByRole("article", { name: "Document Analyst" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Orchestrator" })).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "Pricing Agent" })).toBeInTheDocument();
  });

  it("initial state: every lane shows 'Idle'", () => {
    renderLive(initialState());
    expect(screen.getAllByText("Idle")).toHaveLength(3);
  });

  it("after the fixture stream drains: every lane shows 'Done'", () => {
    renderLive(drainFixture());
    expect(screen.getAllByText("Done")).toHaveLength(3);
  });
});

describe("LiveView — ItemsStream", () => {
  it("empty state when no items have been extracted yet", () => {
    renderLive(initialState());
    expect(
      screen.getByText(/waiting for the analyst to extract/i),
    ).toBeInTheDocument();
  });

  it("one extracted item: row shows the description + requested qty but no price yet", () => {
    renderLive(stateAfterFirstItem());
    // Item 1 in rfp_messy: "2x4 x 8' SPF #2", qty "100", unit "pcs".
    expect(screen.getByText(/2x4 x 8' SPF #2/i)).toBeInTheDocument();
    expect(screen.getByText(/100 pcs/)).toBeInTheDocument();
  });

  it("after drain: 26 rows render; priced rows show their SKU/product_name + confidence pill", () => {
    renderLive(drainFixture());
    const list = screen.getByRole("list");
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(26);
    // The matched item for line 1 carries product_name on the row.
    expect(screen.getByText(/SPF #2 2x4x8/)).toBeInTheDocument();
    // Cedar shakes = no_match in the fixture.
    expect(screen.getByText(/no match/i)).toBeInTheDocument();
    // Underlayment shortfall renders a "short" chip.
    expect(screen.getByText(/^short$/i)).toBeInTheDocument();
    // Substitutes mark themselves.
    expect(screen.getAllByText(/^sub$/i).length).toBeGreaterThan(0);
  });
});
