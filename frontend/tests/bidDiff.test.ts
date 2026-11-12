// @vitest-environment node
/**
 * Pure bidDiff helpers (SPEC §9.7). The reducer stores
 * `serverDraft`, `localDraft`, and `agentBid` separately; this
 * suite covers the diff math against the agent snapshot.
 */
import { describe, expect, it } from "vitest";

import type { Bid, BidLine } from "@/api";
import {
  changedFields,
  computeBidDiff,
  fieldChanged,
  findAgentLine,
} from "@/lib/bidDiff";

function emptyBid(lines: BidLine[] = []): Bid {
  return {
    revision: 1,
    kind: "draft",
    generated_by: "orchestrator",
    partial_reasons: [],
    lines,
    exclusions: [],
    labor: [],
    unreadable: [],
    conflicts: [],
    assumptions: [],
    contractor_questions: [],
    summary: "",
    totals: {
      subtotal_cents: 0,
      tax_rate_ppm: 66_250,
      tax_cents: 0,
      total_cents: 0,
      priced_lines: 0,
      open_flags: 0,
    },
  };
}

function baseLine(id: string): BidLine {
  return {
    line_id: id,
    item_id: id,
    description: "Original",
    source: { page: 1, quote: null, read_via: "text", verified: true },
    requested_quantity_text: "10",
    requested_unit_raw: "EA",
    sku: "ORIG-SKU",
    product_name: null,
    sale_qty: "10",
    sale_unit: "EA",
    conversion_path: null,
    unit_price_cents: 500,
    catalog_unit_price_cents: 500,
    extended_cents: 5_000,
    stock: null,
    match: null,
    requirements: [],
    review: { state: "none", reasons: [], resolved_by: null },
    origins: { quantity: "agent", unit_price: "agent", sku: "agent", description: "agent" },
    override_reason: null,
  };
}

describe("changedFields / fieldChanged", () => {
  it("returns empty when draft line matches the agent line", () => {
    const line = baseLine("l1");
    expect(changedFields(line, line)).toHaveLength(0);
  });

  it("flags quantity + unit_price edits against the agent line", () => {
    const agent = baseLine("l1");
    const draft: BidLine = {
      ...agent,
      sale_qty: "15",
      unit_price_cents: 700,
    };
    const fields = changedFields(draft, agent).map((c) => c.field);
    expect(fields).toContain("sale_qty");
    expect(fields).toContain("unit_price_cents");
    const qty = fieldChanged(draft, agent, "sale_qty");
    expect(qty?.from).toBe("10");
    expect(qty?.to).toBe("15");
  });

  it("agentLine=null (user-added line) → no diff fields", () => {
    const draft = baseLine("l1");
    expect(changedFields(draft, null)).toHaveLength(0);
    expect(fieldChanged(draft, null, "sale_qty")).toBeNull();
  });
});

describe("findAgentLine", () => {
  it("returns the line with the matching line_id", () => {
    const agentBid = emptyBid([baseLine("l1"), baseLine("l2")]);
    expect(findAgentLine(agentBid, "l2")?.line_id).toBe("l2");
  });

  it("returns null when agentBid is null or line_id is absent", () => {
    expect(findAgentLine(null, "l1")).toBeNull();
    expect(findAgentLine(emptyBid([baseLine("l1")]), "l2")).toBeNull();
  });
});

describe("computeBidDiff", () => {
  it("empty agentBid → all zeros", () => {
    const bid = emptyBid([baseLine("l1")]);
    expect(computeBidDiff(bid, null)).toEqual({
      changed: 0,
      added: 0,
      deleted: 0,
      accepted: 0,
    });
  });

  it("counts changed, added, deleted, accepted against agent snapshot", () => {
    const agentBid = emptyBid([
      baseLine("l1"),
      baseLine("l2"),
      baseLine("l3"),
      baseLine("l4-deleted"),
    ]);
    const draft = emptyBid([
      baseLine("l1"), // unchanged
      { ...baseLine("l2"), sale_qty: "50" }, // changed
      {
        ...baseLine("l3"),
        review: { state: "resolved", reasons: [], resolved_by: "accept" },
      }, // accepted
      baseLine("l5"), // added (no agent counterpart)
      // l4-deleted is absent from draft
    ]);
    const diff = computeBidDiff(draft, agentBid);
    expect(diff.changed).toBe(1);
    expect(diff.added).toBe(1);
    expect(diff.deleted).toBe(1);
    expect(diff.accepted).toBe(1);
  });

  it("accepted line that was also edited counts under accepted only (SPEC §9.7)", () => {
    const agentBid = emptyBid([baseLine("l1")]);
    const draft = emptyBid([
      {
        ...baseLine("l1"),
        sale_qty: "42",
        review: { state: "resolved", reasons: [], resolved_by: "accept" },
      },
    ]);
    const diff = computeBidDiff(draft, agentBid);
    expect(diff.accepted).toBe(1);
    expect(diff.changed).toBe(0);
  });
});
