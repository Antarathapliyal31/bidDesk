import { createContext, useCallback, useContext, useMemo, useState } from "react";

/**
 * Minimal context so any component (sidebar footer, top-right search
 * button, pages) can open/close the global ⌘K menu without prop
 * drilling. The menu's render tree lives in app/CommandMenu.tsx.
 */
type CommandMenuCtx = {
  open: boolean;
  setOpen: (open: boolean) => void;
  toggle: () => void;
};

const Ctx = createContext<CommandMenuCtx | null>(null);

export function CommandMenuProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const toggle = useCallback(() => setOpen((v) => !v), []);
  const value = useMemo(() => ({ open, setOpen, toggle }), [open, toggle]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useCommandMenu(): CommandMenuCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCommandMenu must be used inside CommandMenuProvider");
  return v;
}
