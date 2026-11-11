import { AlertTriangleIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Designed error card (SPEC §11.4). Used for failed fetches, failed
 * runs and the ErrorBoundary fallback. Always names the failure and
 * offers a retry — never a bare "Something went wrong".
 */
type Props = {
  title: string;
  message?: string;
  retry?: { label?: string; onClick: () => void };
  className?: string;
};

export function ErrorState({ title, message, retry, className }: Props) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-col items-center justify-center rounded-md border border-border bg-card px-6 py-12 text-center",
        className,
      )}
    >
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-muted">
        <AlertTriangleIcon className="size-5 text-fail-red-text" aria-hidden />
      </div>
      <h2 className="text-base font-semibold text-foreground">{title}</h2>
      {message ? (
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{message}</p>
      ) : null}
      {retry ? (
        <button
          type="button"
          onClick={retry.onClick}
          className="mt-4 inline-flex h-9 items-center rounded-md border border-border bg-background px-4 text-sm font-medium hover:bg-muted"
        >
          {retry.label ?? "Try again"}
        </button>
      ) : null}
    </div>
  );
}
