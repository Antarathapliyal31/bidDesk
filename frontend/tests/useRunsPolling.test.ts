// @vitest-environment node
/**
 * `shouldPollRuns` is the refetchInterval policy for the History
 * page. The real TanStack timer plumbing is well-tested by the
 * library; this covers our branch.
 */
import { describe, expect, it } from "vitest";

import { shouldPollRuns, type RunsPage } from "../src/api/useRuns";

function page(statuses: Array<RunsPage["items"][number]["status"]>): RunsPage {
  return {
    items: statuses.map((s, i) => ({
      id: `r${i}`,
      status: s,
      outcome: null,
      input_kind: "pdf",
      input_name: "x",
      project_name: null,
      branch_name: "y",
      priced_lines: 0,
      open_flags: 0,
      total_cents: null,
      created_at: "2026-11-10T00:00:00Z",
      finalized_at: null,
      diff: null,
    })),
    next_cursor: null,
  };
}

describe("shouldPollRuns", () => {
  it("returns false when there are no pages", () => {
    expect(shouldPollRuns([])).toBe(false);
  });

  it("returns false when no visible run is running", () => {
    expect(shouldPollRuns([page(["completed", "partial", "failed"])])).toBe(false);
  });

  it("returns 5000 when at least one visible run is running", () => {
    expect(shouldPollRuns([page(["completed", "running"])])).toBe(5000);
  });

  it("checks every page, not just the first", () => {
    expect(
      shouldPollRuns([page(["completed"]), page(["interrupted", "running"])]),
    ).toBe(5000);
  });
});
