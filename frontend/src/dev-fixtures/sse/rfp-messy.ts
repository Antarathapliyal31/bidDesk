/**
 * Scripted SSE event stream for the `run_demo_messy` fixture run.
 *
 * Mirrors `samples/expected/rfp_messy.json` at the terminal state:
 *   - 26 items extracted (kinds, source refs, notes match the JSON)
 *   - 23 priced with a SKU, 1 `no_match` (cedar shakes), 2 `skipped`
 *     (line 25 no length, line 26 quantity-not-in-quote) — all 26
 *     items emit an `item.priced` event so the reducer merges a
 *     priced line for every extracted id
 *   - 2 substitutes (line 3: 17ft→18ft, line 4: SPF→SYP for PT)
 *   - 1 shortfall (line 20: underlayment)
 *   - 1 pint-fallback medium confidence (line 17: 12 yd → 36 ft → 4 EA)
 *   - 2 conflicts (OSB thickness, insulation excluded)
 *   - 1 item.flagged (line 26, quantity_not_in_quote) emitted by the
 *     orchestrator after pricing
 *   - final `bid.ready(open_flags: <derived>)` + `run.finished`
 *     (completed / bid_ready)
 *
 * Span shape (SPEC §7.1 loop ordering):
 *   analyst.extract (closed after items) → orchestrator.loop (open) →
 *     ↳ classify / check_gap_rules / add_requirements tools →
 *     ↳ pricing_agent.batch (nested, parent = orchestrator.loop) →
 *     ↳ conflict.flagged × 2 + item.flagged × 1 →
 *   orchestrator.loop (closed) → bid.ready → run.finished.
 *
 * Verification rule for this fixture: `source.verified = true`
 * ⟺ `read_via = "text"`. Vision pages (page 7) carry
 * `verified: false`.
 *
 * Phase 5 merge: this module + the driver + fixture-aware branch
 * of `useRunStream` are deleted.
 */
import type { ConflictView, ItemView, PricedLineView, RunEvent, SourceRef } from "@/api";

export const RFP_MESSY_RUN_ID = "run_demo_messy";

const TS_BASE = Date.parse("2026-11-11T12:00:00Z");
function ts(offsetSec: number): string {
  return new Date(TS_BASE + offsetSec * 1000).toISOString();
}

let seq = 0;
function nextId(): number {
  seq += 1;
  return seq;
}

/**
 * Compose a SourceRef. `verified` is DERIVED from `read_via`:
 * only text-layer quotes are verified in this fixture. Vision-page
 * extractions are unverified until the estimator accepts them.
 */
function src(page: number, quote: string, read_via: "text" | "vision" = "text"): SourceRef {
  return { page, quote, read_via, verified: read_via === "text" };
}

function extracted(
  id: string,
  kind: ItemView["kind"],
  description: string,
  qty: string | null,
  unit: string | null,
  source: SourceRef,
  notes: string[] = [],
): RunEvent {
  const idx = parseInt(id.split("_")[1] ?? "0", 10);
  return {
    id: nextId(),
    run_id: RFP_MESSY_RUN_ID,
    ts: ts(10 + idx * 0.2),
    agent: "document_analyst",
    span_id: "s_extract",
    parent_span_id: null,
    type: "item.extracted",
    payload: { item: { id, kind, description, quantity_text: qty, unit_raw: unit, source, notes } },
  };
}

function pricedEvent(line: PricedLineView, offsetSec: number): RunEvent {
  return {
    id: nextId(),
    run_id: RFP_MESSY_RUN_ID,
    ts: ts(offsetSec),
    agent: "pricing_agent",
    span_id: "s_pricing",
    parent_span_id: "s_orch",
    type: "item.priced",
    payload: { line },
  };
}

/** Shorthand for a fully-populated PricedLineView. */
function p(
  item_id: string,
  status: PricedLineView["status"],
  sku: string | null,
  product_name: string | null,
  sale_qty: string | null,
  sale_unit: string | null,
  unit_price_cents: number | null,
  extras: Partial<PricedLineView> = {},
): PricedLineView {
  const extended =
    unit_price_cents !== null && sale_qty !== null ? unit_price_cents * Number(sale_qty) : null;
  return {
    item_id,
    status,
    sku,
    product_name,
    sale_qty,
    sale_unit,
    unit_price_cents,
    extended_cents: extras.extended_cents ?? extended,
    conversion_path: extras.conversion_path ?? null,
    confidence: extras.confidence ?? (status === "matched" ? "high" : null),
    confidence_reasons: extras.confidence_reasons ?? [],
    shortfall: extras.shortfall ?? 0,
    is_substitute: extras.is_substitute ?? false,
    substitute_reason: extras.substitute_reason ?? null,
  };
}

