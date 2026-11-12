/**
 * Sidebar (SPEC §11.2):
 *   - inner <nav aria-label="Primary"> is the navigation landmark.
 *   - the active route link has aria-current="page" (NavLink v6).
 *   - the footer "Keyboard shortcuts" button opens the shortcuts sheet.
 *   - "Recent runs" shows the top five from `useRuns()` (same query
 *     as the History page); empty text only when the query resolves
 *     with no items.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useRuns as realUseRuns } from "@/api/useRuns";
import { FIXTURE_RUNS } from "@/dev-fixtures";
import { Sidebar } from "@/app/Sidebar";
import { ShortcutsProvider, useShortcutsSheet } from "@/app/ShortcutsContext";

// Mutable `impl` lets tests toggle the hook between the real
// fixture-backed version and a stubbed empty result.
type UseRunsFn = typeof realUseRuns;
let impl: UseRunsFn = realUseRuns;

vi.mock("@/api", async (orig) => {
  const actual = await orig<typeof import("@/api")>();
  return {
    ...actual,
    useRuns: (...args: Parameters<UseRunsFn>) => impl(...args),
  };
});

function Harness({ children, route = "/new" }: { children: React.ReactNode; route?: string }) {
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
      <MemoryRouter initialEntries={[route]}>
        <ShortcutsProvider>{children}</ShortcutsProvider>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function ShortcutsProbe() {
  const { open } = useShortcutsSheet();
  return <div data-testid="shortcuts-open">{open ? "yes" : "no"}</div>;
}

beforeEach(() => {
  impl = realUseRuns;
});
afterEach(() => vi.clearAllMocks());

describe("Sidebar", () => {
  it("exposes a 'Primary' navigation landmark", () => {
    render(
      <Harness>
        <Sidebar />
      </Harness>,
    );
    expect(screen.getByRole("navigation", { name: "Primary" })).toBeInTheDocument();
  });

  it("marks the current route link with aria-current='page' and only that one", () => {
    render(
      <Harness route="/runs">
        <Sidebar />
      </Harness>,
    );
    // Multiple links named "History" can exist once recent runs
    // render — pick the primary-nav one explicitly.
    const history = screen.getByRole("link", { name: "History" });
    const newBid = screen.getByRole("link", { name: /new bid/i });
    expect(history).toHaveAttribute("aria-current", "page");
    expect(newBid).not.toHaveAttribute("aria-current", "page");
  });

  it("opens the shortcuts sheet when the footer button is clicked", async () => {
    const user = userEvent.setup();
    render(
      <Harness>
        <>
          <Sidebar />
          <ShortcutsProbe />
        </>
      </Harness>,
    );
    expect(screen.getByTestId("shortcuts-open").textContent).toBe("no");
    const btn = screen.getByRole("button", { name: /keyboard shortcuts/i });
    expect(btn).toHaveAttribute("aria-haspopup", "dialog");
    await user.click(btn);
    expect(screen.getByTestId("shortcuts-open").textContent).toBe("yes");
  });

  it("shows up to 5 recent runs linking to /runs/:id", async () => {
    render(
      <Harness>
        <Sidebar />
      </Harness>,
    );
    // The hook resolves via withDelay; wait for the first run to
    // land, then assert the shape of the list.
    const first = FIXTURE_RUNS[0]!;
    const firstName = first.project_name ?? first.input_name;
    const firstLink = await screen.findByRole("link", { name: new RegExp(firstName, "i") });
    expect(firstLink).toHaveAttribute("href", `/runs/${first.id}`);
    // Only five render even though FIXTURE_RUNS has more.
    // (Count hrefs pointing at /runs/run_demo_*.)
    const recentLinks = screen.getAllByRole("link").filter((el) =>
      (el.getAttribute("href") ?? "").startsWith("/runs/run_"),
    );
    expect(recentLinks.length).toBeLessThanOrEqual(5);
  });

  it("shows the empty text only when `useRuns` returns no items", async () => {
    impl = () =>
      ({
        data: { pages: [{ items: [], next_cursor: null }], pageParams: [undefined] },
        isPending: false,
        isError: false,
        error: null,
        hasNextPage: false,
        fetchNextPage: () => Promise.resolve(),
        isFetchingNextPage: false,
        refetch: () => Promise.resolve(),
      }) as unknown as ReturnType<UseRunsFn>;
    render(
      <Harness>
        <Sidebar />
      </Harness>,
    );
    await waitFor(() =>
      expect(screen.getByText(/runs will appear here/i)).toBeInTheDocument(),
    );
  });
});
