/**
 * Global shortcut behavior (SPEC §11.5 + WCAG 2.1.4):
 *   n → /new           typing in input suppresses
 *   g h → /runs        chord times out after 1s
 *   ⌘K / Ctrl+K        always toggles (even from inputs)
 *   modal open         suppresses letter shortcuts (⌘K still works)
 *   switch off         only ⌘K works
 */
import { act, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandMenuProvider, useCommandMenu } from "../src/app/CommandMenuContext";
import {
  ShortcutsProvider,
  useShortcutsSheet,
} from "../src/app/ShortcutsContext";
import { useGlobalShortcuts } from "../src/app/useGlobalShortcuts";

function Harness({ children }: { children?: React.ReactNode }) {
  useGlobalShortcuts();
  return <>{children}</>;
}

function LocationProbe() {
  const loc = useLocation();
  return <span data-testid="loc">{loc.pathname}</span>;
}

function CommandOpenProbe() {
  const { open } = useCommandMenu();
  return <span data-testid="cmd-open">{open ? "yes" : "no"}</span>;
}

function ShortcutsOpenProbe() {
  const { open } = useShortcutsSheet();
  return <span data-testid="sh-open">{open ? "yes" : "no"}</span>;
}

function renderApp(options: { initialPath?: string; withInput?: boolean; withModal?: boolean } = {}) {
  const { initialPath = "/new", withInput = false, withModal = false } = options;
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <CommandMenuProvider>
        <ShortcutsProvider>
          <Harness>
            <LocationProbe />
            <CommandOpenProbe />
            <ShortcutsOpenProbe />
            {withInput ? <input data-testid="field" /> : null}
            {withModal ? (
              <div role="dialog" aria-modal="true" data-testid="modal">
                Modal
              </div>
            ) : null}
            <Routes>
              <Route path="*" element={<span />} />
            </Routes>
          </Harness>
        </ShortcutsProvider>
      </CommandMenuProvider>
    </MemoryRouter>,
  );
}

function press(key: string, init: KeyboardEventInit = {}, target: Element = document.body) {
  const ev = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  act(() => {
    target.dispatchEvent(ev);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  try {
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useGlobalShortcuts", () => {
  it("pressing 'n' navigates to /new", () => {
    renderApp({ initialPath: "/runs" });
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
    press("n");
    expect(screen.getByTestId("loc").textContent).toBe("/new");
  });

  it("pressing 'n' inside an input does nothing", () => {
    renderApp({ initialPath: "/runs", withInput: true });
    const field = screen.getByTestId("field") as HTMLInputElement;
    field.focus();
    press("n", {}, field);
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
  });

  it("'g' then 'h' navigates to /runs", () => {
    renderApp({ initialPath: "/new" });
    press("g");
    press("h");
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
  });

  it("'g', then 1.1s, then 'h' does nothing (chord timed out)", () => {
    renderApp({ initialPath: "/new" });
    press("g");
    act(() => {
      vi.advanceTimersByTime(1100);
    });
    press("h");
    expect(screen.getByTestId("loc").textContent).toBe("/new");
  });

  it("the mod-K combo toggles the command menu even when focus is in an input", () => {
    // In jsdom, navigator.platform is empty so hasModKey reads
    // ctrlKey. The behavior under test ("mod-K always fires, even
    // from an input") is identical on both platforms.
    renderApp({ withInput: true });
    const field = screen.getByTestId("field") as HTMLInputElement;
    field.focus();
    expect(screen.getByTestId("cmd-open").textContent).toBe("no");
    press("k", { ctrlKey: true }, field);
    expect(screen.getByTestId("cmd-open").textContent).toBe("yes");
  });

  it("letter shortcuts are suppressed while a modal dialog is open; ⌘K still fires", () => {
    renderApp({ initialPath: "/runs", withModal: true });
    press("n");
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
    press("k", { ctrlKey: true });
    expect(screen.getByTestId("cmd-open").textContent).toBe("yes");
  });

  it("with the single-key switch OFF, 'n' does nothing but ⌘K still fires", () => {
    function FlipSwitch() {
      const { setSingleKeyEnabled } = useShortcutsSheet();
      return (
        <button data-testid="flip" onClick={() => setSingleKeyEnabled(false)}>
          off
        </button>
      );
    }
    render(
      <MemoryRouter initialEntries={["/runs"]}>
        <CommandMenuProvider>
          <ShortcutsProvider>
            <Harness>
              <LocationProbe />
              <CommandOpenProbe />
              <FlipSwitch />
              <Routes>
                <Route path="*" element={<span />} />
              </Routes>
            </Harness>
          </ShortcutsProvider>
        </CommandMenuProvider>
      </MemoryRouter>,
    );
    act(() => {
      screen.getByTestId("flip").click();
    });
    press("n");
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
    press("k", { ctrlKey: true });
    expect(screen.getByTestId("cmd-open").textContent).toBe("yes");
  });

  it("ignores repeat, composing and already-prevented events", () => {
    renderApp({ initialPath: "/runs" });
    press("n", { repeat: true });
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
    press("n", { isComposing: true });
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
    // A listener registered before ours calls preventDefault.
    const stopper = (e: Event) => e.preventDefault();
    window.addEventListener("keydown", stopper, { capture: true });
    press("n");
    window.removeEventListener("keydown", stopper, { capture: true });
    expect(screen.getByTestId("loc").textContent).toBe("/runs");
  });
});