// ─── Priced table (26 lines) ──────────────────────────────────────

export const PRICED_LINES: readonly PricedLineView[] = [
  p("item_1",  "matched",    "LUM-2X4-8-SPF-2",         "SPF #2 2x4x8",             "100", "EA", 325),
  p("item_2",  "matched",    "LUM-2X6-16-SYP-2-PTAG",   "SYP #2 2x6x16 PT above-ground", "40",  "EA", 1_575),
  p("item_3",  "substitute", "LUM-2X10-18-SPF-2",       "SPF #2 2x10x18",           "20",  "EA", 3_200,
    { is_substitute: true, substitute_reason: "17 ft not stocked; next longer is 18 ft",
      confidence: "medium", confidence_reasons: ["Substituted 2x10x18 for requested 2x10x17"] }),
  p("item_4",  "substitute", "LUM-4X4-8-SYP-2-PTGC",    "SYP #2 4x4x8 PT ground-contact", "20",  "EA", 1_125,
    { is_substitute: true, substitute_reason: "SPF requested but PT lumber only stocked in SYP",
      confidence: "medium", confidence_reasons: ["PT species switch — see DECISIONS #7"] }),
  p("item_5",  "matched",    "LUM-2X6-8-SYP-2-PTAG",    "SYP #2 2x6x8 PT above-ground", "2",   "EA", 790,
    { confidence: "medium", confidence_reasons: ["Length not specified — assumed 8 ft"],
      conversion_path: "12 LF → 2 EA (length_ft = 8)" }),
  p("item_6",  "matched",    "LUM-2X8-12-SPF-2",        "SPF #2 2x8x12",            "20",  "EA", 1_450),
  p("item_7",  "matched",    "LUM-2X12-14-SPF-2",       "SPF #2 2x12x14",           "10",  "EA", 2_650),
  p("item_8",  "matched",    "SHE-OSB-7_16-48X96",      "7/16 in OSB 48x96",        "30",  "SHT", 2_250,
    { conversion_path: "960 SF → 30 SHT (coverage 32 SF/sheet)" }),
  p("item_9",  "matched",    "SHE-CDX-1_2-48X96",       "1/2 in CDX plywood 48x96", "10",  "SHT", 4_800,
    { conversion_path: "320 SF → 10 SHT (coverage 32 SF/sheet)" }),
  p("item_10", "matched",    "DRY-1_2-48X96-MR",        "1/2 in drywall MR 48x96",  "25",  "SHT", 1_900,
    { conversion_path: "800 SF → 25 SHT (coverage 32 SF/sheet)" }),
  p("item_11", "matched",    "DRY-1_2-48X96",           "1/2 in drywall 48x96",     "38",  "SHT", 1_400,
    { conversion_path: "1200 SF → 38 SHT (coverage 32 SF/sheet)" }),
  p("item_12", "matched",    "DRY-5_8-48X96-TYPEX",     "5/8 in Type-X drywall 48x96", "13", "SHT", 2_100,
    { conversion_path: "400 SF → 13 SHT (coverage 32 SF/sheet)" }),
  p("item_13", "matched",    "DRY-5_8-48X96-TYPEX",     "5/8 in Type-X drywall 48x96", "38", "SHT", 2_100,
    { conversion_path: "1200 SF → 38 SHT (coverage 32 SF/sheet)",
      confidence: "medium",
      confidence_reasons: ["Drywall type not specified — defaulted to Type-X (only 5/8 stocked)"] }),
  p("item_14", "matched",    "INS-R19-BAG",             "R-19 batt insulation",     "25",  "BAG", 4_800,
    { conversion_path: "1200 SF → 25 BAG (coverage 49 SF/bag)" }),
  p("item_15", "matched",    "INS-R30-BAG",             "R-30 batt insulation",     "17",  "BAG", 6_200,
    { conversion_path: "800 SF → 17 BAG (coverage 49 SF/bag)" }),
  p("item_16", "matched",    "INS-R13-BAG",             "R-13 batt insulation",     "9",   "BAG", 4_200,
    { conversion_path: "400 SF → 9 BAG (coverage 49 SF/bag)" }),
  p("item_17", "matched",    "TRM-1X6-10-SPF-2",        "SPF #2 1x6x10 trim",       "4",   "EA", 1_250,
    { confidence: "medium",
      confidence_reasons: ["unit converted from \"12 yards\" via pint"],
      conversion_path: "12 yd → 36 ft → 4 EA (length_ft = 10)" }),
  p("item_18", "no_match",   null,                      null,                       null,   null,  null,
    { confidence: null, confidence_reasons: ["Cedar shakes not in catalog"] }),
  p("item_19", "matched",    "ROOF-SHINGLE-BDL",        "Asphalt shingles (bundle)", "45",  "BDL", 2_950,
    { conversion_path: "15 SQ → 1500 SF → 45 BDL (3 bdl/sq)",
      confidence: "medium",
      confidence_reasons: ["Roofing type not specified — defaulted to 3-tab asphalt"] }),
  p("item_20", "matched",    "ROOF-UNDERLAY-ROLL",      "Roofing underlayment roll", "4",   "ROLL", 3_450,
    { shortfall: 4,
      confidence_reasons: ["Shortfall: zero stock at BR-NJ-01; no transfer branch covers"] }),
  p("item_21", "matched",    "FAST-NAIL-COMMON-8D-BOX", "8d common nails (50 lb box)", "2",  "BOX", 5_200),
  p("item_22", "matched",    "FAST-SCR-DECK-3IN-BOX",   "Deck screws 3 in (5 lb box)", "3",  "BOX", 1_950),
  p("item_23", "matched",    "CON-JHANG-2X8",           "Joist hanger 2x8",         "40",  "EA", 310),
  p("item_24", "matched",    "SHE-OSB-1_2-48X96",       "1/2 in OSB 48x96",         "20",  "SHT", 2_650,
    { conversion_path: "640 SF → 20 SHT (coverage 32 SF/sheet)" }),
  p("item_25", "skipped",    null,                      null,                       null,   null,  null,
    { confidence: null, confidence_reasons: ["Length not specified — flagged for estimator"] }),
  p("item_26", "skipped",    null,                      null,                       null,   null,  null,
    { confidence: null, confidence_reasons: ["Quantity not in quote — flagged by analyst"] }),
];

