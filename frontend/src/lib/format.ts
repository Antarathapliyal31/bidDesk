/**
 * Display-only formatters. The frontend never computes totals or
 * taxes — those come from the backend (`bid/totals.py`). These
 * helpers only turn integer values into the strings the UI shows.
 */

/**
 * Format a tax rate stored as integer parts per million (ppm) as a
 * human-readable percentage. 66250 → "6.625%", 70625 → "7.0625%".
 *
 * ppm, not basis points: 1 bp = 100 ppm. With toFixed(4) we cover
 * ppm's full precision (1 ppm = 0.0001%) and the zero-trim keeps
 * whole and half percentages tidy (6.000% → "6%", 6.500% → "6.5%").
 *
 * Mirrors backend `branches.tax_rate_ppm`.
 */
export function formatTaxRate(ppm: number): string {
  const pct = ppm / 10_000;
  const s = pct.toFixed(4).replace(/\.?0+$/, "");
  return `${s}%`;
}

/**
 * Short date string for history rows. Explicit `en-US` locale +
 * UTC time zone so test snapshots don't drift with the host
 * machine's region. "2026-11-09T19:41:03Z" → "Nov 9, 2026".
 */
export function formatShortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}
