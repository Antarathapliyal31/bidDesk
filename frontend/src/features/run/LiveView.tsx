import type { RunViewState } from "@/lib/runReducer";

import { AgentLanes } from "./AgentLanes";
import { ItemsStream } from "./ItemsStream";
import { TraceTimeline } from "./TraceTimeline";

/**
 * LiveView (SPEC §11.2). Phase 7 steps 4–7:
 *   - AgentLanes across the top (3 cards).
 *   - TraceTimeline in the middle — spans + tool calls, recursive.
 *   - ItemsStream below — extracted → priced per item.
 *
 * The SourcePanel is mounted once at RunPage level (one panel per
 * page); each CitationLink in ItemsStream + the bid tables calls
 * `openSource` through the context. `pdfUrl` is the run's PDF URL
 * (null for paste runs) and is threaded down to ItemsStream so the
 * panel can render the right file.
 */
type Props = {
  state: RunViewState;
  pdfUrl: string | null;
};

export function LiveView({ state, pdfUrl }: Props) {
  return (
    <div className="mx-auto max-w-5xl space-y-5 px-6 py-6">
      <AgentLanes agents={state.agents} spans={state.spans} />
      <TraceTimeline state={state} />
      <ItemsStream state={state} pdfUrl={pdfUrl} />
    </div>
  );
}
