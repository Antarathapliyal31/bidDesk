import { AlertTriangleIcon, PlayIcon } from "lucide-react";
import { forwardRef, useId } from "react";

import { MOD_KEY_LABEL, MOD_KEY_LABEL_SR } from "@/app/platform";
import { InlineError } from "@/components/InlineError";
import { Kbd } from "@/components/Kbd";
import { cn } from "@/lib/utils";

/**
 * Sticky bottom bar with the Run button and a disabled-reason hint.
 * The label reflects the number of injected faults in violet so the
 * estimator can't miss that this run is instrumented for demo
 * failures (SPEC §10.6).
 *
 * The button never uses the native `disabled` attribute: it stays
 * focusable so keyboard users can land on it and read its
 * description. `aria-disabled` + an onClick guard do the real job;
 * `aria-describedby` points at the disabled-reason or the
 * server-error element so the reason is announced.
 *
 * `ref` targets the primary button so SampleInputs (and other
 * siblings) can focus it after prefilling the form.
 */
type Props = {
  disabled: boolean;
  // Required but nullable so the caller can always pass a computed
  // value — `exactOptionalPropertyTypes` rejects the `?: string`
  // form when the caller holds `string | undefined`.
  disabledReason: string | undefined;
  faultCount: number;
  submitting: boolean;
  onSubmit: () => void;
  errorMessage: string | undefined;
};

export const SubmitBar = forwardRef<HTMLButtonElement, Props>(function SubmitBar(
  { disabled, disabledReason, faultCount, submitting, onSubmit, errorMessage },
  ref,
) {
  const reasonId = useId();
  const errorId = useId();
  const hintId = useId();

  const injected = faultCount > 0;
  const label =
    faultCount > 0 ? `Run with ${faultCount} injected fault${faultCount === 1 ? "" : "s"}` : "Run";

  // Describe-by: prefer server error, else disabled-reason, else the
  // default ⌘↵ hint. All three ids exist in the DOM below.
  const describedBy = errorMessage ? errorId : disabled && disabledReason ? reasonId : hintId;

  function handleClick() {
    if (disabled || submitting) return;
    onSubmit();
  }

  return (
    <div className="sticky bottom-0 -mx-6 border-t border-border bg-background/95 px-6 py-3 backdrop-blur">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {errorMessage ? (
            <InlineError id={errorId}>{errorMessage}</InlineError>
          ) : disabled && disabledReason ? (
            <p id={reasonId} className="text-xs text-muted-foreground">
              {disabledReason}
            </p>
          ) : (
            <p id={hintId} className="flex items-center gap-1 text-xs text-muted-foreground">
              Press
              <Kbd label={MOD_KEY_LABEL_SR}>{MOD_KEY_LABEL}</Kbd>
              <Kbd label="Enter">↵</Kbd>
              to run
            </p>
          )}
        </div>
        <button
          ref={ref}
          type="button"
          onClick={handleClick}
          aria-disabled={disabled || submitting}
          aria-describedby={describedBy}
          className={cn(
            "inline-flex h-10 items-center gap-2 rounded-md px-5 text-sm font-medium shadow-sm transition-colors",
            injected
              ? "bg-injected-violet text-injected-violet-foreground hover:opacity-90"
              : "bg-primary text-primary-foreground hover:opacity-90",
            (disabled || submitting) && "cursor-not-allowed opacity-60",
          )}
        >
          {injected ? (
            <AlertTriangleIcon className="size-4" aria-hidden />
          ) : (
            <PlayIcon className="size-4" aria-hidden />
          )}
          {submitting ? "Starting…" : label}
        </button>
      </div>
    </div>
  );
});
