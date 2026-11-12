import { useCallback, useEffect, useRef, useState } from "react";

import { type RunSummary } from "@/api";
import { cn } from "@/lib/utils";

import { ROW_GRID, RunRow } from "./RunRow";

/**
 * Table of runs (SPEC §11.1 History). Keyboard-first:
 *   - j / k move the active row (focus follows).
 *   - Enter follows the link (native <a> behavior).
 *   - j/k suppressed inside editable targets.
 *
 * Header columns use plain text (sr-only labels live on each row
 * cell) so a screen reader hears the column meaning per row — the
 * link row has to be an <a>, so proper table semantics don't fit.
 */
type Props = {
  runs: RunSummary[];
};

function isEditable(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  const tag = t.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (t.isContentEditable) return true;
  return false;
}

export function RunsTable({ runs }: Props) {
  const [active, setActive] = useState(0);
  const rowRefs = useRef<Record<string, HTMLElement | null>>({});

  const move = useCallback(
    (delta: 1 | -1) => {
      setActive((i) => {
        const next = Math.min(runs.length - 1, Math.max(0, i + delta));
        const run = runs[next];
        if (run) {
          const el = rowRefs.current[run.id];
          el?.focus();
        }
        return next;
      });
    },
    [runs],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isEditable(e.target)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "j") {
        e.preventDefault();
        move(1);
      } else if (e.key === "k") {
        e.preventDefault();
        move(-1);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move]);

  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <div
        className={cn(
          "grid items-center gap-4 border-b border-border bg-muted/50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground",
          "whitespace-nowrap",
          ROW_GRID,
        )}
      >
        <span>Status</span>
        <span>Project · Input</span>
        <span>Branch</span>
        <span className="text-right">Lines</span>
        <span className="text-right">Flags</span>
        <span className="text-right">Total</span>
        <span className="pl-3">Created</span>
      </div>
      <div role="list" aria-label="Runs">
        {runs.map((r, i) => (
          <div
            key={r.id}
            role="listitem"
            ref={(el) => {
              rowRefs.current[r.id] = el;
            }}
          >
            <RunRow run={r} selected={i === active} onSelect={() => setActive(i)} />
          </div>
        ))}
      </div>
    </div>
  );
}
