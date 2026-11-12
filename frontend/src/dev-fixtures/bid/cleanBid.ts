/**
 * Clean-sample fixture bid (for `rfp_clean.pdf` runs). 14 items,
 * all matched, no conflicts, no partial reasons — the happy-path
 * demo. Numbers pinned to the same `tax_rate_ppm` math as the
 * messy fixture (SPEC §9.3 half-up).
 *
 * Shape mirrors `messyBid.ts` so both can be read by the UI
 * without a code path split.
 */
import type {
  Bid,
  BidLine,
  BidTotals,
  MatchInfo,
  ReviewState,
  SourceRef,
} from "@/api";

type CleanRow = {
  item_id: string;
  description: string;
  qty_text: string;
  unit_raw: string;
  page: number;
  quote: string;
  sku: string;
  product_name: string;
  sale_qty: string;
  sale_unit: string;
  unit_price_cents: number;
};

const ROWS: CleanRow[] = [
  { item_id: "item_1",  description: "2x4 x 8' SPF #2", qty_text: "50", unit_raw: "pcs",
    page: 1, quote: "1. 2x4 x 8' SPF #2 ................. 50 pcs",
    sku: "LUM-2X4-8-SPF-2", product_name: "SPF #2 2x4x8", sale_qty: "50", sale_unit: "EA",
    unit_price_cents: 325 },
  { item_id: "item_2",  description: "2x6 x 16' SPF #2", qty_text: "30", unit_raw: "pcs",
    page: 1, quote: "2. 2x6 x 16' SPF #2 ................ 30 pcs",
    sku: "LUM-2X6-16-SPF-2", product_name: "SPF #2 2x6x16", sale_qty: "30", sale_unit: "EA",
    unit_price_cents: 1_400 },
  { item_id: "item_3",  description: "2x8 x 12' SPF #2", qty_text: "20", unit_raw: "pcs",
    page: 1, quote: "3. 2x8 x 12' SPF #2 ................ 20 pcs",
    sku: "LUM-2X8-12-SPF-2", product_name: "SPF #2 2x8x12", sale_qty: "20", sale_unit: "EA",
    unit_price_cents: 1_450 },
  { item_id: "item_4",  description: "4x4 x 8' SPF #2", qty_text: "8", unit_raw: "pcs",
    page: 1, quote: "4. 4x4 x 8' SPF #2 ................. 8 pcs",
    sku: "LUM-4X4-8-SPF-2", product_name: "SPF #2 4x4x8", sale_qty: "8", sale_unit: "EA",
    unit_price_cents: 950 },
  { item_id: "item_5",  description: "1/2 in OSB sheathing", qty_text: "1920", unit_raw: "sq ft",
    page: 2, quote: "5. 1/2 in OSB sheathing ............ 1920 sq ft",
    sku: "SHE-OSB-1_2-48X96", product_name: "1/2 in OSB 48x96", sale_qty: "60", sale_unit: "SHT",
    unit_price_cents: 2_650 },
  { item_id: "item_6",  description: "5/8 in Type X drywall", qty_text: "2400", unit_raw: "sq ft",
    page: 2, quote: "6. 5/8 in Type X drywall ........... 2400 sq ft",
    sku: "DRY-5_8-48X96-TYPEX", product_name: "5/8 in Type-X drywall 48x96", sale_qty: "75", sale_unit: "SHT",
    unit_price_cents: 2_100 },
  { item_id: "item_7",  description: "R-19 insulation", qty_text: "1200", unit_raw: "sq ft",
    page: 2, quote: "7. R-19 insulation ................. 1200 sq ft",
    sku: "INS-R19-BAG", product_name: "R-19 batt insulation", sale_qty: "25", sale_unit: "BAG",
    unit_price_cents: 4_800 },
  { item_id: "item_8",  description: "Asphalt shingles", qty_text: "10", unit_raw: "SQ",
    page: 2, quote: "8. Asphalt shingles ................ 10 SQ",
    sku: "ROOF-SHINGLE-BDL", product_name: "Asphalt shingles (bundle)", sale_qty: "30", sale_unit: "BDL",
    unit_price_cents: 2_950 },
  { item_id: "item_9",  description: "House wrap", qty_text: "1", unit_raw: "roll",
    page: 2, quote: "9. House wrap ...................... 1 roll",
    sku: "WRAP-9X111-ROLL", product_name: "House wrap 9 ft x 111 ft", sale_qty: "1", sale_unit: "ROLL",
    unit_price_cents: 11_900 },
  { item_id: "item_10", description: "Joist hangers 2x8", qty_text: "40", unit_raw: "ea",
    page: 3, quote: "10. Joist hangers 2x8 .............. 40 ea",
    sku: "CON-JHANG-2X8", product_name: "Joist hanger 2x8", sale_qty: "40", sale_unit: "EA",
    unit_price_cents: 310 },
  { item_id: "item_11", description: "Common nails 8d", qty_text: "2", unit_raw: "boxes",
    page: 3, quote: "11. Common nails 8d ................ 2 boxes",
    sku: "FAST-NAIL-COMMON-8D-BOX", product_name: "8d common nails (50 lb box)", sale_qty: "2", sale_unit: "BOX",
    unit_price_cents: 5_200 },
  { item_id: "item_12", description: "Drywall screws 1-1/4 in", qty_text: "2", unit_raw: "boxes",
    page: 3, quote: "12. Drywall screws 1-1/4 in ........ 2 boxes",
    sku: "FAST-SCR-DRYWALL-1_25-BOX", product_name: "Drywall screws 1-1/4 in (5 lb)", sale_qty: "2", sale_unit: "BOX",
    unit_price_cents: 1_850 },
  { item_id: "item_13", description: "Deck screws 3 in", qty_text: "3", unit_raw: "boxes",
    page: 3, quote: "13. Deck screws 3 in ............... 3 boxes",
    sku: "FAST-SCR-DECK-3IN-BOX", product_name: "Deck screws 3 in (5 lb box)", sale_qty: "3", sale_unit: "BOX",
    unit_price_cents: 1_950 },
  { item_id: "item_14", description: "Finish nails 6d", qty_text: "1", unit_raw: "box",
    page: 3, quote: "14. Finish nails 6d ................ 1 box",
    sku: "FAST-NAIL-FIN-6D-BOX", product_name: "Finish nails 6d (1 lb box)", sale_qty: "1", sale_unit: "BOX",
    unit_price_cents: 1_050 },
];

