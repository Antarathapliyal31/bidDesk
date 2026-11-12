/**
 * Pure helpers for best-effort quote highlighting in the SourcePanel.
 *
 * The viewer gets a `SourceRef` with `quote: string | null`. On the
 * cited page, react-pdf's `customTextRenderer` is called once per
 * text item; we wrap every item that is contained in the normalized
 * quote (whitespace-collapsed, lowercased, min length 3) in `<mark>`.
 *
 * Misses are fine — the callout above the viewer still names the
 * quote verbatim. No findController, no cross-item spans.
 */
const MIN_HIGHLIGHT_LEN = 3;

/**
 * Collapse all whitespace to single spaces and lowercase. Keeps
 * punctuation intact — PDFs tend to split tokens like "SPF#2" and
 * "SPF #2" across items, so we don't strip anything else.
 */
export function normalizeQuote(s: string): string {
  return s.replace(/\s+/g, " ").trim().toLowerCase();
}

/**
 * `true` if `item` (also normalized) is a non-empty substring of
 * the already-normalized quote and its length is ≥ `minLen`.
 *
 * Items shorter than `minLen` almost always false-match (e.g. "a",
 * ".", "x") and would litter the page with noise.
 */
export function matchesQuote(
  item: string,
  normalizedQuote: string,
  minLen: number = MIN_HIGHLIGHT_LEN,
): boolean {
  const normalized = normalizeQuote(item);
  if (normalized.length < minLen) return false;
  if (normalizedQuote.length === 0) return false;
  return normalizedQuote.includes(normalized);
}

/**
 * Minimal HTML escape for `customTextRenderer` output — react-pdf
 * passes the returned string straight to `dangerouslySetInnerHTML`,
 * so an unescaped `<` in a PDF's text item would break the layer.
 */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
