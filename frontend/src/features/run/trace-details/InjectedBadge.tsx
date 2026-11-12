import { AlertTriangleIcon } from "lucide-react";

/**
 * Violet "demo fault" badge (SPEC §10.6). Rendered inline in the
 * SpanRow header when a span's `injected` is true — these are
 * failures that came from `/api/_demo/faults`, not real errors.
 * Keeping the colour reserved to violet (SPEC §11.5) means a demo
 * failure can never be mistaken for a real one.
 */
export function InjectedBadge() {
  return (
    <span
      className="inline-flex items-center gap-1 rounded bg-injected-violet/15 px-1.5 py-0.5 text-[10px] font-medium text-injected-violet-text"
      title="This failure was injected via /api/_demo/faults — demo only."
    >
      <AlertTriangleIcon className="size-3" aria-hidden />
      Demo fault
    </span>
  );
}
