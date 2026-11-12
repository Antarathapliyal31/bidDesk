import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

/**
 * Open-state container for the "?" shortcuts sheet, plus the WCAG
 * 2.1.4 single-key-shortcuts toggle (default on, persisted in
 * localStorage). Kept separate from the command-menu context so
 * the two can open independently and don't steal focus from each
 * other.
 *
 * When `singleKeyEnabled` is false, only the ⌘K / Ctrl+K combo
 * fires in `useGlobalShortcuts` — letter chords and "?" are muted.
 */
const LS_KEY = "biddesk.singleKeyShortcutsEnabled";

function readPersistedEnabled(): boolean {
  try {
    const raw = typeof localStorage === "undefined" ? null : localStorage.getItem(LS_KEY);
    if (raw === null) return true;
    return raw !== "false";
  } catch {
    return true;
  }
}

function writePersistedEnabled(v: boolean): void {
  try {
    if (typeof localStorage === "undefined") return;
    localStorage.setItem(LS_KEY, v ? "true" : "false");
  } catch {
    /* localStorage can be disabled (Safari private, SSR, etc.) — ignore. */
  }
}

type ShortcutsCtx = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
  singleKeyEnabled: boolean;
  setSingleKeyEnabled: (v: boolean) => void;
};

const Ctx = createContext<ShortcutsCtx | null>(null);

/**
 * Raw context handle for consumers that need to read the value
 * optionally. `useBidKeyboard` uses this so tests don't need to
 * wrap in the full provider tree — missing provider → single-key
 * shortcuts default to enabled.
 */
export const ShortcutsSheetCtx = Ctx;

export function ShortcutsProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [singleKeyEnabled, setSingleKeyEnabledRaw] = useState<boolean>(() =>
    readPersistedEnabled(),
  );

  // Sync any external storage edits (rare, but cheap).
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === LS_KEY) {
        setSingleKeyEnabledRaw(readPersistedEnabled());
      }
    }
    if (typeof window === "undefined") return;
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const toggle = useCallback(() => setOpen((v) => !v), []);

  const setSingleKeyEnabled = useCallback((v: boolean) => {
    setSingleKeyEnabledRaw(v);
    writePersistedEnabled(v);
  }, []);

  const value = useMemo(
    () => ({ open, setOpen, toggle, singleKeyEnabled, setSingleKeyEnabled }),
    [open, toggle, singleKeyEnabled, setSingleKeyEnabled],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useShortcutsSheet(): ShortcutsCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useShortcutsSheet must be used inside ShortcutsProvider");
  return v;
}
