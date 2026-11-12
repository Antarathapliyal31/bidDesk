import type { SourceRef } from "@/api";
import { useSourcePanel } from "@/features/run/source/SourcePanelContext";
import { cn } from "@/lib/utils";

/**
 * Tiny "p. N" chip that opens the SourcePanel to the cited page.
 * Pairs with ItemsStream rows and (Phase 8) BidLineRow citations.
 *
 * Styled as a focusable pill so keyboard users can land on it and
 * fire `openSource` with Enter/Space. `aria-label` reads the full
 * "page N" to the screen reader because "p." alone is ambiguous.
 *
 * `pdfUrl` is passed in by the caller — the page that owns the run
 * context (RunPage) decides what URL to serve for this run id.
 */
type Props = {
  source: SourceRef;
  pdfUrl: string | null;
  className?: string;
};

export function CitationLink({ source, pdfUrl, className }: Props) {
  const { openSource } = useSourcePanel();
  if (source.page === null) {
    return (
      <span className={cn("text-muted-foreground", className)} aria-hidden>
        —
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={() => openSource(source, pdfUrl)}
      aria-label={`page ${source.page}`}
      className={cn(
        "inline-flex items-center rounded border border-border bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        className,
      )}
    >
      p.{source.page}
    </button>
  );
}
