import { CompassIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { useDocumentTitle } from "@/app/useDocumentTitle";

/**
 * Designed 404. Reachable from any unknown URL; links back to New bid
 * and History.
 */
export function NotFoundPage() {
  useDocumentTitle("Not found");
  return (
    <div className="mx-auto flex max-w-xl flex-col items-center justify-center px-6 py-24 text-center">
      <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-muted">
        <CompassIcon className="size-5 text-muted-foreground" aria-hidden />
      </div>
      <h1 className="text-xl font-semibold">We couldn't find that page</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        The link may be stale, or that run doesn't exist anymore.
      </p>
      <div className="mt-6 flex items-center gap-2">
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
    </div>
  );
}
