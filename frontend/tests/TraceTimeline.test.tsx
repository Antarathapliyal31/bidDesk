/**
 * TraceTimeline integration with the fixture + reducer.
 *
 * Verifies:
 *   - Empty state when no spans have arrived.
 *   - After the fixture drains, the three root spans (s_extract,
 *     s_orch, s_pricing-is-nested-under-s_orch) render in order.
 *   - s_pricing appears as a child of s_orch once s_orch is expanded.
 *   - Expanding a span surfaces the tool call that happened on it.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RFP_MESSY_EVENTS } from "@/dev-fixtures/sse/rfp-messy";
import { initialRunViewState, runReducer, type RunViewState } from "@/lib/runReducer";

import { TraceTimeline } from "../src/features/run/TraceTimeline";

function drain(): RunViewState {
  return RFP_MESSY_EVENTS.reduce((s, e) => runReducer(s, e), initialRunViewState);
}

describe("TraceTimeline", () => {
  it("empty state before any span.started lands", () => {
    render(<TraceTimeline state={initialRunViewState} />);
    expect(screen.getByText(/waiting for the first span/i)).toBeInTheDocument();
  });

  it("after fixture drain: both root spans render", () => {
    render(<TraceTimeline state={drain()} />);
    // Two roots per rfp-messy fixture: analyst.extract, orchestrator.loop.
    expect(screen.getByText("analyst.extract")).toBeInTheDocument();
    expect(screen.getByText("orchestrator.loop")).toBeInTheDocument();
    // Pricing batch is NESTED — not visible until we expand s_orch.
    expect(screen.queryByText("pricing_agent.batch")).toBeNull();
  });

  it("expanding the orchestrator span reveals the nested pricing span", async () => {
    const user = userEvent.setup();
    render(<TraceTimeline state={drain()} />);
    const orchToggle = screen.getByRole("button", { name: /orchestrator\.loop/i });
    await user.click(orchToggle);
    expect(screen.getByText("pricing_agent.batch")).toBeInTheDocument();
  });

  it("expanding the orchestrator span reveals the last tool call it made", async () => {
    const user = userEvent.setup();
    render(<TraceTimeline state={drain()} />);
    await user.click(screen.getByRole("button", { name: /orchestrator\.loop/i }));
    // Reducer keeps the LAST tool call; in the fixture that's
    // `add_requirements` with result_preview "3 applied".
    const region = screen.getAllByRole("region").find((el) =>
      within(el).queryByText("add_requirements"),
    );
    expect(region).toBeDefined();
    expect(within(region!).getByText(/3 applied/)).toBeInTheDocument();
  });

  it("every finished root span surfaces its duration in the header", () => {
    render(<TraceTimeline state={drain()} />);
    // Fixture durations: 12_000ms (analyst) and 8_000ms (orch).
    expect(screen.getByText(/12\.0s/)).toBeInTheDocument();
    expect(screen.getByText(/8\.0s/)).toBeInTheDocument();
  });
});
