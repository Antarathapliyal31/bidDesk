/**
 * Fixture bid-backend. Replaces (for this branch only):
 *   - GET /api/runs/:id/bid       → `getFixtureBid(runId)`
 *   - PATCH /api/runs/:id/bid     → `applyOps(bid, ops)`
 *   - POST   .../reprice/:line_id → `fakeReprice(bid, line_id)`
 *   - POST   .../finalize         → `finalize(bid)`
 *
 * Phase 5 merge: this directory + `messyBid.ts` are deleted; the
 * `useBid*` hooks swap their `queryFn` to real `fetch` calls.
 * Shapes match `src/api/bid.ts` verbatim so call sites don't move.
 */
import type {
  AcceptLineOp,
  AddLineOp,
  ApplyRepriceOp,
  Bid,
  BidLine,
  BidTotals,
  DeleteLineOp,
  EditOp,
  Origin,
  RepriceProposal,
  SetDescriptionOp,
  SetQuantityOp,
  SetSkuOp,
  SetUnitPriceOp,
} from "@/api";

import { FIXTURE_RUNS } from "../runs";

import { buildCleanBid } from "./cleanBid";
import { buildMessyBid } from "./messyBid";
import { buildPasteBid } from "./pasteBid";

const BRANCH_TAX_PPM: Readonly<Record<string, number>> = {
  "BR-NJ-01": 66_250,
  "BR-NY-01": 86_250,
  "BR-PA-01": 60_000,
};

/**
 * Envelope matching SPEC §13 `GET /api/runs/:id`: the authoritative
 * current `Bid` plus the immutable `agent_bid` (revision 0) the UI
 * diffs against (SPEC §9.7). The two share a shape — different
 * `kind` / `revision` + the agent_bid never receives edits.
 */
export type FixtureBidEnvelope = {
  bid: Bid;
  agent_bid: Bid;
};

/**
 * Keyed by runId. The fixture Bid shape depends on the run's
 * `input_name` (so rfp_clean runs open their own clean bid,
 * rfp_messy opens the messy bid, paste runs get the paste bid).
 * Runs that never had a bid (running / failed / no_materials)
 * return null.
 */
export function getFixtureBid(runId: string): FixtureBidEnvelope | null {
  const run = FIXTURE_RUNS.find((r) => r.id === runId);
  if (run) {
    if (run.status === "failed" || run.status === "running") return null;
    if (run.outcome === "no_materials") return null;
    const branch = branchIdFromName(run.branch_name);
    const kind = run.finalized_at ? "final" : "draft";
    const bid = buildFor(run.input_name, kind, branch);
    const agent_bid = buildFor(run.input_name, "agent", branch);
    return { bid, agent_bid };
  }
  // Canonical rfp_messy stream + any fallback submit.
  if (runId.startsWith("run_demo_")) {
    return {
      bid: buildMessyBid("draft", 1, "BR-NJ-01"),
      agent_bid: buildMessyBid("agent", 0, "BR-NJ-01"),
    };
  }
  return null;
}

function buildFor(
  inputName: string,
  kind: "agent" | "draft" | "final",
  branch: string,
): Bid {
  const revision = kind === "agent" ? 0 : 1;
  const lower = inputName.toLowerCase();
  if (lower === "rfp_clean.pdf") return buildCleanBid(kind, revision, branch);
  if (lower === "rfp_scanned.pdf") return buildCleanBid(kind, revision, branch);
  if (lower.endsWith(".txt") || lower === "pasted list") {
    return buildPasteBid(kind, revision, branch);
  }
  // Default: messy. Covers rfp_messy.pdf + the corrupt.pdf fallback
  // (though failed runs are gated out before we get here).
  return buildMessyBid(kind, revision, branch);
}

export function branchIdFromName(branchName: string): string {
  if (branchName.includes("North Jersey")) return "BR-NJ-01";
  if (branchName.includes("Long Island")) return "BR-NY-01";
  if (branchName.includes("Lehigh")) return "BR-PA-01";
  return "BR-NJ-01";
}

// ─── Totals + helpers ─────────────────────────────────────────────

function recomputeExtended(line: BidLine): BidLine {
  if (line.sale_qty === null || line.unit_price_cents === null) {
    return { ...line, extended_cents: null };
  }
  const qty = Number(line.sale_qty);
  if (!Number.isFinite(qty)) return line;
  return { ...line, extended_cents: Math.round(qty * line.unit_price_cents) };
}

