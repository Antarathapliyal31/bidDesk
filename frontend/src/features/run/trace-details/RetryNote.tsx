import { RefreshCwIcon } from "lucide-react";

/**
 * Amber retry note (SPEC §10.3 retry.scheduled). Rendered inside an
 * expanded SpanRow when the span's `retry` field is set. Wording is
 * close to SPEC §11.4: "Pricing agent timed out (45 s) — retrying
 * 1/1".
 */
type Props = {
  retry: {
    attempt: number;
    delay_s: number;
    reason: string;
  };
};

export function RetryNote({ retry }: Props) {
  return (
    <div
      role="status"
      className="mt-2 flex items-start gap-2 rounded-md border border-warn-amber/30 bg-warn-amber/10 px-3 py-2 text-xs text-warn-amber-text"
    >
      <RefreshCwIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <div className="min-w-0">
        <div className="font-medium">
          {retry.reason} — retrying {retry.attempt}/{retry.attempt}
        </div>
        <div className="mt-0.5 text-[11px] text-warn-amber-text/80">
          Backoff: {retry.delay_s.toFixed(1)}s
        </div>
      </div>
    </div>
  );
}
