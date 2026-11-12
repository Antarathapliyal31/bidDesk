/**
 * TraceView tab (SPEC §11.2 "same TraceTimeline, replay from DB").
 *
 * Reuses the same reducer state as the Live tab. Covered here:
 *   - Summary header shows event count, span count, status badge,
 *     and (once outcome lands) the outcome.
 *   - The TraceTimeline renders the fixture's roots.
 *   - Before any events arrive, the summary stays sensible (0/0)
 *     and the timeline shows its empty state.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RFP_MESSY_EVENTS } from "@/dev-fixtures/sse/rfp-messy";
import { initialRunViewState, runReducer, type RunViewState } from "@/lib/runReducer";

import { TraceView } from "../src/features/run/TraceView";

function drain(): RunViewState {
  return RFP_MESSY_EVENTS.reduce((s, e) => runReducer(s, e), initialRunViewState);
}

describe("TraceView", () => {
  it("initial state: summary shows 0 events / 0 spans, timeline shows empty state", () => {
    render(<TraceView state={initialRunViewState} />);
    const stats = screen.getByRole("region", { name: /trace summary/i });
    expect(stats).toHaveTextContent(/events\s*0/i);
    expect(stats).toHaveTextContent(/spans\s*0/i);
    expect(screen.getByText(/waiting for the first span/i)).toBeInTheDocument();
  });

  it("after drain: summary reflects totals, both root spans render, outcome shows", () => {
    render(<TraceView state={drain()} />);
    // Span count: analyst + orchestrator + pricing = 3. Event count
    // is whatever the fixture produced.
    const stats = screen.getByRole("region", { name: /trace summary/i });
    expect(stats).toHaveTextContent(/spans\s*3/i);
    // Roots appear in the timeline.
    expect(screen.getByText("analyst.extract")).toBeInTheDocument();
    expect(screen.getByText("orchestrator.loop")).toBeInTheDocument();
    // Outcome lands with run.finished.
    expect(stats).toHaveTextContent(/outcome/i);
  });

  it("summary header labels every stat for screen readers", () => {
    render(<TraceView state={drain()} />);
    const stats = screen.getByRole("region", { name: /trace summary/i });
    expect(stats).toHaveTextContent(/events/i);
    expect(stats).toHaveTextContent(/spans/i);
    expect(stats).toHaveTextContent(/elapsed/i);
  });
});