// ─── Conflicts ────────────────────────────────────────────────────

export const CONFLICTS: readonly ConflictView[] = [
  {
    id: "conf_1",
    type: "size_mismatch",
    side_a: { item_id: "item_8",  source: src(3, "8. 7/16 in OSB for roof sheathing ................. 960 sq ft") },
    side_b: { item_id: "item_24", source: src(8, "21. 1/2 in OSB for roof sheathing ................. 640 sq ft") },
    explanation: "Roof sheathing thickness conflict (spec vs schedule)",
  },
  {
    id: "conf_2",
    type: "excluded_but_listed",
    side_a: { item_id: null,       source: src(8, "- Insulation by others.") },
    side_b: { item_id: "item_14",  source: src(5, "14. R-19 insulation ............................... 1200 sq ft") },
    explanation: "Insulation is excluded but R-values appear in the schedule",
  },
];

// ─── Derived open_flags ──────────────────────────────────────────

/** Items that get an item.flagged event after pricing. */
const FLAGGED_ITEM_IDS = ["item_26"] as const;

/**
 * Enumerate all items whose review opens per SPEC §9.3 bullet 3:
 * unverified, substitute, no_match/skipped/timeout/failed, shortfall,
 * confidence low, involved in a conflict, flagged by name.
 */
function computeOpenFlags(extractedItems: Iterable<ItemView>): number {
  const open = new Set<string>();
  for (const item of extractedItems) {
    if (!item.source.verified) open.add(item.id);
  }
  for (const line of PRICED_LINES) {
    if (
      line.status === "no_match" ||
      line.status === "failed" ||
      line.status === "timeout" ||
      line.status === "skipped"
    )
      open.add(line.item_id);
    if (line.is_substitute) open.add(line.item_id);
    if ((line.shortfall ?? 0) > 0) open.add(line.item_id);
    if (line.confidence === "low") open.add(line.item_id);
  }
  for (const c of CONFLICTS) {
    if (c.side_a.item_id) open.add(c.side_a.item_id);
    if (c.side_b.item_id) open.add(c.side_b.item_id);
  }
  for (const id of FLAGGED_ITEM_IDS) open.add(id);
  return open.size;
}

