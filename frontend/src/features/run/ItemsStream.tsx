import { ArrowRightLeftIcon, CameraIcon, FlagIcon, PackageXIcon } from "lucide-react";

import { CitationLink } from "@/components/CitationLink";
import { ConfidencePill } from "@/components/ConfidencePill";
import { Money } from "@/components/Money";
import { Skeleton } from "@/components/ui/skeleton";
import { reasonCodeLabel } from "@/lib/reasonCodes";
import type { ItemStreamView, RunViewState } from "@/lib/runReducer";
import { cn } from "@/lib/utils";

/**
 * Items stream (SPEC §11.2). One row per extracted item, in the
 * order the analyst emitted them. Each row transitions from the
 * "extracted" skeleton (description + qty + source chip, pricing
 * cells blank) to the "priced" state as `item.priced` events land.
 *
 * Status chips surface the non-happy paths early (no_match, skipped,
 * substitute, shortfall, flagged) so the estimator sees them stream
 * in rather than having to find them in the bid afterward.
 */
type Props = {
  state: RunViewState;
  /**
   * URL of the source PDF for this run, or null for paste / uploaded
   * runs. Passed to each row's citation link; the SourcePanel uses
   * it to render the right PDF.
   */
  pdfUrl: string | null;
};

// Column template shared by header + rows so they always align.
// Src · Item · Requested · Priced · Unit price · Ext.
const GRID_COLS = "grid-cols-[52px_minmax(0,1fr)_120px_140px_110px_110px]";

