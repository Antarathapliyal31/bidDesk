/**
 * StatusBadge (SPEC §11.5): one dot + one text label per run
 * status. Interrupted reads as a neutral, distinct-from-failed
 * state so an estimator can tell "the server restarted mid-run"
 * apart from "the pipeline crashed" at a glance.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "../src/components/StatusBadge";

describe("StatusBadge", () => {
  it.each(["running", "completed", "partial", "failed", "interrupted"] as const)(
    "renders a visible text label for status=%s",
    (status) => {
      render(<StatusBadge status={status} />);
      const label = status[0]!.toUpperCase() + status.slice(1);
      expect(screen.getByText(label)).toBeInTheDocument();
    },
  );

  it("interrupted uses neutral styling (no red), visibly distinct from failed", () => {
    const { container: interruptedCtx } = render(<StatusBadge status="interrupted" />);
    const { container: failedCtx } = render(<StatusBadge status="failed" />);
    const interruptedClasses = interruptedCtx.querySelector("span")?.className ?? "";
    const failedClasses = failedCtx.querySelector("span")?.className ?? "";

    // Interrupted has the neutral muted-foreground; failed has red.
    expect(interruptedClasses).toContain("text-muted-foreground");
    expect(interruptedClasses).not.toContain("text-fail-red-text");
    expect(failedClasses).toContain("text-fail-red-text");

    // The dot's background class differs too — neutral zinc vs red.
    const interruptedDot = interruptedCtx.querySelectorAll("span")[1];
    const failedDot = failedCtx.querySelectorAll("span")[1];
    expect(interruptedDot?.className).toContain("bg-zinc-400");
    expect(failedDot?.className).toContain("bg-fail-red");
  });
});
