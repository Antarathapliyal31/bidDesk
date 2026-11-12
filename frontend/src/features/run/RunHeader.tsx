import { ActivityIcon, CloudOffIcon, HistoryIcon, RadioIcon, SparklesIcon } from "lucide-react";

import { formatElapsed } from "@/app/useElapsed";
import { StatusBadge } from "@/components/StatusBadge";
import { TabsList, TabsTrigger } from "@/components/ui/tabs";
import { isReusedStream } from "@/api";
import { cn } from "@/lib/utils";
import {
  elapsedSecondsFromState,
  type ConnectionState,
  type RunViewState,
} from "@/lib/runReducer";

/**
 * Run-page header. Four slots:
 *   - title (project name or input name)
 *   - status badge (running / completed / partial / failed / interrupted)
 *   - connection indicator (Live / Reconnecting / Replay)
 *   - tabs (Live · Bid · Trace; Source panel is separate in SPEC §11.2)
 *
 * `bidTab.disabled` until `bid.ready`. `bidTab` is auto-switched by
 * RunPage when `bid.ready` arrives AND the user is still on Live.
 */
type Props = {
  runId: string;
  state: RunViewState;
  connection: ConnectionState;
  /**
   * Run-level metadata from `useRunSummary(runId)`. Carries
   * `project_name` for the title (SPEC §5.1 `runs.project_name`
   * — NOT in the SSE event stream; see DECISIONS F9). `null` for
   * new-bid submits before the run row is persisted, or for runs
   * that aren't in the fixture history.
   */
  projectName: string | null;
};

function ConnectionChip({
  connection,
  isRunning,
}: {
  connection: ConnectionState;
  isRunning: boolean;
}) {
  if (connection === "live" && isRunning) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium text-run-blue-text">
        <RadioIcon className="size-3.5" aria-hidden />
        Live
      </span>
    );
  }
  if (connection === "reconnecting") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium text-warn-amber-text">
        <ActivityIcon className="size-3.5" aria-hidden />
        Reconnecting…
      </span>
    );
  }
  if (connection === "live" || connection === "closed") {
    // Live but not-running, or closed after the run finished.
    return (
      <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium text-muted-foreground">
        <HistoryIcon className="size-3.5" aria-hidden />
        Replay
      </span>
    );
  }
  // connecting — before any event lands.
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium text-muted-foreground">
      <CloudOffIcon className="size-3.5" aria-hidden />
      Connecting…
    </span>
  );
}

export function RunHeader({ runId, state, connection, projectName }: Props) {
  // Title/subtitle (SPEC §11.5 "document feel"): project name as
  // the title when the summary carries one (history rows set
  // `project_name` in their `runs` row — fetched via
  // `useRunSummary`). The input filename demotes to a subtitle.
  // For runs without a project name (fresh bids), the filename
  // is the title and there's no subtitle.
  const title = projectName ?? state.inputName ?? runId;
  const subtitle = projectName !== null ? state.inputName : null;
  const isRunning = state.status === "running";
  // Elapsed comes from event ts, not Date.now() — see RunViewState
  // docs. One number, one source; Trace tab reads the same helper.
  const elapsed = elapsedSecondsFromState(state);
  const demoReused = isReusedStream(runId);

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-background/95 backdrop-blur">
      <div className="mx-auto flex max-w-5xl flex-col gap-3 px-6 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="min-w-0 flex-1">
            <h1 className="truncate text-xl font-semibold tracking-tight">{title}</h1>
            {subtitle ? (
              <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
            ) : null}
          </div>
          {demoReused ? (
            <span
              className="inline-flex items-center gap-1 rounded-md border border-border bg-muted px-2 py-0.5 text-xs font-medium text-injected-violet-text"
              title="Fixture mode: this run replays the rfp_messy scripted stream. Phase 5 merge wires per-run SSE."
            >
              <SparklesIcon className="size-3.5" aria-hidden />
              Demo data
            </span>
          ) : null}
          <StatusBadge status={state.status} />
          <ConnectionChip connection={connection} isRunning={isRunning} />
          <span
            className={cn(
              "tabular text-xs text-muted-foreground",
              !state.startedAtTs && "opacity-50",
            )}
            aria-label="Elapsed time"
          >
            {formatElapsed(elapsed)}
          </span>
        </div>

        <TabsList className="self-start">
          <TabsTrigger value="live">Live</TabsTrigger>
          <TabsTrigger value="bid" disabled={state.bidReady === null}>
            Bid
            {state.bidReady && state.bidReady.open_flags > 0 ? (
              <span
                className="ml-1.5 inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-warn-amber px-1 text-xs font-medium text-white"
                aria-label={`${state.bidReady.open_flags} open flags`}
              >
                {state.bidReady.open_flags}
              </span>
            ) : null}
          </TabsTrigger>
          <TabsTrigger value="trace">Trace</TabsTrigger>
        </TabsList>
      </div>
    </header>
  );
}
