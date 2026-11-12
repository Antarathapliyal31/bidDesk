/**
 * HistoryPage (SPEC §11.1, §11.4):
 *   - first page shows FIXTURE_PAGE_SIZE rows + Load more.
 *   - clicking Load more fetches the next page; the button then
 *     disappears because next_cursor is null on the last page.
 *   - Empty state renders when useRuns returns no items.
 *   - Error state renders ApiError's mapped message and Try again
 *     calls refetch.
 *
 * Polling (shouldPollRuns) is unit-tested separately in
 * tests/useRunsPolling.test.ts — it's a pure function.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/api/ApiError";
import { FIXTURE_PAGE_SIZE, useRuns as realUseRuns } from "@/api/useRuns";
import { FIXTURE_RUNS } from "@/dev-fixtures";

import { HistoryPage } from "../src/features/history/HistoryPage";

// Mutable `impl` lets each test decide whether to use the real
// fixture-backed hook or inject a stubbed result. Reset to real in
// beforeEach so a leftover mock from one test can't leak into
// another.
type UseRunsFn = typeof realUseRuns;
let impl: UseRunsFn = realUseRuns;

vi.mock("@/api", async (orig) => {
  const actual = await orig<typeof import("@/api")>();
  return {
    ...actual,
    useRuns: (...args: Parameters<UseRunsFn>) => impl(...args),
  };
});

function TestWrap({ children }: { children: React.ReactNode }) {
  const [qc] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { retry: false, refetchOnWindowFocus: false, staleTime: 0 },
          mutations: { retry: 0 },
        },
      }),
  );
  return (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/runs"]}>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  impl = realUseRuns;
});
afterEach(() => vi.clearAllMocks());

describe("HistoryPage", () => {
  it("shows the first FIXTURE_PAGE_SIZE rows + Load more, then paginates on click", async () => {
    const user = userEvent.setup();
    render(
      <TestWrap>
        <HistoryPage />
      </TestWrap>,
    );

    await waitFor(() => {
      expect(screen.getAllByRole("listitem")).toHaveLength(FIXTURE_PAGE_SIZE);
    });
    const loadMore = screen.getByRole("button", { name: /load more/i });
    expect(loadMore).toBeInTheDocument();

    await user.click(loadMore);

    await waitFor(() => {
      expect(screen.getAllByRole("listitem")).toHaveLength(FIXTURE_RUNS.length);
    });
    expect(screen.queryByRole("button", { name: /load more/i })).toBeNull();
  });

  it("renders the empty state with 'Create your first bid' when there are no runs", () => {
    impl = () =>
      ({
        data: {
          pages: [{ items: [], next_cursor: null }],
          pageParams: [undefined],
        },
        isPending: false,
        isError: false,
        error: null,
        hasNextPage: false,
        fetchNextPage: () => Promise.resolve(),
        isFetchingNextPage: false,
        refetch: () => Promise.resolve(),
      }) as unknown as ReturnType<UseRunsFn>;
    render(
      <TestWrap>
        <HistoryPage />
      </TestWrap>,
    );
    expect(screen.getByRole("heading", { level: 2, name: /no runs yet/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /create your first bid/i })).toHaveAttribute(
      "href",
      "/new",
    );
  });

  it("non-terminal rows (running / failed / interrupted) render '—' for Lines + Flags, not 0", async () => {
    render(
      <TestWrap>
        <HistoryPage />
      </TestWrap>,
    );
    // The running fixture (run_demo_a1) has priced_lines: 0, open_flags: 0.
    // A terminal "completed" row has >0 priced_lines and we don't
    // want to treat that one as the test subject.
    const runningRow = await screen.findByRole("link", {
      name: new RegExp("Maple St. Duplex".replace(/\./g, "\\."), "i"),
    });
    expect(runningRow).toBeInTheDocument();
    // Scope the dash assertion to the running row to avoid matching
    // other rows' em-dashes (e.g. "no value" in Money).
    const dashes = runningRow.querySelectorAll("[aria-hidden]");
    const dashTexts = Array.from(dashes).map((n) => n.textContent);
    // At least two em-dashes (Lines + Flags) inside this row.
    expect(dashTexts.filter((t) => t === "—").length).toBeGreaterThanOrEqual(2);
  });

  it("renders the error state + Try again calls refetch", async () => {
    const user = userEvent.setup();
    const refetchSpy = vi.fn().mockResolvedValue(undefined);
    impl = () =>
      ({
        data: undefined,
        isPending: false,
        isError: true,
        error: new ApiError("network_error", "x", 0),
        hasNextPage: false,
        fetchNextPage: () => Promise.resolve(),
        isFetchingNextPage: false,
        refetch: refetchSpy,
      }) as unknown as ReturnType<UseRunsFn>;
    render(
      <TestWrap>
        <HistoryPage />
      </TestWrap>,
    );
    // messageForError maps network_error to the "can't reach server" copy.
    expect(screen.getByRole("alert")).toHaveTextContent(/can't reach the server/i);

    await user.click(screen.getByRole("button", { name: /try again/i }));
    expect(refetchSpy).toHaveBeenCalledTimes(1);
  });
});
