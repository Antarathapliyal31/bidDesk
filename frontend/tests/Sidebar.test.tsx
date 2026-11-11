/**
 * Sidebar accessibility + behavior (SPEC §11.2):
 *   - inner <nav aria-label="Primary"> is the navigation landmark.
 *   - the active route link has aria-current="page" (NavLink v6).
 *   - the footer "Keyboard shortcuts" button opens the shortcuts sheet.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Sidebar } from "../src/app/Sidebar";
import { ShortcutsProvider, useShortcutsSheet } from "../src/app/ShortcutsContext";

function Harness({ children, route = "/new" }: { children: React.ReactNode; route?: string }) {
  return (
    <MemoryRouter initialEntries={[route]}>
      <ShortcutsProvider>{children}</ShortcutsProvider>
    </MemoryRouter>
  );
}

function ShortcutsProbe() {
  const { open } = useShortcutsSheet();
  return <div data-testid="shortcuts-open">{open ? "yes" : "no"}</div>;
}

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
    const history = screen.getByRole("link", { name: /history/i });
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
});
