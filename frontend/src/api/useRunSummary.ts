import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { FIXTURE_RUNS, type FxRunSummary, withDelay } from "@/dev-fixtures";
import type { RunViewState } from "@/lib/runReducer";

import type { ApiError } from "./ApiError";

/**
 * Run-level metadata that ISN'T in the SSE event stream — project
 * name, created_at, finalized_at, outcome totals. These come from
 * the backend's run record (SPEC §5.1 `runs` table), not from the
 * agents, so they don't belong on `run.started` (DECISIONS F9).
 *
 * Phase 5 merge: `queryFn` becomes
 *   const r = await fetch(`/api/runs/${runId}`);
 *   if (!r.ok) throw await apiErrorFromResponse(r);
 *   return flatten(await r.json());
 * where `flatten` lifts `input.kind` → `input_kind`, etc. per
 * API_CONTRACT's "field aliases" note. Call sites don't change.
 *
 * ## Invalidation (DECISIONS F9 refinement)
 *
 * The summary's two "live" fields are `project_name` (can
 * change when the document analyst infers one from the PDF) and
 * the outcome totals (settle on `bid.ready`). While the run is
 * live we refetch on:
 *   1. `document_analyst` span finishes (analyst's output lands
 *      on the `runs` row shortly after).
 *   2. `bid.ready` arrives (final totals + revision=0 settle).
 *
 * Once the run is finalized (status !== "running") the summary
 * is a frozen resource — `staleTime: Infinity` so a History→Run
 * round-trip doesn't refetch. While running, `staleTime: 30s`
 * so a stray re-mount doesn't thrash.
 *
 * `runState` is optional: callers without the live reducer state
 * (e.g. the History row context) still get the one-shot fetch;
 * they just don't participate in the live-invalidation signals.
 */
export type RunSummary = FxRunSummary;

export type UseRunSummaryOptions = {
  runState?: RunViewState;
};

export function useRunSummary(runId: string, options: UseRunSummaryOptions = {}) {
  const qc = useQueryClient();
  const runState = options.runState;

  // The three signals we watch. Booleans — change infrequently, so
  // the effect only fires on real transitions.
  const analystSpanFinished = runState
    ? Object.values(runState.spans).some(
        (s) => s.agent === "document_analyst" && s.state === "finished",
      )
    : false;
  const bidReadyLanded = runState?.bidReady != null;
  const isFinalized = runState ? runState.status !== "running" : false;

  // A stable key so the effect only fires when a signal FLIPS,
  // not on every render that happens to have the same value.
  const invalidateKey = `${analystSpanFinished ? 1 : 0}:${bidReadyLanded ? 1 : 0}`;
  useEffect(() => {
    if (!analystSpanFinished && !bidReadyLanded) return;
    void qc.invalidateQueries({ queryKey: ["run", runId, "summary"] });
    // `invalidateKey` is intentionally the dep so we fire on the
    // flip from false→true, not every re-render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qc, runId, invalidateKey]);

  return useQuery<RunSummary | null, ApiError>({
    queryKey: ["run", runId, "summary"],
    queryFn: () => {
      const match = FIXTURE_RUNS.find((r) => r.id === runId) ?? null;
      return withDelay<RunSummary | null>(match, 50);
    },
    // Live run: short staleTime so a navigate-away-and-back
    // picks up a fresh value if the analyst inferred a project
    // name since the last fetch. Finalized: never refetches.
    staleTime: isFinalized ? Infinity : 30_000,
    // For brand-new runs the row doesn't exist in the fixture yet;
    // don't thrash with retries.
    retry: false,
  });
}
