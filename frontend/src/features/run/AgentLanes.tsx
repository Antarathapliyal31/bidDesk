import {
  AlertTriangleIcon,
  CheckIcon,
  CircleDashedIcon,
  FileTextIcon,
  type LucideIcon,
  LoaderIcon,
  NetworkIcon,
  SquareIcon,
  WalletIcon,
} from "lucide-react";

import type { AgentName } from "@/api";
import type { AgentLaneView, RunViewState, SpanView } from "@/lib/runReducer";
import { cn } from "@/lib/utils";

/**
 * The three agent lanes (SPEC §11.2). Each card surfaces:
 *   - agent name + icon
 *   - lane state (idle · working · done · failed · interrupted)
 *   - the latest `agent.activity` line (truncated, aria-live polite)
 *   - a tiny errorKind badge when the lane is failed OR carries a
 *     recorded errorKind after a recovered child failure.
 *
 * Display-only rule (SPEC §11.2 + manual-pass fix #9): a lane whose
 * reducer state is "idle" but whose agent has at least one past
 * (finished/failed/interrupted) span is rendered as "Done". The
 * reducer's own definition of "idle" is "no active span now" — for
 * a lane that has already done work in this run, "Idle" would read
 * as "hasn't started" and lie to the estimator.
 */
const AGENTS: ReadonlyArray<{ id: AgentName; label: string; icon: LucideIcon }> = [
  { id: "document_analyst", label: "Document Analyst", icon: FileTextIcon },
  { id: "orchestrator", label: "Orchestrator", icon: NetworkIcon },
  { id: "pricing_agent", label: "Pricing Agent", icon: WalletIcon },
];

type LaneState = "idle" | "working" | "done" | "failed" | "interrupted";

const STATE_CLASS: Record<LaneState, string> = {
  idle: "text-muted-foreground",
  working: "text-run-blue-text",
  done: "text-done-green-text",
  failed: "text-fail-red-text",
  interrupted: "text-warn-amber-text",
};

const STATE_LABEL: Record<LaneState, string> = {
  idle: "Idle",
  working: "Working",
  done: "Done",
  failed: "Failed",
  interrupted: "Interrupted",
};

const STATE_ICON: Record<LaneState, LucideIcon> = {
  idle: CircleDashedIcon,
  working: LoaderIcon,
  done: CheckIcon,
  failed: AlertTriangleIcon,
  interrupted: SquareIcon,
};

function LaneStateChip({ state }: { state: LaneState }) {
  const Icon = STATE_ICON[state];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 text-xs font-medium",
        STATE_CLASS[state],
      )}
    >
      <Icon
        className={cn("size-3.5", state === "working" && "animate-spin motion-reduce:animate-none")}
        aria-hidden
      />
      {STATE_LABEL[state]}
    </span>
  );
}

type Props = {
  agents: RunViewState["agents"];
  spans: RunViewState["spans"];
};

/**
 * Display-only transition: idle lane + at least one past span for
 * this agent → "done". Keeps the reducer's semantics of "idle = no
 * active span" clean while giving the UI a truthful label.
 */
function displayLaneState(
  agent: AgentName,
  lane: AgentLaneView,
  spans: Record<string, SpanView>,
): AgentLaneView["state"] {
  if (lane.state !== "idle") return lane.state;
  for (const span of Object.values(spans)) {
    if (span.agent !== agent) continue;
    if (span.state === "finished" || span.state === "failed" || span.state === "interrupted") {
      return "done";
    }
  }
  return "idle";
}

export function AgentLanes({ agents, spans }: Props) {
  return (
    <section
      aria-label="Agents"
      className="grid grid-cols-1 gap-3 md:grid-cols-3"
    >
      {AGENTS.map(({ id, label, icon: Icon }) => {
        const lane = agents[id];
        const displayState = displayLaneState(id, lane, spans);
        return (
          <article
            key={id}
            className="rounded-md border border-border bg-card p-4"
            aria-label={label}
          >
            <div className="flex items-center gap-2">
              <span className="inline-flex size-7 items-center justify-center rounded-md bg-muted">
                <Icon className="size-4 text-muted-foreground" aria-hidden />
              </span>
              <span className="text-sm font-semibold">{label}</span>
              <span className="ml-auto">
                <LaneStateChip state={displayState} />
              </span>
            </div>
            <p
              aria-live="polite"
              className="mt-3 line-clamp-2 min-h-[2.5rem] text-xs text-muted-foreground"
            >
              {lane.activity ?? (lane.state === "idle" ? "" : "…")}
            </p>
            {lane.errorKind ? (
              <p className="mt-2 inline-flex items-center gap-1 text-xs text-fail-red-text">
                <AlertTriangleIcon className="size-3.5" aria-hidden />
                {lane.errorKind}
              </p>
            ) : null}
          </article>
        );
      })}
    </section>
  );
}
