/**
 * CommandMenuTrigger renders accessible search-like affordance that
 * opens the command menu. On non-Mac (jsdom default: empty
 * navigator.platform), the modifier key is labeled "Ctrl" and the
 * second Kbd is always "K" — no stray "+" character.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CommandMenuTrigger } from "../src/app/CommandMenuTrigger";
import { CommandMenuProvider, useCommandMenu } from "../src/app/CommandMenuContext";

function Harness({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const { open } = useCommandMenu();
  if (open) onOpenChange(true);
  return <CommandMenuTrigger />;
}

describe("CommandMenuTrigger", () => {
  it("is found by role button with accessible name containing 'search'", () => {
    render(
      <CommandMenuProvider>
        <CommandMenuTrigger />
      </CommandMenuProvider>,
    );
    const btn = screen.getByRole("button", { name: /search/i });
    expect(btn).toBeInTheDocument();
  });

  it("on non-Mac, renders 'Ctrl' and 'K' pills without a stray '+'", () => {
    render(
      <CommandMenuProvider>
        <CommandMenuTrigger />
      </CommandMenuProvider>,
    );
    const btn = screen.getByRole("button", { name: /search/i });
    // Visible symbols — "Ctrl" is text, "K" is text on the second pill.
    expect(btn.textContent).toContain("Ctrl");
    expect(btn.textContent).toContain("K");
    expect(btn.textContent).not.toContain("+K");
    expect(btn.textContent).not.toContain("+");
  });

  it("opens the command menu on click", async () => {
    const onOpen = vi.fn();
    const user = userEvent.setup();
    render(
      <CommandMenuProvider>
        <Harness onOpenChange={onOpen} />
      </CommandMenuProvider>,
    );
    await user.click(screen.getByRole("button", { name: /search/i }));
    expect(onOpen).toHaveBeenCalledWith(true);
  });
});
