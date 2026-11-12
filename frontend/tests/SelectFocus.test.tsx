/**
 * Radix Select focus-return on close.
 *
 * Two intertwined expectations:
 *   - Pointer close (user clicks an item): skip the focus-restore
 *     to the trigger so the next outside click isn't swallowed by
 *     Radix's brief `body { pointer-events: none }` window
 *     (radix-ui #1859).
 *   - Keyboard close (user Enters on an item): keep the default
 *     focus-return to the trigger so Tab order stays coherent.
 *
 * The file is isolated so the `vi.mock("@/api")` for `useBranches`
 * doesn't leak into unrelated R-finding tests.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { createTestQueryClient } from "@/app/queryClient";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { FIXTURE_BRANCHES } from "@/dev-fixtures";
import { BranchSelect } from "@/features/new-bid/BranchSelect";

// Scoped mock: only this file. `useBranches` resolves synchronously
// so the dropdown is interactive without the fixture's 600ms delay.
vi.mock("@/api", async (orig) => {
  const actual = await orig<typeof import("@/api")>();
  return {
    ...actual,
    useBranches: () => ({
      data: FIXTURE_BRANCHES,
      isPending: false,
      isError: false,
      error: null,
      refetch: () => Promise.resolve(),
    }),
  };
});

afterEach(() => {
  vi.clearAllMocks();
});

function Harness({ onOutsideClick }: { onOutsideClick: () => void }) {
  const qc = createTestQueryClient();
  return (
    <QueryClientProvider client={qc as unknown as QueryClient}>
      <div>
        <BranchSelect
          value={null}
          onChange={() => {
            /* ignore */
          }}
        />
        <button type="button" onClick={onOutsideClick} data-testid="outside">
          Run
        </button>
      </div>
    </QueryClientProvider>
  );
}

describe("SelectContent focus-return discrimination", () => {
  it("pointer close: first click on an outside button fires its handler (no swallow)", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Harness onOutsideClick={onClick} />);
    const trigger = await screen.findByRole("combobox");
    await user.click(trigger);
    const firstItem = await screen.findByRole("option", {
      name: new RegExp(FIXTURE_BRANCHES[0]!.name, "i"),
    });
    // Pointer selection: userEvent.click dispatches
    // pointerdown + pointerup + click.
    await user.click(firstItem);
    // Trigger should NOT be the active element (we skipped the
    // auto-focus-return).
    expect(document.activeElement).not.toBe(trigger);
    // The first click on the outside button fires its handler.
    const outside = screen.getByTestId("outside");
    await user.click(outside);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("keyboard close: trigger gets focus back (default accessibility)", async () => {
    const user = userEvent.setup();
    render(<Harness onOutsideClick={() => {}} />);
    const trigger = await screen.findByRole("combobox");
    // Focus the trigger and open via keyboard.
    trigger.focus();
    expect(document.activeElement).toBe(trigger);
    await user.keyboard("{Enter}");
    // The options list is now open; find the first option and
    // activate with Enter.
    const firstItem = await screen.findByRole("option", {
      name: new RegExp(FIXTURE_BRANCHES[0]!.name, "i"),
    });
    expect(firstItem).toBeDefined();
    // Radix Select auto-focuses the (first) item once open.
    // Pressing Enter activates it.
    await user.keyboard("{Enter}");
    // Focus should return to the trigger (keyboard close path).
    expect(document.activeElement).toBe(trigger);
  });

  it("Escape (keyboard) close: trigger also gets focus back", async () => {
    const user = userEvent.setup();
    render(<Harness onOutsideClick={() => {}} />);
    const trigger = await screen.findByRole("combobox");
    trigger.focus();
    await user.keyboard("{Enter}"); // open
    await screen.findByRole("listbox");
    await user.keyboard("{Escape}"); // close, no selection
    expect(document.activeElement).toBe(trigger);
  });

  it("body.style.pointerEvents is reset to '' on close (real-browser workaround for radix-ui #1859)", async () => {
    const user = userEvent.setup();
    render(<Harness onOutsideClick={() => {}} />);
    const trigger = await screen.findByRole("combobox");
    await user.click(trigger);
    const firstItem = await screen.findByRole("option", {
      name: new RegExp(FIXTURE_BRANCHES[0]!.name, "i"),
    });
    // Simulate Chrome's scroll-lock behaviour that Radix sometimes
    // leaves in place: set body pointer-events to 'none' just
    // before the Select closes. The close path should clear it so
    // the next outside click isn't swallowed.
    document.body.style.pointerEvents = "none";
    await user.click(firstItem);
    // After close, the lock is gone.
    expect(document.body.style.pointerEvents).toBe("");
  });
});

describe("SelectContent pointer-lock guard: nested inside a Dialog", () => {
  it("Select closing inside an open Dialog LEAVES body pointer-events locked", async () => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={createTestQueryClient() as unknown as QueryClient}>
        {/* Open Radix Dialog with a BranchSelect inside it. The
            Dialog's scroll-lock drives body pointer-events: none;
            when the Select closes, our workaround must NOT steal
            that lock — the Dialog still owns it. */}
        <Dialog defaultOpen>
          <DialogContent aria-describedby={undefined}>
            <DialogTitle>Pick a branch</DialogTitle>
            <BranchSelect
              value={null}
              onChange={() => {
                /* ignore */
              }}
            />
          </DialogContent>
        </Dialog>
      </QueryClientProvider>,
    );
    // Confirm the Dialog mounted.
    expect(screen.getByRole("dialog")).toBeInTheDocument();

    const trigger = await screen.findByRole("combobox");
    await user.click(trigger);
    const firstItem = await screen.findByRole("option", {
      name: new RegExp(FIXTURE_BRANCHES[0]!.name, "i"),
    });
    // Simulate the Dialog's scroll-lock (Radix sets this when the
    // Dialog opens in a real browser).
    document.body.style.pointerEvents = "none";
    await user.click(firstItem);
    // Dialog is still open → the lock stays. Our Select workaround
    // defers to the Dialog's own cleanup.
    expect(document.body.style.pointerEvents).toBe("none");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });
});
