import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

import { useCommandMenu } from "./CommandMenuContext";
import { hasModKey } from "./platform";
import { useShortcutsSheet } from "./ShortcutsContext";

/**
 * Global keyboard shortcuts (SPEC §11.5):
 *   ⌘K / Ctrl+K   open/close command menu
 *   n             navigate to /new
 *   g h           chord: go to history
 *   ?             open the shortcuts sheet
 *
 * WCAG 2.1.4 (Shortcut Keys): single-letter and "?" shortcuts can
 * be disabled from the shortcuts sheet (default on, persisted). The
 * mod-key combo (⌘K) always fires — it's never accidental.
 *
 * Letter / "?" shortcuts are also suppressed when:
 *   - focus is in an editable element (input/textarea/select/
 *     contenteditable/role=textbox|searchbox|combobox); typing wins.
 *   - any `[role="dialog"][aria-modal="true"]` is open (so "n" won't
 *     silently nav while a confirm dialog is up). ⌘K still fires.
 *   - the event is a key repeat, an IME composition, or already
 *     defaultPrevented.
 *
 * Chord state is a 1-second window; any key that isn't the second
 * chord char disarms it.
 */
const CHORD_TIMEOUT_MS = 1000;

function isEditableTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  const role = t.getAttribute("role");
  if (role === "textbox" || role === "combobox" || role === "searchbox") return true;
  return false;
}

function isModalOpen(): boolean {
  if (typeof document === "undefined") return false;
  return document.querySelector('[role="dialog"][aria-modal="true"]') !== null;
}

export function useGlobalShortcuts(): void {
  const navigate = useNavigate();
  const { toggle: toggleCommand } = useCommandMenu();
  const { setOpen: setShortcutsOpen, singleKeyEnabled } = useShortcutsSheet();

  // Latest-value ref so the keydown listener (added once) always
  // reads current context state without re-binding. Updated in an
  // effect, not during render, to satisfy react-hooks/refs.
  const singleKeyRef = useRef(singleKeyEnabled);
  useEffect(() => {
    singleKeyRef.current = singleKeyEnabled;
  }, [singleKeyEnabled]);

  const chordTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const chordArmed = useRef<null | "g">(null);

  useEffect(() => {
    function disarmChord() {
      chordArmed.current = null;
      if (chordTimer.current) {
        clearTimeout(chordTimer.current);
        chordTimer.current = null;
      }
    }
    function armChord(key: "g") {
      chordArmed.current = key;
      if (chordTimer.current) clearTimeout(chordTimer.current);
      chordTimer.current = setTimeout(disarmChord, CHORD_TIMEOUT_MS);
    }

    function onKeyDown(e: KeyboardEvent) {
      if (e.repeat || e.isComposing || e.defaultPrevented) return;

      // ⌘K / Ctrl+K — ALWAYS fires (editable target, modal, toggle off
      // — doesn't matter). It's the explicit reach for the command menu.
      if (hasModKey(e) && !e.shiftKey && !e.altKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        disarmChord();
        toggleCommand();
        return;
      }

      // Everything below is a single-key shortcut.
      if (!singleKeyRef.current) {
        disarmChord();
        return;
      }
      if (isEditableTarget(e.target)) {
        disarmChord();
        return;
      }
      if (isModalOpen()) {
        disarmChord();
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) {
        disarmChord();
        return;
      }

      if (e.key === "?" || (e.shiftKey && e.key === "/")) {
        e.preventDefault();
        disarmChord();
        setShortcutsOpen(true);
        return;
      }

      if (chordArmed.current === "g") {
        if (e.key === "h") {
          e.preventDefault();
          disarmChord();
          navigate("/runs");
          return;
        }
        disarmChord();
      }

      if (e.key === "g") {
        e.preventDefault();
        armChord("g");
        return;
      }

      if (e.key === "n") {
        e.preventDefault();
        navigate("/new");
        return;
      }
    }

    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      disarmChord();
    };
  }, [navigate, toggleCommand, setShortcutsOpen]);
}
