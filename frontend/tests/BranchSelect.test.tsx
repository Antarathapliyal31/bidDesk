/**
 * BranchSelect states (SPEC §11.4):
 *   - pending → disabled trigger, placeholder reads "Loading branches…".
 *   - error   → InlineError + Try again; clicking Try again recovers
 *               and shows the options.
 *   - success → the branch list is rendered (Label "Branch" finds it).
 *
 * `useBranches` is mocked with a React-state toggle so "Try again"
 * flips the result from error to success without a real queryClient
 * dance (the live-fetch path is covered by the hook's own query
 * machinery, not this component test).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BranchSelect } from "../src/features/new-bid/BranchSelect";

vi.mock("@/api", async (orig) => {
  const actual = await orig<typeof import("@/api")>();
  return {
    ...actual,
    useBranches: function MockUseBranches() {
      const [state, setState] = useState<"error" | "ok">("error");
      if (state === "error") {
        return {
          data: undefined,
          isPending: false,
          isError: true,
          error: new Error("boom"),
          refetch: () => {
            setState("ok");
            return Promise.resolve();
          },
        };
      }
      return {
        data: [
          {
            id: "BR-NJ-01",
            name: "BidDesk North Jersey",
            city: "Edison",
            state: "NJ",
            tax_rate_ppm: 66250,
          },
        ],
        isPending: false,
        isError: false,
        error: null,
        refetch: () => Promise.resolve(),
      };
    },
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function Harness() {
  const [value, setValue] = useState<string | null>(null);
  return <BranchSelect value={value} onChange={setValue} />;
}

describe("BranchSelect", () => {
  it("error → click Try again → options appear", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    // Error state: no combobox yet, InlineError visible.
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn't load branches/i);
    expect(screen.queryByRole("combobox", { name: /branch/i })).toBeNull();

    const retry = screen.getByRole("button", { name: /try again/i });
    await user.click(retry);

    // After refetch: combobox renders, alert is gone.
    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: /branch/i })).toBeInTheDocument();
    });
    expect(screen.queryByText(/couldn't load branches/i)).toBeNull();
  });
});
