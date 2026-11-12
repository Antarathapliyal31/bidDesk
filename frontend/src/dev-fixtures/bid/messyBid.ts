/**
 * Canonical fixture Bid derived from the rfp_messy priced lines
 * (see `src/dev-fixtures/sse/rfp-messy.ts`). Produces a full
 * `Bid` shape — lines, exclusions, labor, assumptions,
 * contractor questions, conflicts, totals — so `BidWorkspace`
 * can render against it without a backend.
 *
 * Numbers pinned to SPEC §9.3 math (half-up tax) + DECISIONS #11
 * (tax_rate_ppm). Deterministic: the same fixture rebuild gives
 * identical totals.
 *
 * Phase 5 merge: this module is deleted alongside the SSE fixture.
 * Backend's `/api/runs/:id` returns the real `Bid` through the
 * same contract (`src/api/bid.ts`) — call sites don't change.
 */
import type {
  AssumptionView,
  Bid,
  BidLine,
  BidTotals,
  ContractorQuestionView,
  ExclusionLine,
  LaborLine,
  MatchInfo,
  RequirementView,
  ReviewReasonCode,
  ReviewState,
  SourceRef,
} from "@/api";

import { PRICED_LINES, EXTRACTED_PAYLOADS, CONFLICTS } from "../sse/rfp-messy";

const DEFAULT_ORIGINS: BidLine["origins"] = {
  quantity: "agent",
  unit_price: "agent",
  sku: "agent",
  description: "agent",
};

function reviewFromPriced(
  item: (typeof EXTRACTED_PAYLOADS)[number],
  priced: (typeof PRICED_LINES)[number],
): ReviewState {
  const reasons: ReviewReasonCode[] = [];
  if (!item.source.verified && item.source.read_via === "text") {
    reasons.push("quote_not_found");
  }
  if (item.notes.includes("quantity_not_in_quote")) reasons.push("quantity_not_in_quote");
  if (item.notes.includes("unknown_unit")) reasons.push("unknown_unit");
  if (priced.status === "no_match") reasons.push("no_match");
  if (priced.status === "failed") reasons.push("failed");
  if (priced.status === "timeout") reasons.push("timeout");
  if (priced.status === "skipped") reasons.push("not_priced");
  if (priced.shortfall !== null && priced.shortfall > 0) reasons.push("shortfall");
  if (priced.is_substitute) reasons.push("substitute");
  if (priced.confidence === "low") reasons.push("low_confidence");
  return {
    state: reasons.length > 0 ? "open" : "none",
    reasons,
    resolved_by: null,
  };
}

function matchFromPriced(priced: (typeof PRICED_LINES)[number]): MatchInfo {
  return {
    status: priced.status,
    confidence: priced.confidence,
    confidence_reasons: [...priced.confidence_reasons],
    assumptions: [],
    is_substitute: priced.is_substitute,
    substitute_reason: priced.substitute_reason,
    verify_notes: [],
  };
}

function sourceOf(item: (typeof EXTRACTED_PAYLOADS)[number]): SourceRef {
  return { ...item.source };
}

function stockFromPriced(priced: (typeof PRICED_LINES)[number]) {
  if (priced.status !== "matched" && priced.status !== "substitute") return null;
  const shortfall = priced.shortfall ?? 0;
  const requested = priced.sale_qty ? Number(priced.sale_qty) : 0;
  const on_hand = Math.max(0, requested - shortfall);
  return {
    on_hand,
    shortfall,
    next_receipt: shortfall > 0 ? { date: "2026-12-22", qty: requested * 2 } : null,
    transfer_options: [],
  };
}

function priceLineRequirements(itemId: string): RequirementView[] {
  // A couple of hand-authored requirements matched to the messy
  // fixture's gap-rule set (SPEC §7.4). Only item_5 (sill plates)
  // and item_4 (ground-contact posts) get them; everything else is
  // empty. Keeps the demo honest without over-populating.
  if (itemId === "item_5") {
    return [
      {
        field: "treatment",
        value: "pressure_treated_above_ground",
        reason: "Sill plates on concrete slab require PT per code.",
        source: { page: 2, quote: "concrete slab", read_via: "text", verified: true },
      },
    ];
  }
  if (itemId === "item_4") {
    return [
      {
        field: "treatment",
        value: "pressure_treated_ground_contact",
        reason: "4x4 posts noted as ground-contact.",
        source: {
          page: 2,
          quote: "(ground contact)",
          read_via: "text",
          verified: true,
        },
      },
    ];
  }
  return [];
}

