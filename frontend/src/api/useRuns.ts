import { useInfiniteQuery } from "@tanstack/react-query";

import {
  FIXTURE_RUNS,
  type FxDiffSummary,
  type FxInputKind,
  type FxOutcome,
  type FxRunStatus,
  type FxRunSummary,
  withDelay,
} from "@/dev-fixtures";

import type { ApiError } from "./ApiError";
import { apiFetch, isFixtureMode } from "./client";

/**
 * Run list for the History page. Paginated via `next_cursor`.
 *   - Fixtures: page size 5; the cursor is the next item's index
 *     encoded as a string; `null` on the last page.
 *   - Phase 5 merge: the cursor becomes opaque (SPEC + API_CONTRACT
 *     say "the client never decodes it"), queryFn swaps to
 *     `GET /api/runs?status=…&cursor=…&limit=…`. Call sites and
 *     return shape don't change.
 *
 * Polling: while any visible run has status "running", refetch every
 * 5 s so the table picks up newly-finished runs without a reload.
 * When no run is running, refetch stops (interval returns `false`).
 */
export type RunSummary = FxRunSummary;
export type RunStatus = FxRunStatus;
export type Outcome = FxOutcome;
export type InputKind = FxInputKind;
export type DiffSummary = FxDiffSummary;

export type RunsPage = {
  items: RunSummary[];
  next_cursor: string | null;
};

export type UseRunsParams = {
  status?: RunStatus;
};

type InfData = { pages: RunsPage[]; pageParams: (string | undefined)[] };

export const FIXTURE_PAGE_SIZE = 5;

/**
 * Returns the refetch interval for the History query: 5 s while
 * any visible run is still running, otherwise `false` (no poll).
 * Exported so the behavior can be unit-tested without spinning up
 * a real QueryClient + fake timers.
 */
export function shouldPollRuns(pages: RunsPage[]): false | number {
  const anyRunning = pages.some((p) => p.items.some((r) => r.status === "running"));
  return anyRunning ? 5_000 : false;
}

function pageFromFixtures(status: RunStatus | undefined, cursor: string | undefined): RunsPage {
  const filtered = status ? FIXTURE_RUNS.filter((r) => r.status === status) : FIXTURE_RUNS;
  const start = cursor ? Math.max(0, parseInt(cursor, 10)) : 0;
  const end = start + FIXTURE_PAGE_SIZE;
  const items = filtered.slice(start, end);
  const next_cursor = end < filtered.length ? String(end) : null;
  return { items, next_cursor };
}

async function fetchRunsPage(
  status: RunStatus | undefined,
  cursor: string | undefined,
): Promise<RunsPage> {
  const qs = new URLSearchParams();
  if (status) qs.set("status", status);
  if (cursor) qs.set("cursor", cursor);
  const url = `/api/runs${qs.toString() ? `?${qs.toString()}` : ""}`;
  const res = await apiFetch(url);
  return (await res.json()) as RunsPage;
}

export function useRuns(params: UseRunsParams = {}) {
  const key = { status: params.status ?? null };
  return useInfiniteQuery<RunsPage, ApiError, InfData, [string, typeof key], string | undefined>({
    queryKey: ["runs", key],
    queryFn: ({ pageParam }) =>
      isFixtureMode()
        ? withDelay(pageFromFixtures(params.status, pageParam))
        : fetchRunsPage(params.status, pageParam),
    initialPageParam: undefined,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    // Let TanStack's generics infer the query type. Logic lives in
    // `shouldPollRuns` so it's unit-testable without a QueryClient.
    refetchInterval: (query) => shouldPollRuns(query.state.data?.pages ?? []),
  });
}
