import { cn } from "@/lib/utils";

/**
 * Renders an integer-cents money value (SPEC §2: money is integer
 * cents everywhere). Tabular numerals so columns align. The caller
 * owns alignment — this is an inline span; `text-right` belongs on
 * the containing <td>.
 *
 * `cents = null | undefined` is explicit "no value" — renders a dash
 * and an sr-only "No value" so assistive tech never reads it as zero.
 */
type Props = {
  cents: number | null | undefined;
  className?: string;
  currency?: string;
};

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function Money({ cents, className, currency = "USD" }: Props) {
  if (cents === null || cents === undefined) {
    return (
      <span className={cn("tabular text-muted-foreground", className)}>
        <span aria-hidden>—</span>
        <span className="sr-only">No value</span>
      </span>
    );
  }
  const formatter =
    currency === "USD"
      ? USD
      : new Intl.NumberFormat("en-US", {
          style: "currency",
          currency,
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        });
  return <span className={cn("tabular", className)}>{formatter.format(cents / 100)}</span>;
}
