import { FlagIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { type RunSummary } from "@/api";
import { Money } from "@/components/Money";
import { StatusBadge } from "@/components/StatusBadge";
import { formatShortDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * One row of the runs table (SPEC §11.1 History). Entirely a link
 * so a click / Enter navigates to the run page.
 *
 * Column grid: `120px minmax(0, 1fr) 120px 70px 70px 110px 110px`.
 * Project · Input gets the slack (`minmax(0, 1fr)` so truncate works
 * — a plain `1fr` keeps its content's intrinsic min-width). Branch
 * is narrower than before so project text doesn't crowd it.
 *
 * Numeric columns (Lines, Flags, Total) are right-aligned; headers
 * mirror the alignment. Created has extra left padding so it reads
 * as a separate group from the money cell.
 *
 * Lines + Flags render as "—" (not 0) when the run isn't terminal
 * with a priced bid (`running | failed | interrupted`): there isn't
 * yet a credible number to show.
 */
type Props = {
  run: RunSummary;
  selected: boolean;
  onSelect: () => void;
};

export const ROW_GRID = "grid-cols-[120px_minmax(0,1fr)_120px_70px_70px_110px_110px]";

function diffSummary(d: RunSummary["diff"]): string | null {
  if (!d) return null;
  const parts: string[] = [];
  if (d.changed > 0) parts.push(`${d.changed} edited`);
  if (d.added > 0) parts.push(`${d.added} added`);
  if (d.deleted > 0) parts.push(`${d.deleted} deleted`);
  if (d.accepted > 0) parts.push(`${d.accepted} accepted`);
  return parts.length > 0 ? parts.join(", ") : "No changes";
}

function hasPricedBid(status: RunSummary["status"]): boolean {
  return status === "completed" || status === "partial";
}

export function RunRow({ run, selected, onSelect }: Props) {
  const diff = diffSummary(run.diff);
  const finalized = run.finalized_at ? formatShortDate(run.finalized_at) : null;
  const subtitleParts: string[] = [run.input_name];
  if (diff) subtitleParts.push(diff);
  if (finalized) subtitleParts.push(`finalized ${finalized}`);
  const subtitle = subtitleParts.join(" · ");
  const createdShort = formatShortDate(run.created_at);
  const showNumbers = hasPricedBid(run.status);

  const projectName = run.project_name ?? "Untitled";

  return (
    <Link
      to={`/runs/${run.id}`}
      onFocus={onSelect}
      onMouseEnter={onSelect}
      className={cn(
        "grid items-center gap-4 border-b border-border px-4 py-2.5 text-sm transition-colors hover:bg-muted/40",
        ROW_GRID,
        selected && "bg-accent",
      )}
    >
      <StatusBadge status={run.status} />

      <div className="min-w-0">
        <p className="truncate font-medium text-foreground" title={projectName}>
          <span className="sr-only">Project </span>
          {run.project_name ? projectName : <span className="text-muted-foreground">Untitled</span>}
        </p>
        <p className="truncate text-xs text-muted-foreground" title={subtitle}>
          {subtitle}
        </p>
      </div>

      <p className="truncate text-xs text-muted-foreground" title={run.branch_name}>
        <span className="sr-only">Branch </span>
        {run.branch_name}
      </p>

      <p className="tabular text-right text-xs text-muted-foreground">
        {showNumbers ? (
          <>
            <span className="sr-only">{run.priced_lines} priced lines</span>
            <span aria-hidden>{run.priced_lines}</span>
          </>
        ) : (
          <>
            <span className="sr-only">priced lines not available yet</span>
            <span aria-hidden>—</span>
          </>
        )}
      </p>

      <p className="tabular text-right text-xs">
        {showNumbers ? (
          <>
            <span className="sr-only">{run.open_flags} open flags</span>
            {run.open_flags > 0 ? (
              <span
                aria-hidden
                className="inline-flex items-center gap-1 text-warn-amber-text"
              >
                <FlagIcon className="size-3.5" />
                {run.open_flags}
              </span>
            ) : (
              <span aria-hidden className="text-muted-foreground">
                0
              </span>
            )}
          </>
        ) : (
          <>
            <span className="sr-only">open flags not available yet</span>
            <span aria-hidden className="text-muted-foreground">
              —
            </span>
          </>
        )}
      </p>

      <span className="justify-self-end">
        <span className="sr-only">total </span>
        <Money cents={run.total_cents} />
      </span>

      <time
        dateTime={run.created_at}
        title={run.created_at}
        className="tabular pl-3 text-xs text-muted-foreground"
      >
        <span className="sr-only">Created </span>
        {createdShort}
      </time>
    </Link>
  );
}
