import { CompassIcon } from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useRunStream, useRunSummary } from "@/api";
import { useDocumentTitle } from "@/app/useDocumentTitle";
import { EmptyState } from "@/components/EmptyState";
import { Tabs, TabsContent } from "@/components/ui/tabs";

import { BidWorkspace } from "@/features/bid/BidWorkspace";

import { LiveView } from "./LiveView";
import { RunHeader } from "./RunHeader";
import { TraceView } from "./TraceView";
import { pdfUrlForRun } from "./source/pdfRunUrl";
import { SourcePanel } from "./source/SourcePanel";
import { SourcePanelProvider } from "./source/SourcePanelContext";

/**
 * Run workspace (SPEC §11.1, §11.2). Live view + tabs for Bid and
 * Trace. Source panel lands in Task #17 as a separate slide-over.
 *
 * Auto-switch: when `bid.ready` arrives AND the user is still on
 * the Live tab, flip to Bid. If they've already navigated to Trace
 * (or elsewhere), leave them alone.
 */
type TabValue = "live" | "bid" | "trace";

export function RunPage() {
  const { runId = "" } = useParams();
  const { state, connection } = useRunStream(runId);
  // Project name (if any) comes from the run row, not from events
  // — see DECISIONS F9. Loading / missing both read as "no project
  // name"; RunHeader degrades gracefully to showing input_name.
  // Pass the live reducer state so useRunSummary invalidates on
  // the analyst's first span.finished + on bid.ready — the two
  // moments a server-side `project_name` might flip.
  const { data: summary } = useRunSummary(runId, { runState: state });
  const projectName = summary?.project_name ?? null;
  useDocumentTitle(projectName ?? state.inputName ?? `Run ${runId}`);

  // Tab is derived: default = Bid once `bid.ready` arrives, else Live.
  // A user click sets `userTab` which overrides the default and
  // sticks until the component unmounts. Deriving (vs setState-in-
  // effect) sidesteps `react-hooks/set-state-in-effect` and keeps
  // rendering a pure function of state.
  const [userTab, setUserTab] = useState<TabValue | null>(null);
  const defaultTab: TabValue = state.bidReady !== null ? "bid" : "live";
  const tab: TabValue = userTab ?? defaultTab;

  // PDF URL is computed once per (runId, inputName) pair. In fixture
  // mode `pdfUrlForRun` maps `run_demo_*` to the messy sample PDF;
  // paste runs return null and citations collapse to "—".
  const pdfUrl = useMemo(() => pdfUrlForRun(runId, state.inputName), [runId, state.inputName]);

  // Unknown runId: the fixture branch marked the connection closed
  // immediately AND no run.started landed. Phase 5 merge replaces
  // this with a real fetch that distinguishes 404 from transport
  // errors.
  if (connection === "closed" && state.startedAtTs === null) {
    return (
      <div className="mx-auto max-w-xl px-6 py-24">
        <EmptyState
          icon={CompassIcon}
          title="We couldn't find that run"
          description="The id doesn't match anything we know about. Start a new bid or browse history."
          action={
            <div className="flex items-center gap-2">
              <Link
                to="/new"
                className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90"
              >
                Start a new bid
              </Link>
              <Link
                to="/runs"
                className="inline-flex h-9 items-center rounded-md border border-border bg-background px-4 text-sm font-medium hover:bg-muted"
              >
                View history
              </Link>
            </div>
          }
        />
      </div>
    );
  }

  return (
    <SourcePanelProvider>
      <Tabs
        value={tab}
        onValueChange={(v) => setUserTab(v as TabValue)}
        className="flex flex-col"
      >
        <RunHeader
          runId={runId}
          state={state}
          connection={connection}
          projectName={projectName}
          inputName={summary?.input_name ?? null}
        />
        <TabsContent value="live" className="flex-1">
          <LiveView state={state} pdfUrl={pdfUrl} />
        </TabsContent>
        <TabsContent value="bid" className="flex-1">
          <BidWorkspace
            runId={runId}
            state={state}
            projectName={projectName}
            branchName={summary?.branch_name ?? null}
            pdfUrl={pdfUrl}
            active={tab === "bid"}
          />
        </TabsContent>
        <TabsContent value="trace" className="flex-1">
          <TraceView state={state} />
        </TabsContent>
      </Tabs>
      <SourcePanel />
    </SourcePanelProvider>
  );
}