// ─── Extracted items (28 lines in rfp_messy — 26 items + 2 skipped nulls) ─

export const EXTRACTED_PAYLOADS: ItemView[] = [
  { id: "item_1", kind: "material", description: "2x4 x 8' SPF #2",
    quantity_text: "100", unit_raw: "pcs",
    source: src(2, "1. (100) 2x4 x 8' SPF #2"), notes: [] },
  { id: "item_2", kind: "material", description: "2x6 x 16' pressure-treated",
    quantity_text: "40", unit_raw: "pcs",
    source: src(2, "2. (40) 2x6 x 16' pressure-treated"), notes: [] },
  { id: "item_3", kind: "material", description: "2x10 x 17' SPF",
    quantity_text: "20", unit_raw: "pcs",
    // Matches samples/expected/rfp_messy.json verbatim (the PDF
    // has double spaces around the em dash; the analyst normalizes
    // to single spaces — either form highlights the same line via
    // normalizeQuote's whitespace collapse).
    source: src(2, "3. Qty: 20 — 2x10 x 17' SPF"), notes: [] },
  { id: "item_4", kind: "material", description: "4x4 x 8' SPF #2 (ground contact)",
    quantity_text: "20", unit_raw: "pcs",
    source: src(2, "4. twenty (20) 4x4 x 8' SPF #2 (ground contact)"), notes: [] },
  // Item 5 is wrapped across two lines in the PDF (see
  // make_samples.py page-2 block). The verbatim quote preserves
  // both lines so the SourcePanel's customTextRenderer highlights
  // the full row, not just the first wrap. The analyst's quantity-
  // in-quote check (SPEC §6.4 bullet 2) sees "12 LF" at the end.
  { id: "item_5", kind: "material", description: "2x6 PT for sill plates on the concrete slab",
    quantity_text: "12", unit_raw: "LF",
    source: src(2, "5. 2x6 PT for sill plates on the concrete slab ................... 12 LF"), notes: [] },
  { id: "item_6", kind: "material", description: "2x8 x 12' SPF",
    quantity_text: "20", unit_raw: "pcs",
    source: src(3, "6. 2x8 x 12' SPF .................................. 20 pcs"), notes: [] },
  { id: "item_7", kind: "material", description: "2x12 x 14' SPF #2",
    quantity_text: "10", unit_raw: "pcs",
    source: src(3, "7. 2x12 x 14' SPF #2 .............................. 10 pcs"), notes: [] },
  { id: "item_8", kind: "material", description: "7/16 in OSB for roof sheathing",
    quantity_text: "960", unit_raw: "sq ft",
    source: src(3, "8. 7/16 in OSB for roof sheathing ................. 960 sq ft"), notes: [] },
  { id: "item_9", kind: "material", description: "1/2 in CDX plywood for exterior wall sheathing",
    quantity_text: "320", unit_raw: "sq ft",
    source: src(3, "9. 1/2 in CDX plywood for exterior wall sheathing 320 sq ft"), notes: [] },
  { id: "item_10", kind: "material", description: "1/2 in drywall for upstairs bathrooms",
    quantity_text: "800", unit_raw: "sq ft",
    source: src(4, "10. 1/2 in drywall for upstairs bathrooms ......... 800 sq ft"), notes: [] },
  { id: "item_11", kind: "material", description: "1/2 in drywall for bedrooms",
    quantity_text: "1200", unit_raw: "sq ft",
    source: src(4, "11. 1/2 in drywall for bedrooms ................... 1200 sq ft"), notes: [] },
  { id: "item_12", kind: "material", description: "5/8 in Type X drywall at garage ceiling",
    quantity_text: "400", unit_raw: "sq ft",
    source: src(4, "12. 5/8 in Type X drywall at garage ceiling ....... 400 sq ft"), notes: [] },
  { id: "item_13", kind: "material", description: "5/8 in drywall for ceilings",
    quantity_text: "1200", unit_raw: "sq ft",
    source: src(4, "13. 5/8 in drywall for ceilings ................... 1200 sq ft"), notes: [] },
  { id: "item_14", kind: "material", description: "R-19 insulation",
    quantity_text: "1200", unit_raw: "sq ft",
    source: src(5, "14. R-19 insulation ............................... 1200 sq ft"), notes: [] },
  { id: "item_15", kind: "material", description: "R-30 insulation for attic",
    quantity_text: "800", unit_raw: "sq ft",
    source: src(5, "15. R-30 insulation for attic ..................... 800 sq ft"), notes: [] },
  { id: "item_16", kind: "material", description: "R-13 insulation for 2x4 walls",
    quantity_text: "400", unit_raw: "sq ft",
    source: src(5, "16. R-13 insulation for 2x4 walls ................. 400 sq ft"), notes: [] },
  { id: "item_17", kind: "material", description: "Fascia (1x6 trim)",
    quantity_text: "12", unit_raw: "yards",
    source: src(5, "17. Fascia (1x6 trim) ............................. 12 yards"),
    notes: ["unit_pint_convertible"] },
  { id: "item_18", kind: "material", description: "Cedar shakes",
    quantity_text: "10", unit_raw: "SQ",
    source: src(6, "18. Cedar shakes .................................. 10 SQ"), notes: [] },
  { id: "item_19", kind: "material", description: "Asphalt shingles",
    quantity_text: "15", unit_raw: "SQ",
    source: src(6, "19. Asphalt shingles .............................. 15 SQ"), notes: [] },
  { id: "item_20", kind: "material", description: "Underlayment roll",
    quantity_text: "4", unit_raw: "rolls",
    source: src(6, "20. Underlayment roll ............................. 4 rolls"), notes: [] },
  // Vision page (7) — read_via "vision" ⇒ verified=false.
  { id: "item_21", kind: "material", description: "Common nails 8d",
    quantity_text: "2", unit_raw: "boxes",
    source: src(7, "Common nails 8d 2 boxes", "vision"), notes: [] },
  { id: "item_22", kind: "material", description: "Deck screws 3 in",
    quantity_text: "3", unit_raw: "boxes",
    source: src(7, "Deck screws 3 in 3 boxes", "vision"), notes: [] },
  { id: "item_23", kind: "material", description: "Joist hangers 2x8",
    quantity_text: "40", unit_raw: "ea",
    source: src(7, "Joist hangers 2x8 40 ea south wall deck ledger", "vision"), notes: [] },
  { id: "item_24", kind: "material", description: "1/2 in OSB for roof sheathing",
    quantity_text: "640", unit_raw: "sq ft",
    source: src(8, "21. 1/2 in OSB for roof sheathing ................. 640 sq ft"), notes: [] },
  { id: "item_25", kind: "material", description: "Deck ledger at south wall (2x10)",
    quantity_text: "1", unit_raw: "ea",
    source: src(8, "22. Deck ledger at south wall (2x10) .............. 1 ea"), notes: [] },
  { id: "item_26", kind: "material", description: "Roof rafters: 2x8 at 16 in OC",
    quantity_text: null, unit_raw: null,
    source: src(8, "23. Roof rafters: 2x8 at 16 in OC"),
    notes: ["quantity_not_in_quote"] },
];