export function ItemsStream({ state, pdfUrl }: Props) {
  const items = state.itemOrder
    .map((id) => state.items[id])
    .filter((v): v is ItemStreamView => v !== undefined);

  if (items.length === 0) {
    return (
      <section
        aria-label="Items"
        aria-busy="true"
        className="rounded-md border border-border bg-card p-6 text-sm text-muted-foreground"
      >
        Waiting for the analyst to extract the first items…
      </section>
    );
  }

  return (
    <section aria-label="Items" className="overflow-hidden rounded-md border border-border bg-card">
      <div
        className={cn(
          "grid gap-4 border-b border-border bg-muted/50 px-4 py-2 text-xs font-medium uppercase tracking-wide text-muted-foreground",
          GRID_COLS,
        )}
      >
        <span>Src</span>
        <span>Item</span>
        <span>Requested</span>
        <span>Priced</span>
        <span className="text-right">Unit price</span>
        <span className="text-right">Ext.</span>
      </div>
      <ul role="list" className="divide-y divide-border">
        {items.map((item) => (
          <li key={item.id}>
            <ItemRow item={item} pdfUrl={pdfUrl} />
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Source-provenance badge. Three paths (SPEC §6.4):
 *   - read_via = "vision"  → neutral "Read from image" chip (no
 *     text layer, verification wasn't a quote match — distinct
 *     affordance from an unverified text quote).
 *   - verified = true      → no badge (the quote matched).
 *   - verified = false + text → caller renders "Quote not found"
 *     amber chip next to this.
 */
function SourceBadge({ item }: { item: ItemStreamView }) {
  if (item.read_via === "vision") {
    return (
      <span
        className="inline-flex items-center gap-0.5 rounded border border-border bg-muted px-1 py-0 font-medium text-muted-foreground"
        title="Analyst read this page as an image; no text quote to verify."
      >
        <CameraIcon className="size-3" aria-hidden />
        Read from image
      </span>
    );
  }
  return null;
}

function renderBadgeRow(item: ItemStreamView, flaggedReasons: string[]): React.ReactNode {
  const isVision = item.read_via === "vision";
  const quoteNotFound = !isVision && item.notes.includes("quote_not_found");
  const hasFlags = flaggedReasons.length > 0;
  if (!isVision && !quoteNotFound && !hasFlags) return null;
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
      <SourceBadge item={item} />
      {quoteNotFound ? (
        <span
          className="inline-flex items-center gap-0.5 rounded border border-warn-amber/30 bg-warn-amber/10 px-1 py-0 font-medium text-warn-amber-text"
          title="The analyst kept this item but couldn't verify the quote against the PDF text."
        >
          Quote not found
        </span>
      ) : null}
      {hasFlags ? (
        <span className="inline-flex items-center gap-1 text-warn-amber-text">
          <FlagIcon className="size-3.5" aria-hidden />
          {flaggedReasons.join(", ")}
        </span>
      ) : null}
    </div>
  );
}

function ItemRow({ item, pdfUrl }: { item: ItemStreamView; pdfUrl: string | null }) {
  const priced = item.priced;
  const showMatchedChips = priced?.status === "matched" || priced?.status === "substitute";
  const flaggedReasons = item.flags.map((f) => reasonCodeLabel(f.reason_code));

  return (
    <div
      className={cn(
        "grid items-center gap-4 px-4 py-2.5 text-sm transition-colors",
        GRID_COLS,
      )}
    >
      <CitationLink
        source={{
          page: item.page,
          quote: item.quote,
          read_via: item.read_via,
          verified: item.verified,
        }}
        pdfUrl={pdfUrl}
      />

      <div className="min-w-0">
        <p
          className="truncate font-medium text-foreground"
          title={priced?.product_name ?? item.description}
        >
          {item.description}
        </p>
        {priced?.product_name && priced.product_name !== item.description ? (
          <p className="truncate text-xs text-muted-foreground" title={priced.product_name}>
            {priced.product_name}
          </p>
        ) : null}
        {/* Status badges live on their own line so a long product
            name never clips them (R1 from the 2026-11-11 manual
            pass). `flex-wrap` keeps them legible on narrow columns. */}
        {renderBadgeRow(item, flaggedReasons)}
      </div>

      <p className="tabular text-xs text-muted-foreground">
        {item.quantity_text ? (
          <>
            {item.quantity_text}
            {item.unit_raw ? ` ${item.unit_raw}` : ""}
          </>
        ) : (
          <span aria-hidden>—</span>
        )}
      </p>

      {priced ? (
        <PricedCell item={item} />
      ) : (
        <div className="flex items-center gap-2">
          <Skeleton className="h-3.5 w-16" />
          <Skeleton className="h-3.5 w-10" />
        </div>
      )}

      {priced ? (
        <div className="flex items-center justify-end gap-2">
          {priced.shortfall !== null && priced.shortfall > 0 ? (
            <span
              className="inline-flex items-center gap-1 rounded-md bg-warn-amber/10 px-1.5 py-0.5 text-xs font-medium text-warn-amber-text"
              title={`Shortfall: ${priced.shortfall}`}
            >
              <ArrowRightLeftIcon className="size-3" aria-hidden />
              short
            </span>
          ) : null}
          {showMatchedChips ? (
            <Money cents={priced.unit_price_cents} className="text-xs" />
          ) : null}
        </div>
      ) : (
        <div className="flex justify-end">
          <Skeleton className="h-3.5 w-14" />
        </div>
      )}

      {priced ? (
        <div className="flex items-center justify-end">
          {showMatchedChips && priced.extended_cents !== null ? (
            <Money cents={priced.extended_cents} className="text-xs font-medium" />
          ) : (
            <span className="text-xs text-muted-foreground" aria-hidden>
              —
            </span>
          )}
        </div>
      ) : (
        <div className="flex justify-end">
          <Skeleton className="h-3.5 w-14" />
        </div>
      )}
    </div>
  );
}

function PricedCell({ item }: { item: ItemStreamView }) {
  const p = item.priced!;
  if (p.status === "no_match") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-fail-red-text">
        <PackageXIcon className="size-3.5" aria-hidden />
        No match
      </span>
    );
  }
  if (p.status === "skipped") {
    return (
      <span className="inline-flex items-center text-xs text-muted-foreground">Skipped</span>
    );
  }
  if (p.status === "failed" || p.status === "timeout") {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-fail-red-text">
        <PackageXIcon className="size-3.5" aria-hidden />
        {p.status === "timeout" ? "Timed out" : "Failed"}
      </span>
    );
  }
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="tabular text-xs">
        {p.sale_qty ?? "—"}
        {p.sale_unit ? ` ${p.sale_unit}` : ""}
      </span>
      {p.confidence ? <ConfidencePill confidence={p.confidence} /> : null}
      {p.is_substitute ? (
        <span
          className="inline-flex items-center gap-1 text-xs text-warn-amber-text"
          title={p.substitute_reason ?? "Substitute"}
        >
          <ArrowRightLeftIcon className="size-3" aria-hidden />
          sub
        </span>
      ) : null}
    </div>
  );
}
