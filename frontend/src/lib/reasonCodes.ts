/**
 * Human-readable rendering for `item.flagged` / item.notes reason
 * codes (SPEC §6.4, §10.3).
 *
 * One mapping table so the same string shows up wherever a code
 * surfaces — ItemsStream flag chip, future ReviewQueue list item,
 * trace detail rows. Unknown codes fall back to a title-cased form
 * of the raw code so the UI never renders an empty string, and
 * the reason the mapping is missing is visible to anyone looking.
 *
 * `invalid_spec:<field>` is a parameterised code (SPEC §6.4 bullet
 * 5) — we strip the prefix and interpolate the field name into the
 * user-visible string.
 */

const REASON_CODES: Readonly<Record<string, string>> = {
  // SPEC §6.4 notes (also appear as reason_codes on item.flagged)
  quote_not_found: "Quote not found",
  quantity_not_in_quote: "Quantity missing in RFP",
  unknown_unit: "Unknown unit",
  unit_pint_convertible: "Unit convertible — check mapping",

  // Pricing / orchestrator-side flags (SPEC §7.3, §8.3)
  shortfall: "Shortfall at branch",
  substitute_proposed: "Substitute proposed",
  no_match: "No catalog match",
  low_confidence: "Low confidence",
};

export function reasonCodeLabel(code: string): string {
  if (REASON_CODES[code]) return REASON_CODES[code];
  if (code.startsWith("invalid_spec:")) {
    const field = code.slice("invalid_spec:".length);
    return field ? `Invalid spec: ${field}` : "Invalid spec";
  }
  // Last-resort fallback: humanize the raw code. "some_code" →
  // "Some code". Signals a missing entry without leaving a blank
  // chip. Not localised — these are developer-facing strings.
  return code
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase());
}
