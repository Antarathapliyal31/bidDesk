/**
 * Bid contract types (SPEC §9.1). Hand-synced until Phase 5 generates
 * from the backend OpenAPI schema.
 *
 * Money is integer cents (SPEC §2, CLAUDE.md §3). Quantities (`sale_qty`,
 * `catalog_unit_price_cents`, etc.) cross the wire in stable string or
 * integer forms — JS never floats a dollar value.
 */
import type { ConflictView, SourceRef } from "./events";

/**
 * How a given field got its current value (SPEC §9.4):
 *   - "agent"            — the agent's original value is still in place
 *   - "user_filled"      — the agent had nothing; the user filled it in
 *   - "user_overridden"  — the agent had a value; the user overrode it
 * Overridden `unit_price` keeps `catalog_unit_price_cents` alongside so
 * the UI can show the diff.
 */
export type Origin = "agent" | "user_filled" | "user_overridden";

export type MatchStatus =
  | "matched"
  | "substitute"
  | "no_match"
  | "failed"
  | "timeout"
  | "skipped"
  // Phase 8 review: a line the pricing agent could not confidently
  // commit; the server flags it for human review rather than
  // picking a SKU. The UI renders a "Needs review" badge next to
  // the status chip.
  | "needs_review";

export type Confidence = "high" | "medium" | "low";

export type ReviewReasonCode =
  | "quote_not_found"
  | "quantity_not_in_quote"
  | "unknown_unit"
  | "sanity_range"
  | "low_confidence"
  | "no_match"
  | "failed"
  | "timeout"
  | "shortfall"
  | "substitute"
  | "conflict"
  | "not_priced"
  // DECISIONS F19: classify_items tagged the line ambiguous but
  // the pricing completion pass placed a match. Flag kept open
  // so the estimator confirms.
  | "ambiguous";

export type ReviewState = {
  state: "none" | "open" | "resolved";
  reasons: ReviewReasonCode[];
  resolved_by: "edit" | "accept" | "delete" | null;
};

export type TransferOption = {
  branch_id: string;
  branch_name: string;
  available_qty: number;
  covers_shortfall: boolean;
};

export type StockInfo = {
  on_hand: number;
  shortfall: number;
  next_receipt: { date: string; qty: number } | null;
  transfer_options: TransferOption[];
};

export type MatchAssumption = {
  field: string;
  value: string;
  reason: string;
};

export type MatchInfo = {
  status: MatchStatus;
  confidence: Confidence | null;
  confidence_reasons: string[];
  assumptions: MatchAssumption[];
  is_substitute: boolean;
  substitute_reason: string | null;
  verify_notes: string[];
};

export type RequirementView = {
  field: string;
  value: string;
  reason: string;
  source: SourceRef;
};

export type BidLine = {
  line_id: string;
  item_id: string | null;
  description: string;
  source: SourceRef;
  requested_quantity_text: string | null;
  requested_unit_raw: string | null;
  sku: string | null;
  product_name: string | null;
  sale_qty: string | null;
  sale_unit: string | null;
  conversion_path: string | null;
  unit_price_cents: number | null;
  catalog_unit_price_cents: number | null;
  extended_cents: number | null;
  stock: StockInfo | null;
  match: MatchInfo | null;
  requirements: RequirementView[];
  review: ReviewState;
  origins: {
    quantity: Origin;
    unit_price: Origin;
    sku: Origin;
    description: Origin;
  };
  override_reason: string | null;
};

export type ExclusionLine = {
  description: string;
  source: SourceRef;
};

export type LaborLine = {
  description: string;
  quantity_text: string | null;
  unit_raw: string | null;
  source: SourceRef | null;
};

export type UnreadableLine = {
  description: string;
};

export type AssumptionView = {
  item_id: string;
  text: string;
};

export type ContractorQuestionView = {
  text: string;
  basis: {
    kind: "gap_rule" | "conflict" | "flagged_line";
    ref_id: string;
  };
};

export type BidTotals = {
  subtotal_cents: number;
  tax_rate_ppm: number;
  tax_cents: number;
  total_cents: number;
  priced_lines: number;
  open_flags: number;
};

export type BidKind = "agent" | "draft" | "final";

export type Bid = {
  revision: number;
  kind: BidKind;
  generated_by: "orchestrator" | "fallback";
  partial_reasons: string[];
  lines: BidLine[];
  exclusions: ExclusionLine[];
  labor: LaborLine[];
  unreadable: UnreadableLine[];
  conflicts: ConflictView[];
  assumptions: AssumptionView[];
  contractor_questions: ContractorQuestionView[];
  summary: string;
  totals: BidTotals;
};

// ─── Edit operations (SPEC §9.4) ──────────────────────────────────
//
// Discriminators + field names mirror `backend/app/contracts/edits.py`
// and `docs/API_CONTRACT.md` exactly — any drift is caught by
// `tests/editOpContract.test.ts` loading the same fixture the
// backend test parses.

export type SetQtyOp = {
  op: "set_qty";
  line_id: string;
  sale_qty: string;
};
export type SetUnitPriceOp = {
  op: "set_unit_price";
  line_id: string;
  unit_price_cents: number;
  // SPEC §9.4: override reason is REQUIRED and non-empty.
  override_reason: string;
};
export type SetSkuOp = {
  op: "set_sku";
  line_id: string;
  sku: string;
};
export type SetDescriptionOp = {
  op: "set_description";
  line_id: string;
  description: string;
};
export type AcceptLineOp = {
  op: "accept_line";
  line_id: string;
};
export type DeleteLineOp = {
  op: "delete_line";
  line_id: string;
};
export type AddLineOp = {
  op: "add_line";
  description: string;
  sale_qty: string;
  sale_unit: string;
  unit_price_cents: number;
};
export type ApplyProposalOp = {
  op: "apply_proposal";
  line_id: string;
  proposal_id: string;
};
export type DismissProposalOp = {
  op: "dismiss_proposal";
  line_id: string;
  proposal_id: string;
};

export type EditOp =
  | SetQtyOp
  | SetUnitPriceOp
  | SetSkuOp
  | SetDescriptionOp
  | AcceptLineOp
  | DeleteLineOp
  | AddLineOp
  | ApplyProposalOp
  | DismissProposalOp;

// ─── Reprice proposal (SPEC §9.5) ─────────────────────────────────

export type RepriceProposal = {
  proposal_id: string;
  line_id: string;
  before: {
    sku: string | null;
    product_name: string | null;
    sale_qty: string | null;
    sale_unit: string | null;
    unit_price_cents: number | null;
    extended_cents: number | null;
    confidence: Confidence | null;
  };
  after: {
    sku: string | null;
    product_name: string | null;
    sale_qty: string | null;
    sale_unit: string | null;
    unit_price_cents: number | null;
    extended_cents: number | null;
    confidence: Confidence | null;
  };
};

// ─── Bid diff (SPEC §9.7) ────────────────────────────────────────

export type BidDiff = {
  changed: number;
  added: number;
  deleted: number;
  accepted: number;
};
