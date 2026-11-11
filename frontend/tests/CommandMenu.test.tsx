/**
 * CommandMenu behavior (SPEC §11.5):
 *   - typing "runs" surfaces History (cmdk `keywords` wiring).
 *   - selecting "New bid" closes the menu and leaves focus on <main>
 *     (via AppShell's location effect + onCloseAutoFocus pattern).
 *   - selecting "Keyboard shortcuts" leaves focus inside the
 *     shortcuts dialog.
 *
 * jsdom has no Radix open/close animations, so this file cannot
 * reproduce the close-animation focus race (where
 * returnFocusTarget's click handler fires during the Radix fade).
 * TODO (Phase 9 / §12.4): add a Playwright test that exercises the
 * command-menu path end-to-end with real animations.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import App from "../src/App";

async function openCommandMenu(user: ReturnType<typeof userEvent.setup>) {
  // The top-right "Search" button inside AppShell opens the menu.
  // Clicking it goes through the real context (same path as ⌘K).
  await user.click(screen.getByRole("button", { name: /search/i }));
  await waitFor(() => expect(screen.getByRole("dialog")).toBeInTheDocument());
}

afterEach(() => {
  // Each test may navigate; reset for the next render() call.
  history.replaceState({}, "", "/");
});

describe("CommandMenu", () => {
  it("surfaces History when the user types 'runs' (cmdk keywords)", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openCommandMenu(user);

    await user.type(screen.getByPlaceholderText(/type a command/i), "runs");

    expect(screen.getByRole("option", { name: /history/i })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /new bid/i })).toBeNull();
  });

  it("'New bid' navigates and leaves focus on <main>", async () => {
    history.replaceState({}, "", "/runs");
    const user = userEvent.setup();
    render(<App />);
    await openCommandMenu(user);
    await user.click(screen.getByRole("option", { name: /new bid/i }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // AppShell moves focus to <main> on every pathname change.
    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole("main"));
    });
  });

  it("'Keyboard shortcuts' leaves focus inside the shortcuts dialog", async () => {
    const user = userEvent.setup();
    render(<App />);
    await openCommandMenu(user);
    await user.click(screen.getByRole("option", { name: /keyboard shortcuts/i }));
    await waitFor(() => {
      // The sheet is now the only [role=dialog] in the DOM.
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent(/keyboard shortcuts/i);
      expect(dialog.contains(document.activeElement)).toBe(true);
    });
  });
});
