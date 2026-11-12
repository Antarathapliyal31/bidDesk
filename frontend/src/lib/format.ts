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
 * Format a file size in bytes with units that scale by magnitude.
 * Under 1 MiB → KB ("320 KB"); 1 MiB and up → one-decimal MB
 * ("1.2 MB"). Avoids the "0.0 MB" output a fixed-MB formatter
 * produces for ~hundreds-of-KB uploads.
 *
 * Binary units (1024-based) match `MAX_UPLOAD_BYTES` in `limits.ts`
 * — see DECISIONS #… / lib/limits.ts for why we picked MiB over MB.
 */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  const KB = 1024;
  const MB = KB * 1024;
  if (bytes < MB) {
    // Round to the nearest whole KB — sub-KB resolution isn't
    // meaningful at this scale. 0 bytes → "0 KB" (still a valid
    // size string; the empty-file validator catches the real case).
    return `${Math.max(0, Math.round(bytes / KB))} KB`;
  }
  return `${(bytes / MB).toFixed(1)} MB`;
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
