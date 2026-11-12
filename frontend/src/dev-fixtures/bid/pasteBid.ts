/**
 * Paste-sample fixture bid (10 items from `list_paste.txt`).
 * Flat text input, no source pages / quotes; the UI renders the
 * citation chips as "—".
 */
import type { Bid, BidLine, BidTotals } from "@/api";

type PasteRow = {
  item_id: string;
  description: string;
  sku: string;
  product_name: string;
  sale_qty: string;
  sale_unit: string;
  unit_price_cents: number;
};

const ROWS: PasteRow[] = [
  { item_id: "item_1",  description: "2x4 x 8' SPF #2",  sku: "LUM-2X4-8-SPF-2",
    product_name: "SPF #2 2x4x8", sale_qty: "40", sale_unit: "EA", unit_price_cents: 325 },
  { item_id: "item_2",  description: "2x6 x 12' SPF #2", sku: "LUM-2X6-12-SPF-2",
    product_name: "SPF #2 2x6x12", sale_qty: "20", sale_unit: "EA", unit_price_cents: 1_100 },
  { item_id: "item_3",  description: "2x8 x 16' SPF #2", sku: "LUM-2X8-16-SPF-2",
    product_name: "SPF #2 2x8x16", sale_qty: "10", sale_unit: "EA", unit_price_cents: 1_950 },
  { item_id: "item_4",  description: "1/2 in OSB sheathing (960 sq ft)", sku: "SHE-OSB-1_2-48X96",
    product_name: "1/2 in OSB 48x96", sale_qty: "30", sale_unit: "SHT", unit_price_cents: 2_650 },
  { item_id: "item_5",  description: "5/8 in drywall (1600 sq ft)", sku: "DRY-5_8-48X96-TYPEX",
    product_name: "5/8 in Type-X drywall 48x96", sale_qty: "50", sale_unit: "SHT", unit_price_cents: 2_100 },
  { item_id: "item_6",  description: "R-13 insulation (1200 sq ft)", sku: "INS-R13-BAG",
    product_name: "R-13 batt insulation", sale_qty: "25", sale_unit: "BAG", unit_price_cents: 4_200 },
  { item_id: "item_7",  description: "Common nails 8d", sku: "FAST-NAIL-COMMON-8D-BOX",
    product_name: "8d common nails (50 lb box)", sale_qty: "2", sale_unit: "BOX", unit_price_cents: 5_200 },
  { item_id: "item_8",  description: "Deck screws 3 in", sku: "FAST-SCR-DECK-3IN-BOX",
    product_name: "Deck screws 3 in (5 lb box)", sale_qty: "2", sale_unit: "BOX", unit_price_cents: 1_950 },
  { item_id: "item_9",  description: "Joist hangers 2x8", sku: "CON-JHANG-2X8",
    product_name: "Joist hanger 2x8", sale_qty: "20", sale_unit: "EA", unit_price_cents: 310 },
  { item_id: "item_10", description: "House wrap", sku: "WRAP-9X111-ROLL",
    product_name: "House wrap 9 ft x 111 ft", sale_qty: "1", sale_unit: "ROLL", unit_price_cents: 11_900 },
];

function pasteLine(row: PasteRow, idx: number): BidLine {
  const extended = Math.round(Number(row.sale_qty) * row.unit_price_cents);
  return {
    line_id: `line_${String(idx + 1).padStart(2, "0")}`,
    item_id: row.item_id,
    description: row.description,
    // Paste runs have no page/quote — citation chips collapse to "—".
    source: { page: null, quote: null, read_via: null, verified: false },
    requested_quantity_text: row.sale_qty,
    requested_unit_raw: row.sale_unit,
    sku: row.sku,
    product_name: row.product_name,
    sale_qty: row.sale_qty,
    sale_unit: row.sale_unit,
    conversion_path: null,
    unit_price_cents: row.unit_price_cents,
    catalog_unit_price_cents: row.unit_price_cents,
    extended_cents: extended,
    stock: null,
    match: {
      status: "matched",
      confidence: "high",
      confidence_reasons: [],
      assumptions: [],
      is_substitute: false,
      substitute_reason: null,
      verify_notes: [],
    },
    requirements: [],
    review: { state: "none", reasons: [], resolved_by: null },
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
    priced_lines: lines.length,
    open_flags: 0,
  };
}

export function buildPasteBid(
  kind: "agent" | "draft" | "final" = "draft",
  revision: number = 1,
  branchId: string = "BR-PA-01",
): Bid {
  const lines = ROWS.map(pasteLine);
  const taxPpm = BRANCH_TAX_PPM[branchId] ?? 60_000;
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
    summary: "Paste sample: 10 lines parsed from list_paste.txt, all priced.",
    totals: totalsFor(lines, taxPpm),
  };
}
