/**
 * Bid workspace keyboard shortcuts (SPEC §11.5). Scoped to the
 * Bid tab — the hook is only mounted while that tab is active.
 *
 *   j / k   move focused line down / up         (single-key)
 *   Enter   edit the focused cell               (single-key)
 *   Esc     cancel the current edit             (single-key)
 *   a       accept the focused line             (single-key)
 *   ] / [   next / prev open-review line        (single-key)
 *   r       re-price the focused line           (single-key)
 *   ⌘↵      finalize                            (always fires)
 *
 * WCAG 2.1.4 — single-key shortcuts honour the "Single-key
 * shortcuts" toggle from `useShortcutsSheet`. When the user has
 * turned them off, only the ⌘/Ctrl combo (Finalize) fires;
 * everything else quietly no-ops. Matches the policy already in
 * `useGlobalShortcuts`.
 *
 * Suppressed regardless of the toggle when:
 *   - focus is in an editable element (we don't eat typed chars)
 *   - a modal dialog is open (reprice / price override / confirm)
 *   - the event is a repeat, IME composing, or defaultPrevented
 *
 * The hook takes a bundle of callbacks; the parent decides what
 * each action actually does.
 */
import { useContext, useEffect } from "react";

import { hasModKey } from "@/app/platform";
import { ShortcutsSheetCtx } from "@/app/ShortcutsContext";

export type UseBidKeyboardApi = {
  /** Called on `j` / `k`. Caller handles bounds / wrap. */
  moveFocus: (delta: 1 | -1) => void;
  /** `Enter` on a focused row — parent picks the default field. */
  startEditDefault: () => void;
  /** `Esc` — cancel the active edit. */
  cancelEdit: () => void;
  /** `a` — accept the focused line. */
  acceptFocused: () => void;
  /** `]` / `[` — next / previous open-review line. */
  jumpFlag: (direction: 1 | -1) => void;
  /** `r` — re-price the focused line. */
  repriceFocused: () => void;
  /** `⌘/Ctrl + Enter` — finalize the bid. */
  finalize: () => void;
};

function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  const role = t.getAttribute("role");
  if (role === "textbox" || role === "combobox" || role === "searchbox") return true;
  return false;
}

function modalOpen(): boolean {
  return (
    typeof document !== "undefined" &&
    document.querySelector('[role="dialog"][aria-modal="true"]') !== null
  );
}

export function useBidKeyboard(active: boolean, api: UseBidKeyboardApi): void {
  // Read the toggle directly from context. Falls back to `true`
  // (enabled) when the provider is absent — matters for isolated
  // component tests that don't wrap in the full AppShell.
  const ctx = useContext(ShortcutsSheetCtx);
  const singleKeyEnabled = ctx?.singleKeyEnabled ?? true;
  useEffect(() => {
    if (!active) return undefined;

    function onKey(e: KeyboardEvent) {
      if (e.repeat || e.isComposing || e.defaultPrevented) return;

      // ⌘/Ctrl+Enter fires regardless of focus OR toggle — the
      // user explicitly pressed the mod key, so Finalize always
      // works. Keep this ABOVE the editable-target and modal
      // guards.
      if (e.key === "Enter" && hasModKey(e)) {
        e.preventDefault();
        api.finalize();
        return;
      }

      // Esc inside an editable target gets delegated to the edit
      // widget (it handles cancel itself). We only intercept Esc
      // when focus is on a row.
      const editable = isEditableTarget(e.target);
      if (e.key === "Escape" && !editable) {
        e.preventDefault();
        api.cancelEdit();
        return;
      }

      // Every other shortcut: suppress while typing, under a
      // modal, or when the user has turned single-key shortcuts
      // off (WCAG 2.1.4).
      if (editable) return;
      if (modalOpen()) return;
      if (!singleKeyEnabled) return;

      switch (e.key) {
        case "j":
          e.preventDefault();
          api.moveFocus(1);
          return;
        case "k":
          e.preventDefault();
          api.moveFocus(-1);
          return;
        case "Enter":
          e.preventDefault();
          api.startEditDefault();
          return;
        case "a":
          e.preventDefault();
          api.acceptFocused();
          return;
        case "]":
          e.preventDefault();
          api.jumpFlag(1);
          return;
        case "[":
          e.preventDefault();
          api.jumpFlag(-1);
          return;
        case "r":
          e.preventDefault();
          api.repriceFocused();
          return;
      }
    }

    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, api, singleKeyEnabled]);
}
