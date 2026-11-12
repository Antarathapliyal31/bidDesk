import { Money } from "@/components/Money";
import type { BidTotals } from "@/api";
import { formatTaxRate } from "@/lib/format";

/**
 * Server-confirmed totals (SPEC §9.3). Right-aligned, tabular
 * numerals. The frontend never recomputes — these are the
 * numbers from `/bid`'s response and the bid-editing PATCH's
 * response. Tax uses branch `tax_rate_ppm` (half-up per SPEC).
 */
type Props = {
  totals: BidTotals;
};

export function TotalsBar({ totals }: Props) {
  return (
    <section
      aria-label="Totals"
      className="rounded-md border border-border bg-muted/20 px-4 py-3"
    >
      <dl className="ml-auto grid w-fit min-w-[260px] grid-cols-[auto_auto] gap-x-6 gap-y-1 text-sm">
        <dt className="text-muted-foreground">Subtotal</dt>
        <dd className="tabular text-right">
          <Money cents={totals.subtotal_cents} />
        </dd>
        <dt className="text-muted-foreground">Tax ({formatTaxRate(totals.tax_rate_ppm)})</dt>
        <dd className="tabular text-right">
          <Money cents={totals.tax_cents} />
        </dd>
        <dt className="border-t border-border pt-1 text-foreground font-medium">Total</dt>
        <dd className="tabular border-t border-border pt-1 text-right font-semibold">
          <Money cents={totals.total_cents} />
        </dd>
      </dl>
      <p className="mt-2 text-right text-xs text-muted-foreground">
        {totals.priced_lines} priced lines
        {totals.open_flags > 0 ? ` · ${totals.open_flags} open flags` : ""}
      </p>
    </section>
  );
}
