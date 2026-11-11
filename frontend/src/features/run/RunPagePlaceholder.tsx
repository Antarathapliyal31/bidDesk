import { ActivityIcon } from "lucide-react";
import { Link, useParams } from "react-router-dom";

import { useDocumentTitle } from "@/app/useDocumentTitle";

/**
 * Designed placeholder for `/runs/:runId`. Phase 7 replaces this with
 * the live view (AgentLanes + TraceTimeline + ItemsStream). Shown now
 * so the submit → run-page nav path is visible end-to-end.
 */
export function RunPagePlaceholder() {
  const { runId } = useParams();
  useDocumentTitle(runId ? `Run ${runId}` : "Run");
  return (
    <div className="mx-auto max-w-3xl px-6 py-12">
      <div className="rounded-md border border-border bg-card p-6">
        <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
          <ActivityIcon className="size-5 text-muted-foreground" aria-hidden />
        </div>
        <h1 className="text-lg font-semibold">Live run view — coming in Phase 7</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Run <span className="font-mono tabular text-foreground">{runId}</span> will stream
          its agent lanes, trace timeline and item extraction here.
        </p>
        <div className="mt-5 flex items-center gap-2">
          <Link
            to="/runs"
            className="inline-flex h-9 items-center rounded-md border border-border bg-background px-4 text-sm font-medium hover:bg-muted"
          >
            Back to history
          </Link>
          <Link
            to="/new"
            className="inline-flex h-9 items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            Start a new bid
          </Link>
        </div>
      </div>
    </div>
  );
}