function recomputeTotals(bid: Bid): BidTotals {
  const subtotal = bid.lines.reduce((s, l) => s + (l.extended_cents ?? 0), 0);
  const tax = Math.floor((subtotal * bid.totals.tax_rate_ppm + 500_000) / 1_000_000);
  const priced = bid.lines.filter(
    (l) => l.match?.status === "matched" || l.match?.status === "substitute",
  ).length;
  const openFlags = bid.lines.filter((l) => l.review.state === "open").length;
  return {
    subtotal_cents: subtotal,
    tax_rate_ppm: bid.totals.tax_rate_ppm,
    tax_cents: tax,
    total_cents: subtotal + tax,
    priced_lines: priced,
    open_flags: openFlags,
  };
}

function replaceLine(bid: Bid, line_id: string, update: (l: BidLine) => BidLine): Bid {
  const nextLines = bid.lines.map((l) => (l.line_id === line_id ? update(l) : l));
  return { ...bid, lines: nextLines };
}

function resolveReview(line: BidLine, by: "edit" | "accept" | "delete"): BidLine {
  if (line.review.state !== "open") return line;
  return { ...line, review: { ...line.review, state: "resolved", resolved_by: by } };
}

function bumpOrigin(current: Origin, hadValue: boolean): Origin {
  // Agent filled in or nothing → user_filled; agent had a real
  // value → user_overridden.
  if (current === "user_overridden") return "user_overridden";
  if (hadValue) return "user_overridden";
  return "user_filled";
}

// ─── Op handlers ──────────────────────────────────────────────────

function applySetQuantity(bid: Bid, op: SetQuantityOp): Bid {
  return replaceLine(bid, op.line_id, (l) => {
    const next: BidLine = {
      ...l,
      sale_qty: op.sale_qty,
      origins: {
        ...l.origins,
        quantity: bumpOrigin(l.origins.quantity, l.sale_qty !== null),
      },
    };
    return resolveReview(recomputeExtended(next), "edit");
  });
}

function applySetUnitPrice(bid: Bid, op: SetUnitPriceOp): Bid {
  return replaceLine(bid, op.line_id, (l) => {
    const next: BidLine = {
      ...l,
      unit_price_cents: op.unit_price_cents,
      override_reason: op.reason,
      origins: {
        ...l.origins,
        unit_price: bumpOrigin(l.origins.unit_price, l.unit_price_cents !== null),
      },
    };
    return resolveReview(recomputeExtended(next), "edit");
  });
}

function applySetSku(bid: Bid, op: SetSkuOp): Bid {
  return replaceLine(bid, op.line_id, (l) => {
    const next: BidLine = {
      ...l,
      sku: op.sku,
      origins: {
        ...l.origins,
        sku: bumpOrigin(l.origins.sku, l.sku !== null),
      },
    };
    return resolveReview(next, "edit");
  });
}

function applySetDescription(bid: Bid, op: SetDescriptionOp): Bid {
  return replaceLine(bid, op.line_id, (l) => {
    const next: BidLine = {
      ...l,
      description: op.text,
      origins: {
        ...l.origins,
        description: "user_overridden",
      },
    };
    return resolveReview(next, "edit");
  });
}

function applyAccept(bid: Bid, op: AcceptLineOp): Bid {
  return replaceLine(bid, op.line_id, (l) => resolveReview(l, "accept"));
}

function applyDelete(bid: Bid, op: DeleteLineOp): Bid {
  return { ...bid, lines: bid.lines.filter((l) => l.line_id !== op.line_id) };
}

