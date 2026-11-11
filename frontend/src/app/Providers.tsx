import { QueryClientProvider } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { BrowserRouter } from "react-router-dom";

import { Toaster } from "@/components/ui/sonner";

import { CommandMenuProvider } from "./CommandMenuContext";
import { createQueryClient } from "./queryClient";
import { ShortcutsProvider } from "./ShortcutsContext";
import { startThemeSync } from "./theme";

/**
 * App-wide providers (SPEC §11.3). Router innermost so route-changing
 * components live inside QueryClient + the UI contexts. Theme sync
 * starts here so dark-mode flips track the OS preference while the
 * app runs (initial state is set by the inline script in index.html
 * — see F3/F4 in DECISIONS.md).
 */
type Props = {
  children: React.ReactNode;
  /** Override the QueryClient in tests via createTestQueryClient(). */
  queryClient?: QueryClient;
  /**
   * Router override for tests. Pass react-router-dom's MemoryRouter
   * (with `initialEntries`) to drive route state deterministically.
   */
  Router?: React.ComponentType<{ children: React.ReactNode }>;
};

export function Providers({ children, queryClient, Router = BrowserRouter }: Props) {
  const qc = useMemo(() => queryClient ?? createQueryClient(), [queryClient]);
  useEffect(() => startThemeSync(), []);
  return (
    <QueryClientProvider client={qc}>
      <CommandMenuProvider>
        <ShortcutsProvider>
          <Router>{children}</Router>
          <Toaster position="bottom-right" closeButton />
        </ShortcutsProvider>
      </CommandMenuProvider>
    </QueryClientProvider>
  );
}
