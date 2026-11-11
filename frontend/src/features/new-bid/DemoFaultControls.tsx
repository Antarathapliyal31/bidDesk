import { AlertTriangleIcon, ChevronRightIcon } from "lucide-react";
import { useId, useState } from "react";

import { useFaults, type FaultKey } from "@/api";
import { cn } from "@/lib/utils";

/**
 * Demo failure-injection controls (SPEC §10.6). Pulls the fault list
 * + `enabled` flag from `useFaults()` (Phase 5 merge: `/api/meta`).
 * Renders NOTHING when the server reports `enabled: false` — so
 * production never shows this card.
 *
 * Each <label> wraps only the title text (keeps the accessible name
 * tight); the longer description lives in a sibling element linked
 * via `aria-describedby`. The toggle carries `aria-controls`
 * pointing at the collapsible list id.
 */
type Props = {
  selected: Set<FaultKey>;
  onToggle: (key: FaultKey, checked: boolean) => void;
};

export function DemoFaultControls({ selected, onToggle }: Props) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  const { data, isPending } = useFaults();

  if (isPending) return null;
  if (!data?.enabled) return null;

  return (
    <section className="rounded-md border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={listId}
        className="flex w-full items-center gap-2 px-4 py-3 text-left text-sm font-medium"
      >
        <AlertTriangleIcon className="size-4 text-injected-violet-text" aria-hidden />
        <span className="flex-1">Demo failure injection</span>
        {selected.size > 0 ? (
          <span className="tabular text-xs text-muted-foreground">{selected.size} selected</span>
        ) : null}
        <ChevronRightIcon
          className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-90")}
          aria-hidden
        />
      </button>
      {open ? (
        <ul id={listId} role="list" className="divide-y divide-border border-t border-border">
          {data.faults.map((f) => {
            const id = `fault-${f.key}`;
            const descId = `${id}-desc`;
            const checked = selected.has(f.key);
            return (
              <li key={f.key} className="flex items-start gap-3 px-4 py-2.5">
                <input
                  id={id}
                  type="checkbox"
                  className="mt-0.5 size-4 rounded border-border accent-primary"
                  checked={checked}
                  onChange={(e) => onToggle(f.key, e.target.checked)}
                  aria-describedby={descId}
                />
                <div className="flex-1">
                  <label htmlFor={id} className="cursor-pointer text-sm font-medium">
                    {f.title}
                  </label>
                  <p id={descId} className="text-xs text-muted-foreground">
                    {f.description}
                  </p>
                </div>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}