export const OPEN_FLAGS_COUNT = computeOpenFlags(EXTRACTED_PAYLOADS);

// ─── Build the stream ─────────────────────────────────────────────

const events: RunEvent[] = [];

// run.started
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(0),
  agent: null,
  span_id: null,
  parent_span_id: null,
  type: "run.started",
  payload: {
    input_kind: "pdf",
    input_name: "rfp_messy.pdf",
    page_count: 8,
    branch_id: "BR-NJ-01",
  },
});

// 8 × page.routed — matches rfp_messy.json page_routes
export const PAGE_ROUTES: Array<"text" | "vision" | "text+vision" | "unreadable"> = [
  "text", "text", "text", "text", "text", "text", "text+vision", "text",
];
PAGE_ROUTES.forEach((route, i) => {
  events.push({
    id: nextId(),
    run_id: RFP_MESSY_RUN_ID,
    ts: ts(1 + i * 0.1),
    agent: "document_analyst",
    span_id: null,
    parent_span_id: null,
    type: "page.routed",
    payload: { page_no: i + 1, route },
  });
});

// Analyst extract span
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(2),
  agent: "document_analyst",
  span_id: "s_extract",
  parent_span_id: null,
  type: "span.started",
  payload: { name: "analyst.extract", label: "Reading 8 pages" },
});
EXTRACTED_PAYLOADS.forEach((item) => {
  events.push(
    extracted(item.id, item.kind, item.description, item.quantity_text, item.unit_raw, item.source, item.notes),
  );
});
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(16),
  agent: "document_analyst",
  span_id: "s_extract",
  parent_span_id: null,
  type: "span.finished",
  payload: { name: "analyst.extract", duration_ms: 12_000, summary: "26 items" },
});

