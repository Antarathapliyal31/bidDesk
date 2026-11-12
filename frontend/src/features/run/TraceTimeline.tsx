import { GitBranchIcon } from "lucide-react";
import { useMemo } from "react";

import { buildSpanTree } from "@/lib/trace";
import type { RunViewState } from "@/lib/runReducer";

import { SpanRow } from "./SpanRow";

/**
 * Trace timeline (SPEC §11.2). A flat <ul> of root SpanRows; each
 * SpanRow recurses into its children when expanded.
 *
 * Phase 7: driven by the live reducer's `spans` + `rootSpanIds`.
 * Phase 8 (Task #18) reuses this same component in a TraceView tab,
 * backed by a one-shot `GET /api/runs/:id/events` fetch — the
 * component takes `RunViewState` only, so swapping the source is a
 * single hook change at the TraceView level.
 */
type Props = {
  state: RunViewState;
};

export function TraceTimeline({ state }: Props) {
  const tree = useMemo(
    () => buildSpanTree(state.spans, state.rootSpanIds),
    [state.spans, state.rootSpanIds],
  );

  if (tree.rootIds.length === 0) {
    return (
      <section
        aria-labelledby="trace-heading"
        className="rounded-lg border border-border bg-card"
      >
        <h2 id="trace-heading" className="sr-only">
          Trace
        </h2>
        <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground">
          <GitBranchIcon className="size-4" aria-hidden />
          Waiting for the first span to open.
        </div>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="trace-heading"
      className="rounded-lg border border-border bg-card"
    >
      <h2
        id="trace-heading"
        className="border-b border-border px-4 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
      >
        Trace
      </h2>
      <ul role="tree" className="space-y-0.5 px-2 py-2">
        {tree.rootIds.map((id) => {
          const span = state.spans[id];
          if (!span) return null;
          return (
            <SpanRow
              key={id}
              span={span}
              tree={tree}
              spans={state.spans}
              depth={0}
            />
          );
        })}
      </ul>
    </section>
  );
}
