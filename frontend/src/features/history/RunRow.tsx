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
 * sr-only labels sit inline so a screen reader hears
 * "<status>, project <name>, branch <name>, N priced lines, N open
 * flags, total <money>, created <date>" without us drawing a <table>
 * (the row has to be a <Link>, so proper table semantics don't fit).
 *
 * The visible created date is short; the hoverable `title` carries
 * the full ISO timestamp for precise auditing. Finalized date
 * (when present) appears in the secondary line next to the diff.
 */
type Props = {
  run: RunSummary;
  selected: boolean;
  onSelect: () => void;
};

function diffSummary(d: RunSummary["diff"]): string | null {
  if (!d) return null;
  const parts: string[] = [];
  if (d.changed > 0) parts.push(`${d.changed} edited`);
  if (d.added > 0) parts.push(`${d.added} added`);
  if (d.deleted > 0) parts.push(`${d.deleted} deleted`);
  if (d.accepted > 0) parts.push(`${d.accepted} accepted`);
  return parts.length > 0 ? parts.join(", ") : "No changes";
}

export function RunRow({ run, selected, onSelect }: Props) {
  const diff = diffSummary(run.diff);
  const finalized = run.finalized_at ? formatShortDate(run.finalized_at) : null;
  const subtitleParts: string[] = [run.input_name];
  if (diff) subtitleParts.push(diff);
  if (finalized) subtitleParts.push(`finalized ${finalized}`);
  const subtitle = subtitleParts.join(" · ");
  const createdShort = formatShortDate(run.created_at);

  return (
    <Link
      to={`/runs/${run.id}`}
      onFocus={onSelect}
      onMouseEnter={onSelect}
      className={cn(
        "grid grid-cols-[140px_1fr_160px_70px_70px_120px_110px] items-center gap-4 border-b border-border px-4 py-2.5 text-sm transition-colors hover:bg-muted/40",
        selected && "bg-accent",
      )}
    >
      <StatusBadge status={run.status} />

      <div className="min-w-0">
        <p className="truncate font-medium text-foreground">
          <span className="sr-only">Project </span>
          {run.project_name ?? <span className="text-muted-foreground">Untitled</span>}
        </p>
        <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
      </div>

      <p className="truncate text-xs text-muted-foreground">
        <span className="sr-only">Branch </span>
        {run.branch_name}
      </p>

      <p className="tabular text-right text-xs text-muted-foreground">
        <span className="sr-only">{run.priced_lines} priced lines</span>
        <span aria-hidden>{run.priced_lines}</span>
      </p>

      <p className="tabular text-right text-xs">
        <span className="sr-only">{run.open_flags} open flags</span>
        {run.open_flags > 0 ? (
          <span aria-hidden className="inline-flex items-center gap-1 text-warn-amber-text">
            <FlagIcon className="size-3.5" />
            {run.open_flags}
          </span>
        ) : (
          <span aria-hidden className="text-muted-foreground">
            0
          </span>
        )}
      </p>

      <span className="justify-self-end">
        <span className="sr-only">total </span>
        <Money cents={run.total_cents} />
      </span>

      <time
        dateTime={run.created_at}
        title={run.created_at}
        className="tabular text-xs text-muted-foreground"
      >
        <span className="sr-only">Created </span>
        {createdShort}
      </time>
    </Link>
  );
}
