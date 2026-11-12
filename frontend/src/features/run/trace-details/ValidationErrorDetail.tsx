import { ShieldAlertIcon } from "lucide-react";

/**
 * Validation errors (SPEC §10.3 validation.failed). Rendered inside
 * an expanded SpanRow when the span's `validation` field is set.
 * SPEC caps server-side at ≤ 5 short strings — we render whatever
 * the reducer kept. Colour is red when the retry didn't recover
 * (final failure) and amber when it did (`will_retry = true`).
 */
type Props = {
  validation: {
    boundary: string;
    errors: readonly string[];
    will_retry: boolean;
  };
};

export function ValidationErrorDetail({ validation }: Props) {
  const amber = validation.will_retry;
  return (
    <div
      role="status"
      className={
        amber
          ? "mt-2 rounded-md border border-warn-amber/30 bg-warn-amber/10 px-3 py-2 text-xs text-warn-amber-text"
          : "mt-2 rounded-md border border-fail-red/30 bg-fail-red/10 px-3 py-2 text-xs text-fail-red-text"
      }
    >
      <div className="flex items-start gap-2">
        <ShieldAlertIcon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        <div className="min-w-0">
          <div className="font-medium">
            Schema validation at <span className="font-mono">{validation.boundary}</span>
            {amber ? " — retrying" : " — gave up"}
          </div>
          {validation.errors.length > 0 ? (
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px]">
              {validation.errors.map((err, i) => (
                <li key={i}>{err}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </div>
  );
}
