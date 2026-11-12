import { FlagIcon } from "lucide-react";

import type { BidLine } from "@/api";
import { Kbd } from "@/components/Kbd";
import { reasonCodeLabel } from "@/lib/reasonCodes";

/**
 * Review queue (SPEC §11.2). Lists every line currently in review
 * state "open" with its reasons. Clicking a row scrolls the line
 * into view; `]` / `[` cycle next / previous via the bid keyboard
 * hook.
 *
 * Compact bar at the top of the workspace; collapses to empty text
 * ("No lines need review") when the queue is clear.
 */
type Props = {
  lines: BidLine[];
  focusedLineId: string | null;
  onJump: (lineId: string) => void;
};

export function ReviewQueue({ lines, focusedLineId, onJump }: Props) {
  const openLines = lines.filter((l) => l.review.state === "open");
  if (openLines.length === 0) {
    return (
      <section
        aria-label="Review queue"
        className="rounded-md border border-border bg-card px-4 py-3 text-xs text-muted-foreground"
      >
        No lines need review.
      </section>
    );
  }
  return (
    <section
      aria-label="Review queue"
      className="rounded-md border border-border bg-card"
    >
      <header className="flex items-center justify-between border-b border-border px-4 py-2">
        <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-warn-amber-text">
          <FlagIcon className="size-3.5" aria-hidden />
          Review queue · {openLines.length}
        </div>
        <p className="text-xs text-muted-foreground">
          <Kbd>]</Kbd> next · <Kbd>[</Kbd> previous
        </p>
      </header>
      <ul className="divide-y divide-border">
        {openLines.map((line) => {
          const reasons = line.review.reasons.map((r) => reasonCodeLabel(r));
          const isFocused = line.line_id === focusedLineId;
          return (
            <li key={line.line_id}>
              <button
                type="button"
                onClick={() => onJump(line.line_id)}
                className={
                  "block w-full px-4 py-2 text-left text-sm transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:bg-muted " +
                  (isFocused ? "bg-muted/50" : "")
                }
                aria-current={isFocused ? "true" : undefined}
              >
                <span className="truncate font-medium text-foreground">
                  {line.description}
                </span>
                <span className="ml-2 text-xs text-warn-amber-text">
                  {reasons.join(", ")}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
