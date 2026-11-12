import { ClockIcon, HashIcon, type LucideIcon } from "lucide-react";

import { StatusBadge } from "@/components/StatusBadge";
import { formatElapsed } from "@/app/useElapsed";
import { elapsedSecondsFromState, type RunViewState } from "@/lib/runReducer";

import { TraceTimeline } from "./TraceTimeline";

/**
 * TraceView (SPEC §11.2 — "same TraceTimeline, replay from DB").
 *
 * Shares `RunViewState` with the Live tab. In fixture + production
 * both: `useRunStream` builds state by replaying persisted events
 * (`GET /events?after_id=0`) and then layering on live events via
 * SSE. The Trace tab just renders TraceTimeline on top of that
 * same state — a historic finished run is the same shape as a
 * live run, just with `status !== "running"`.
 *
 * The summary header exists here (and NOT inside TraceTimeline)
 * because the Live tab doesn't want it — it already has the
 * RunHeader's status + elapsed up top.
 */
type Props = {
  state: RunViewState;
};

export function TraceView({ state }: Props) {
  const eventCount = state.lastEventId;
  const spanCount = Object.keys(state.spans).length;
  const elapsedSeconds = elapsedSecondsFromState(state);

  return (
    <div className="mx-auto max-w-5xl space-y-5 px-6 py-6">
      <section
        aria-label="Trace summary"
        className="rounded-lg border border-border bg-card px-4 py-3"
      >
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs">
          <StatusBadge status={state.status} />
          <SummaryStat icon={HashIcon} label="Events">
            <span className="tabular">{eventCount}</span>
          </SummaryStat>
          <SummaryStat icon={HashIcon} label="Spans">
            <span className="tabular">{spanCount}</span>
          </SummaryStat>
          <SummaryStat icon={ClockIcon} label="Elapsed">
            <span className="tabular">{formatElapsed(elapsedSeconds)}</span>
          </SummaryStat>
          {state.outcome !== null ? (
            <SummaryStat label="Outcome">
              <span className="font-medium capitalize">{state.outcome}</span>
              {state.outcomeDetail ? (
                <span className="ml-1 text-muted-foreground">
                  · {state.outcomeDetail}
                </span>
              ) : null}
            </SummaryStat>
          ) : null}
        </div>
      </section>

      <TraceTimeline state={state} />
    </div>
  );
}

function SummaryStat({
  icon: Icon,
  label,
  children,
}: {
  icon?: LucideIcon;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 text-muted-foreground">
      {Icon ? <Icon className="size-3.5" aria-hidden /> : null}
      <span className="text-[10px] font-medium uppercase tracking-wide">{label}</span>
      <span className="text-foreground">{children}</span>
    </span>
  );
}
