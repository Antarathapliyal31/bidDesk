/**
 * ShortcutsSheet (SPEC §11.5 + WCAG 2.1.4):
 *   - checkbox has the accessible name "Single-key shortcuts".
 *   - toggling the switch off marks the n / g h / ? rows "(off)".
 *   - the sheet never mentions "Phase …" — shortcuts appear here
 *     only when they're actually implemented.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useEffect } from "react";
import { describe, expect, it } from "vitest";

import { ShortcutsProvider, useShortcutsSheet } from "../src/app/ShortcutsContext";
import { ShortcutsSheet } from "../src/app/ShortcutsSheet";

function Harness() {
  const { setOpen } = useShortcutsSheet();
  useEffect(() => setOpen(true), [setOpen]);
  return <ShortcutsSheet />;
}

function renderSheet() {
  return render(
    <ShortcutsProvider>
      <Harness />
    </ShortcutsProvider>,
  );
}

describe("ShortcutsSheet", () => {
  it("renders the switch with accessible name 'Single-key shortcuts'", () => {
    renderSheet();
    const cb = screen.getByRole("checkbox", { name: "Single-key shortcuts" });
    expect(cb).toBeInTheDocument();
    expect(cb).toBeChecked();
  });

  it("does not mention 'phase' anywhere (shortcuts land in the same change that implements them)", () => {
    renderSheet();
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText(/phase/i)).toBeNull();
  });

  it("toggling single-key off marks the n / g h / ? rows '(off)'; ⌘K stays unchanged", async () => {
    const user = userEvent.setup();
    renderSheet();
    const dialog = screen.getByRole("dialog");

    // Baseline: no "(off)" anywhere.
    expect(within(dialog).queryByText(/\(off\)/i)).toBeNull();

    await user.click(screen.getByRole("checkbox", { name: "Single-key shortcuts" }));

    // Each single-key row is annotated "(off)". The ⌘K row (first)
    // is not.
    const nRow = within(dialog).getByText("New bid").closest("li");
    const historyRow = within(dialog).getByText("History").closest("li");
    const sheetRow = within(dialog).getByText("This sheet").closest("li");
    const cmdRow = within(dialog).getByText("Open command menu").closest("li");

    expect(nRow).not.toBeNull();
    expect(historyRow).not.toBeNull();
    expect(sheetRow).not.toBeNull();
    expect(cmdRow).not.toBeNull();

    expect(within(nRow as HTMLElement).getByText(/\(off\)/i)).toBeInTheDocument();
    expect(within(historyRow as HTMLElement).getByText(/\(off\)/i)).toBeInTheDocument();
    expect(within(sheetRow as HTMLElement).getByText(/\(off\)/i)).toBeInTheDocument();
    expect(within(cmdRow as HTMLElement).queryByText(/\(off\)/i)).toBeNull();
  });
});