function buildBidLine(
  item: (typeof EXTRACTED_PAYLOADS)[number],
  idx: number,
): BidLine {
  const priced = PRICED_LINES.find((p) => p.item_id === item.id);
  // Items without a priced row (shouldn't happen in the messy
  // fixture but guard anyway): synthesize a "not priced" line.
  const match: MatchInfo = priced
    ? matchFromPriced(priced)
    : {
        status: "skipped",
        confidence: null,
        confidence_reasons: ["No pricing result"],
        assumptions: [],
        is_substitute: false,
        substitute_reason: null,
        verify_notes: [],
      };
  const salePrice = priced?.unit_price_cents ?? null;
  const saleQty = priced?.sale_qty ?? null;
  const extended =
    salePrice !== null && saleQty !== null
      ? Math.round(Number(saleQty) * salePrice)
      : null;
  const review: ReviewState = priced
    ? reviewFromPriced(item, priced)
    : { state: "open", reasons: ["not_priced"], resolved_by: null };
  return {
    line_id: `line_${String(idx + 1).padStart(2, "0")}`,
    item_id: item.id,
    description: item.description,
    source: sourceOf(item),
    requested_quantity_text: item.quantity_text,
    requested_unit_raw: item.unit_raw,
    sku: priced?.sku ?? null,
    product_name: priced?.product_name ?? null,
    sale_qty: saleQty,
    sale_unit: priced?.sale_unit ?? null,
    conversion_path: null,
    unit_price_cents: salePrice,
    catalog_unit_price_cents: salePrice,
    extended_cents: extended,
    stock: stockFromPriced(priced ?? pricedPlaceholder()),
    match,
    requirements: priceLineRequirements(item.id),
    review,
    origins: DEFAULT_ORIGINS,
    override_reason: null,
  };
}

function pricedPlaceholder(): (typeof PRICED_LINES)[number] {
  return {
    item_id: "",
    status: "skipped",
    sku: null,
    product_name: null,
    sale_qty: null,
    sale_unit: null,
    unit_price_cents: null,
    extended_cents: null,
    confidence: null,
    confidence_reasons: [],
    shortfall: null,
    is_substitute: false,
    substitute_reason: null,
    conversion_path: null,
  };
}

/** SPEC §9.3 half-up tax; matches backend `bid/totals.py`. */
function computeTotals(lines: BidLine[], taxRatePpm: number, openFlags: number): BidTotals {
  const subtotal = lines.reduce((sum, l) => sum + (l.extended_cents ?? 0), 0);
  const tax = Math.floor((subtotal * taxRatePpm + 500_000) / 1_000_000);
  const pricedLines = lines.filter(
    (l) => l.match?.status === "matched" || l.match?.status === "substitute",
  ).length;
  return {
    subtotal_cents: subtotal,
    tax_rate_ppm: taxRatePpm,
    tax_cents: tax,
    total_cents: subtotal + tax,
    priced_lines: pricedLines,
    open_flags: openFlags,
  };
}

const EXCLUSIONS: ExclusionLine[] = [
  {
    description: "Insulation by others.",
    source: { page: 8, quote: "- Insulation by others.", read_via: "text", verified: true },
  },
  {
    description: "Painting not included.",
    source: { page: 8, quote: "- Painting not included.", read_via: "text", verified: true },
  },
];

const LABOR: LaborLine[] = [
  {
    description: "Install drywall",
    quantity_text: "40",
    unit_raw: "hours",
    source: {
      page: 8,
      quote: "24. Install drywall ............................... 40 hours",
      read_via: "text",
      verified: true,
    },
  },
];

const ASSUMPTIONS: AssumptionView[] = [
  { item_id: "item_5", text: "Length not specified — assumed 8 ft" },
  { item_id: "item_13", text: "Drywall type not specified — defaulted to Type-X" },
  { item_id: "item_17", text: 'Unit converted from "12 yards" via pint fallback' },
  { item_id: "item_19", text: "Roofing type not specified — defaulted to 3-tab asphalt" },
];

const CONTRACTOR_QUESTIONS: ContractorQuestionView[] = [
  {
    text: "Confirm whether sill plates on concrete should be PT above-ground or ground-contact.",
    basis: { kind: "gap_rule", ref_id: "pt_sill_plates_on_concrete" },
  },
  {
    text: "Specify roof sheathing thickness — 7/16 in (page 3) and 1/2 in (page 8) both appear.",
    basis: { kind: "conflict", ref_id: "osb_thickness" },
  },
  {
    text: "Specify drywall type for bedrooms — page 4 line 11.",
    basis: { kind: "flagged_line", ref_id: "item_11" },
  },
];

const BRANCH_TAX_PPM: Readonly<Record<string, number>> = {
  "BR-NJ-01": 66_250,
  "BR-NY-01": 86_250,
  "BR-PA-01": 60_000,
};

/**
 * Build the messy fixture Bid at a given kind + revision. The
 * initial state is `kind: "agent", revision: 0`; drafts start as
 * a copy at `revision: 1`.
 */
export function buildMessyBid(
  kind: "agent" | "draft" | "final" = "draft",
  revision: number = 1,
  branchId: string = "BR-NJ-01",
): Bid {
  const lines = EXTRACTED_PAYLOADS.map((item, idx) => buildBidLine(item, idx));
  const openFlags = lines.filter((l) => l.review.state === "open").length;
  const totals = computeTotals(lines, BRANCH_TAX_PPM[branchId] ?? 66_250, openFlags);
  return {
    revision,
    kind,
    generated_by: "orchestrator",
    partial_reasons: [],
    lines,
    exclusions: EXCLUSIONS,
    labor: LABOR,
    unreadable: [],
    conflicts: [...CONFLICTS],
    assumptions: ASSUMPTIONS,
    contractor_questions: CONTRACTOR_QUESTIONS,
    summary:
      "Materials bid for the messy RFP sample. Two conflicts flagged (roof sheathing thickness; insulation listed but excluded). Three items need confirmation (sill plate treatment, drywall type, roofing type).",
    totals,
  };
}