function sourceOf(row: CleanRow): SourceRef {
  return { page: row.page, quote: row.quote, read_via: "text", verified: true };
}

function matchOf(): MatchInfo {
  return {
    status: "matched",
    confidence: "high",
    confidence_reasons: [],
    assumptions: [],
    is_substitute: false,
    substitute_reason: null,
    verify_notes: [],
  };
}

function cleanLine(row: CleanRow, idx: number): BidLine {
  const extended = Math.round(Number(row.sale_qty) * row.unit_price_cents);
  const review: ReviewState = { state: "none", reasons: [], resolved_by: null };
  return {
    line_id: `line_${String(idx + 1).padStart(2, "0")}`,
    item_id: row.item_id,
    description: row.description,
    source: sourceOf(row),
    requested_quantity_text: row.qty_text,
    requested_unit_raw: row.unit_raw,
    sku: row.sku,
    product_name: row.product_name,
    sale_qty: row.sale_qty,
    sale_unit: row.sale_unit,
    conversion_path: null,
    unit_price_cents: row.unit_price_cents,
    catalog_unit_price_cents: row.unit_price_cents,
    extended_cents: extended,
    stock: {
      on_hand: Math.ceil(Number(row.sale_qty) * 1.2),
      shortfall: 0,
      next_receipt: null,
      transfer_options: [],
    },
    match: matchOf(),
    requirements: [],
    review,
    origins: { quantity: "agent", unit_price: "agent", sku: "agent", description: "agent" },
    override_reason: null,
  };
}

const BRANCH_TAX_PPM: Readonly<Record<string, number>> = {
  "BR-NJ-01": 66_250,
  "BR-NY-01": 86_250,
  "BR-PA-01": 60_000,
};

function totalsFor(lines: BidLine[], taxPpm: number): BidTotals {
  const subtotal = lines.reduce((s, l) => s + (l.extended_cents ?? 0), 0);
  const tax = Math.floor((subtotal * taxPpm + 500_000) / 1_000_000);
  return {
    subtotal_cents: subtotal,
    tax_rate_ppm: taxPpm,
    tax_cents: tax,
    total_cents: subtotal + tax,
    priced_lines: lines.filter(
      (l) => l.match?.status === "matched" || l.match?.status === "substitute",
    ).length,
    open_flags: 0,
  };
}

export function buildCleanBid(
  kind: "agent" | "draft" | "final" = "draft",
  revision: number = 1,
  branchId: string = "BR-NJ-01",
): Bid {
  const lines = ROWS.map(cleanLine);
  const taxPpm = BRANCH_TAX_PPM[branchId] ?? 66_250;
  return {
    revision,
    kind,
    generated_by: "orchestrator",
    partial_reasons: [],
    lines,
    exclusions: [],
    labor: [],
    unreadable: [],
    conflicts: [],
    assumptions: [],
    contractor_questions: [],
    summary:
      "Clean sample: 14 materials, every line matched a catalog SKU, no open reviews.",
    totals: totalsFor(lines, taxPpm),
  };
}
