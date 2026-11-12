import { AlertCircleIcon, CheckCircle2Icon, LoaderIcon } from "lucide-react";

import { Kbd } from "@/components/Kbd";
import { MOD_KEY_LABEL } from "@/app/platform";
import { cn } from "@/lib/utils";

/**
 * Save state indicator + Finalize button (SPEC §11.4 FinalizeBar).
 *
 *   Saved · Saving… · Unsaved changes · Couldn't save — Retry
 *
 * Finalize is disabled while any review is still open; the disabled
 * reason is announced via aria-describedby and clickable to jump
 * to the first open review line.
 */
type Props = {
  save: "saved" | "saving" | "unsaved" | "error";
  openFlags: number;
  onFinalize: () => void;
  onJumpToFirstFlag: () => void;
  onRetrySave: () => void;
  finalizing: boolean;
  isFinal: boolean;
};

export function FinalizeBar({
  save,
  openFlags,
  onFinalize,
  onJumpToFirstFlag,
  onRetrySave,
  finalizing,
  isFinal,
}: Props) {
  const canFinalize = openFlags === 0 && !isFinal && !finalizing;
  return (
    <div className="sticky bottom-0 -mx-6 border-t border-border bg-background/95 px-6 py-3 backdrop-blur">
      <div className="flex items-center justify-between gap-4">
        <SaveStatus save={save} onRetry={onRetrySave} />
        <div className="flex items-center gap-3">
          {openFlags > 0 ? (
            <button
              type="button"
              onClick={onJumpToFirstFlag}
              className="text-xs text-warn-amber-text underline-offset-2 hover:underline"
            >
              {openFlags} line{openFlags === 1 ? "" : "s"} need review
            </button>
          ) : null}
          <button
            type="button"
            onClick={canFinalize ? onFinalize : onJumpToFirstFlag}
            aria-disabled={!canFinalize}
            className={cn(
              "inline-flex h-10 items-center gap-2 rounded-md px-5 text-sm font-medium shadow-sm transition-colors",
              canFinalize
                ? "bg-primary text-primary-foreground hover:opacity-90"
                : "cursor-not-allowed bg-muted text-muted-foreground",
            )}
          >
            {finalizing ? (
              <LoaderIcon className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
            ) : null}
            {isFinal ? "Finalized" : "Finalize"}
            {canFinalize ? (
              <>
                <Kbd label={`${MOD_KEY_LABEL} + Return`}>{MOD_KEY_LABEL}</Kbd>
                <Kbd label="Return">↵</Kbd>
              </>
            ) : null}
          </button>
        </div>
      </div>
    </div>
  );
}

function SaveStatus({
  save,
  onRetry,
}: {
  save: Props["save"];
  onRetry: () => void;
}) {
  if (save === "saved")
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <CheckCircle2Icon className="size-3.5" aria-hidden />
        Saved
      </span>
    );
  if (save === "saving")
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
        <LoaderIcon className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
        Saving…
      </span>
    );
  if (save === "unsaved")
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-warn-amber-text">
        Unsaved changes
      </span>
    );
  return (
    <span className="inline-flex items-center gap-2 text-xs text-fail-red-text">
      <AlertCircleIcon className="size-3.5" aria-hidden />
      Couldn't save.
      <button
        type="button"
        onClick={onRetry}
        className="font-medium text-foreground underline-offset-2 hover:underline"
      >
        Retry
      </button>
    </span>
  );
}