// Orchestrator loop (stays open across pricing)
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(17),
  agent: "orchestrator",
  span_id: "s_orch",
  parent_span_id: null,
  type: "span.started",
  payload: { name: "orchestrator.loop", label: "Triage + enrich + price" },
});
events.push(
  { id: nextId(), run_id: RFP_MESSY_RUN_ID, ts: ts(17.2),
    agent: "orchestrator", span_id: "s_orch", parent_span_id: null,
    type: "tool.called", payload: { tool: "classify_items", args_preview: "26 items" } },
  { id: nextId(), run_id: RFP_MESSY_RUN_ID, ts: ts(17.3),
    agent: "orchestrator", span_id: "s_orch", parent_span_id: null,
    type: "tool.result", payload: { tool: "classify_items", ok: true, result_preview: "price: 26" } },
  { id: nextId(), run_id: RFP_MESSY_RUN_ID, ts: ts(17.5),
    agent: "orchestrator", span_id: "s_orch", parent_span_id: null,
    type: "tool.called", payload: { tool: "check_gap_rules", args_preview: "26 items" } },
  { id: nextId(), run_id: RFP_MESSY_RUN_ID, ts: ts(17.7),
    agent: "orchestrator", span_id: "s_orch", parent_span_id: null,
    type: "tool.result", payload: { tool: "check_gap_rules", ok: true,
      result_preview: "3 requirement rules, 7 questions" } },
  { id: nextId(), run_id: RFP_MESSY_RUN_ID, ts: ts(17.9),
    agent: "orchestrator", span_id: "s_orch", parent_span_id: null,
    type: "tool.called", payload: { tool: "add_requirements", args_preview: "{ item_5, item_4, item_10 }" } },
  { id: nextId(), run_id: RFP_MESSY_RUN_ID, ts: ts(18.0),
    agent: "orchestrator", span_id: "s_orch", parent_span_id: null,
    type: "tool.result", payload: { tool: "add_requirements", ok: true, result_preview: "3 applied" } },
);

// Pricing batch — NESTED under the orchestrator span.
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(20),
  agent: "pricing_agent",
  span_id: "s_pricing",
  parent_span_id: "s_orch",
  type: "span.started",
  payload: { name: "pricing_agent.batch", label: "Pricing 26 lines" },
});
PRICED_LINES.forEach((line, i) => {
  events.push(pricedEvent(line, 21 + i * 0.1));
});
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(24),
  agent: "pricing_agent",
  span_id: "s_pricing",
  parent_span_id: "s_orch",
  type: "span.finished",
  payload: {
    name: "pricing_agent.batch",
    duration_ms: 4_000,
    summary: "26 lines: 23 priced, 1 no_match, 2 skipped",
  },
});

// Conflicts + item.flagged — orchestrator reviews the priced bid.
CONFLICTS.forEach((conflict, i) => {
  events.push({
    id: nextId(),
    run_id: RFP_MESSY_RUN_ID,
    ts: ts(24.2 + i * 0.1),
    agent: "orchestrator",
    span_id: "s_orch",
    parent_span_id: null,
    type: "conflict.flagged",
    payload: { conflict },
  });
});
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(24.5),
  agent: "orchestrator",
  span_id: "s_orch",
  parent_span_id: null,
  type: "item.flagged",
  payload: { item_id: "item_26", reason_code: "quantity_not_in_quote", message: null },
});

// Close orchestrator span after pricing + review.
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(25),
  agent: "orchestrator",
  span_id: "s_orch",
  parent_span_id: null,
  type: "span.finished",
  payload: { name: "orchestrator.loop", duration_ms: 8_000, summary: "triaged + priced + conflicts flagged" },
});

// bid.ready + run.finished
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(25.2),
  agent: null,
  span_id: null,
  parent_span_id: null,
  type: "bid.ready",
  payload: { revision: 0, generated_by: "orchestrator", open_flags: OPEN_FLAGS_COUNT },
});
events.push({
  id: nextId(),
  run_id: RFP_MESSY_RUN_ID,
  ts: ts(25.5),
  agent: null,
  span_id: null,
  parent_span_id: null,
  type: "run.finished",
  payload: { status: "completed", outcome: "bid_ready", outcome_detail: null },
});

export const RFP_MESSY_EVENTS: readonly RunEvent[] = events;
