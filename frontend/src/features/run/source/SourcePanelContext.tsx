/**
 * SourcePanel context + provider (SPEC §11.2).
 *
 * One panel per RunPage. Opening a citation sets `source` + `pdfUrl`
 * + `open=true`; the side-mounted `<SourcePanel />` subscribes and
 * renders. A later `openSource` call replaces the current pair
 * (no stacking — we have one panel, not a history).
 *
 * The `pdfUrl` is nullable so vision-only sources (no text PDF) can
 * still open the panel and show the "Read from image" callout.
 */
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import type { SourceRef } from "@/api";

export type SourcePanelState = {
  open: boolean;
  source: SourceRef | null;
  pdfUrl: string | null;
};

type Api = SourcePanelState & {
  openSource: (source: SourceRef, pdfUrl: string | null) => void;
  close: () => void;
};

const Context = createContext<Api | null>(null);

const INITIAL: SourcePanelState = { open: false, source: null, pdfUrl: null };

export function SourcePanelProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SourcePanelState>(INITIAL);

  const openSource = useCallback((source: SourceRef, pdfUrl: string | null) => {
    setState({ open: true, source, pdfUrl });
  }, []);

  const close = useCallback(() => {
    // Keep `source` / `pdfUrl` around on close so the exit animation
    // has something to render while Radix transitions out. The next
    // openSource() replaces them cleanly.
    setState((s) => ({ ...s, open: false }));
  }, []);

  const value = useMemo<Api>(
    () => ({ ...state, openSource, close }),
    [state, openSource, close],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSourcePanel(): Api {
  const v = useContext(Context);
  if (v === null) {
    throw new Error(
      "useSourcePanel must be used inside <SourcePanelProvider>. RunPage wraps its children with it.",
    );
  }
  return v;
}
