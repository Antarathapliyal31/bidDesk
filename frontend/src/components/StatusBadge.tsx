import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Status chip used in the run header, sidebar recent-runs and the
 * history table (SPEC §11.5). Every variant pairs a colored dot with
 * a text label — color alone is never load-bearing (color-blind safe,
 * WCAG AA against card/background surfaces).
 *
 * This is a static badge: the label text is visible, so no role/aria
 * is needed. Use aria-live="polite" on the parent region in the run
 * header (Phase 7) when the status changes while the user is reading.
 *
 * TODO (Phase 6 proper): once schema.gen.ts exists, assert
 *   `LABEL satisfies Record<components["schemas"]["RunStatus"], string>`
 * so a new backend status (e.g. "paused") fails the build until the UI
 * adds it here.
 */
const badge = cva(
  "inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      status: {
        running: "border-transparent bg-transparent text-run-blue-text",
        completed: "border-transparent bg-transparent text-done-green-text",
        partial: "border-transparent bg-transparent text-warn-amber-text",
        failed: "border-transparent bg-transparent text-fail-red-text",
        interrupted: "border-transparent bg-transparent text-muted-foreground",
      },
    },
    defaultVariants: { status: "running" },
  },
);

const dot = cva("size-2 rounded-full", {
  variants: {
    status: {
      running: "bg-run-blue",
      completed: "bg-done-green",
      partial: "bg-warn-amber",
      failed: "bg-fail-red",
      interrupted: "bg-zinc-400",
    },
  },
  defaultVariants: { status: "running" },
});

type Status = NonNullable<VariantProps<typeof badge>["status"]>;

const LABEL: Record<Status, string> = {
  running: "Running",
  completed: "Completed",
  partial: "Partial",
  failed: "Failed",
  interrupted: "Interrupted",
};

type Props = {
  status: Status;
  className?: string;
  label?: string;
};

export function StatusBadge({ status, className, label }: Props) {
  return (
    <span className={cn(badge({ status }), className)}>
      <span className={dot({ status })} aria-hidden />
      {label ?? LABEL[status]}
    </span>
  );
}
