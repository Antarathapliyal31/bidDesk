import { useEffect, useRef } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { CommandMenu } from "./CommandMenu";
import { CommandMenuTrigger } from "./CommandMenuTrigger";
import { ShortcutsSheet } from "./ShortcutsSheet";
import { Sidebar } from "./Sidebar";
import { useGlobalShortcuts } from "./useGlobalShortcuts";

/**
 * Two-pane shell: fixed sidebar + scrollable main area. The top bar
 * on the right hosts the clickable command-menu search button —
 * reachable by click, keyboard (⌘K / Ctrl+K). The sidebar footer's
 * "Keyboard shortcuts" button opens the shortcuts sheet (not the
 * command menu).
 *
 * Accessibility:
 *   - "Skip to main content" is the first focusable element, hidden
 *     until it receives focus (keyboard users press Tab once to land
 *     here, Enter jumps past the sidebar).
 *   - <main id="main" tabIndex={-1}> is a focus target so SPA
 *     navigations can move focus there (screen readers announce the
 *     new page) without stealing scroll.
 *   - On every pathname change we reset main's scrollTop and focus
 *     it (preventScroll keeps the scroll reset from being stolen).
 */
export function AppShell() {
  const mainRef = useRef<HTMLElement>(null);
  const { pathname } = useLocation();
  useGlobalShortcuts();

  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    el.scrollTop = 0;
    el.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="flex h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:inline-flex focus:h-9 focus:items-center focus:rounded-md focus:bg-primary focus:px-4 focus:text-sm focus:font-medium focus:text-primary-foreground"
      >
        Skip to main content
      </a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-end border-b border-border bg-background px-6">
          <CommandMenuTrigger />
        </header>
        <main
          ref={mainRef}
          id="main"
          tabIndex={-1}
          className="min-w-0 flex-1 overflow-y-auto bg-background outline-none"
        >
          <Outlet />
        </main>
      </div>
      <CommandMenu />
      <ShortcutsSheet />
    </div>
  );
}
