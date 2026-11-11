import { FilePlusIcon, HistoryIcon } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";

import { useRuns } from "@/api";
import { useDocumentTitle } from "@/app/useDocumentTitle";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { TableSkeleton } from "@/components/skeletons";
import { messageForError } from "@/lib/apiMessages";

import { RunsTable } from "./RunsTable";

/**
 * History page (SPEC §11.1, §11.2). States §11.4:
 *   - loading → shaped TableSkeleton (not a spinner).
 *   - error   → ErrorState with retry. ApiError codes feed through
 *               `messageForError`. `?error=1` in DEV simulates the
 *               state; the retry handler clears the param so the
 *               refetch result can actually render.
 *   - empty   → EmptyState with "Create your first bid" CTA.
 *   - success → RunsTable + "Load more" (visible when the current
 *               page carries a `next_cursor`). The hook polls every
 *               5 s while any visible run has status "running".
 */
export function HistoryPage() {
  useDocumentTitle("History");
  const [params, setParams] = useSearchParams();
  const forceError = import.meta.env.DEV && params.get("error") === "1";

  const query = useRuns();
  const {
    data,
    isPending,
    isError,
    error,
    refetch,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
  } = query;

  function retry() {
    if (forceError) {
      const next = new URLSearchParams(params);
      next.delete("error");
      setParams(next, { replace: true });
    }
    void refetch();
  }

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-6 py-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">History</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Every bid you've run, newest first.
        </p>
      </header>

      {isPending ? (
        <TableSkeleton rows={7} />
      ) : isError || forceError ? (
        <ErrorState
          title="Couldn't load history"
          message={isError ? messageForError(error) : "Simulated error (?error=1)."}
          retry={{ onClick: retry }}
        />
      ) : !data ||
        data.pages.length === 0 ||
        data.pages[0]!.items.length === 0 ? (
        <EmptyState
          icon={HistoryIcon}
          title="No runs yet"
          description="Upload an RFP or paste a list to start your first bid."
          action={
            <Link
              to="/new"
              className="inline-flex h-9 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90"
            >
              <FilePlusIcon className="size-4" aria-hidden />
              Create your first bid
            </Link>
          }
        />
      ) : (
        <>
          <RunsTable runs={data.pages.flatMap((p) => p.items)} />
          {hasNextPage ? (
            <div className="flex justify-center">
              <button
                type="button"
                onClick={() => void fetchNextPage()}
                disabled={isFetchingNextPage}
                className="inline-flex h-9 items-center rounded-md border border-border bg-background px-4 text-sm font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isFetchingNextPage ? "Loading…" : "Load more"}
              </button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