function applyAddLine(bid: Bid, op: AddLineOp): Bid {
  const existingIds = bid.lines.map((l) => l.line_id);
  let n = existingIds.length + 1;
  let line_id = `line_${String(n).padStart(2, "0")}`;
  while (existingIds.includes(line_id)) {
    n += 1;
    line_id = `line_${String(n).padStart(2, "0")}`;
  }
  const extended = Math.round(Number(op.sale_qty) * op.unit_price_cents);
  const added: BidLine = {
    line_id,
    item_id: null,
    description: op.description,
    source: { page: null, quote: null, read_via: null, verified: false },
    requested_quantity_text: op.sale_qty,
    requested_unit_raw: op.sale_unit,
    sku: null,
    product_name: null,
    sale_qty: op.sale_qty,
    sale_unit: op.sale_unit,
    conversion_path: null,
    unit_price_cents: op.unit_price_cents,
    catalog_unit_price_cents: null,
    extended_cents: extended,
    stock: null,
    match: null,
    requirements: [],
    review: { state: "none", reasons: [], resolved_by: null },
    origins: {
      quantity: "user_filled",
      unit_price: "user_filled",
      sku: "user_filled",
      description: "user_filled",
    },
    override_reason: null,
  };
  return { ...bid, lines: [...bid.lines, added] };
}

function applyApplyReprice(bid: Bid, op: ApplyRepriceOp, proposal?: RepriceProposal): Bid {
  if (!proposal || proposal.line_id !== op.line_id) return bid;
  return replaceLine(bid, op.line_id, (l) => {
    const next: BidLine = {
      ...l,
      sku: proposal.after.sku,
      product_name: proposal.after.product_name,
      sale_qty: proposal.after.sale_qty,
      sale_unit: proposal.after.sale_unit,
      unit_price_cents: proposal.after.unit_price_cents,
      extended_cents: proposal.after.extended_cents,
      match: l.match
        ? { ...l.match, confidence: proposal.after.confidence }
        : l.match,
      origins: {
        ...l.origins,
        unit_price: "agent",
        sku: "agent",
        quantity: "agent",
      },
      override_reason: null,
    };
    return resolveReview(next, "edit");
  });
}

/**
 * Apply a list of ops in order, recomputing totals + bumping
 * revision. Pure: no mutation of the input bid. Totals are the
 * server's (we're the server in fixture mode).
 */
export function applyOps(
  bid: Bid,
  ops: EditOp[],
  repriceStore: Map<string, RepriceProposal>,
): Bid {
  const next = ops.reduce<Bid>((acc, op) => {
    switch (op.op) {
      case "set_quantity":
        return applySetQuantity(acc, op);
      case "set_unit_price":
        return applySetUnitPrice(acc, op);
      case "set_sku":
        return applySetSku(acc, op);
      case "set_description":
        return applySetDescription(acc, op);
      case "accept_line":
        return applyAccept(acc, op);
      case "delete_line":
        return applyDelete(acc, op);
      case "add_line":
        return applyAddLine(acc, op);
      case "apply_reprice":
        return applyApplyReprice(acc, op, repriceStore.get(op.proposal_id));
      default: {
        const _exhaustive: never = op;
        return _exhaustive;
      }
    }
  }, bid);
  return {
    ...next,
    revision: bid.revision + 1,
    totals: recomputeTotals(next),
  };
}

/**
 * Build a plausible reprice proposal for a line. In reality the
 * backend runs the pricing agent again; in fixture mode we nudge
 * the price up/down by ~5% to show a diff.
 */
export function fakeReprice(bid: Bid, line_id: string): RepriceProposal | null {
  const line = bid.lines.find((l) => l.line_id === line_id);
  if (!line || line.unit_price_cents === null) return null;
  const nudged = Math.round(line.unit_price_cents * 1.05);
  const qty = line.sale_qty ? Number(line.sale_qty) : 0;
  return {
    proposal_id: `rep_${line.line_id}_${Date.now().toString(36)}`,
    line_id: line.line_id,
    before: {
      sku: line.sku,
      product_name: line.product_name,
      sale_qty: line.sale_qty,
      sale_unit: line.sale_unit,
      unit_price_cents: line.unit_price_cents,
      extended_cents: line.extended_cents,
      confidence: line.match?.confidence ?? null,
    },
    after: {
      sku: line.sku,
      product_name: line.product_name,
      sale_qty: line.sale_qty,
      sale_unit: line.sale_unit,
      unit_price_cents: nudged,
      extended_cents: Math.round(qty * nudged),
      confidence: line.match?.confidence ?? null,
    },
  };
}

export function finalize(bid: Bid): Bid {
  return { ...bid, kind: "final" };
}

export { buildCleanBid } from "./cleanBid";
export { buildMessyBid } from "./messyBid";
export { buildPasteBid } from "./pasteBid";
export { BRANCH_TAX_PPM };
