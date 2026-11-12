// @vitest-environment node
/**
 * Fixture bid-apply engine (src/dev-fixtures/bid/index.ts). Pins
 * the op semantics used by the `useBidDraft` hook so changes to
 * the reducer-side test don't paper over broken op math.
 *
 *   - Each op recomputes `extended_cents` on affected lines + the
 *     bid totals.
 *   - Origins flip to `user_overridden` for fields the agent had,
 *     `user_filled` for new values on empty cells.
 *   - Review state resolves to `edit` / `accept` / `delete` on
 *     the right ops.
 *   - `buildMessyBid` is deterministic (same input → same totals).
 */
import { describe, expect, it } from "vitest";

import type { Bid, EditOp, RepriceProposal } from "@/api";
import { applyOps, buildMessyBid, fakeReprice, finalize } from "@/dev-fixtures/bid";

function seed(): Bid {
  return buildMessyBid("draft", 1, "BR-NJ-01");
}

describe("buildMessyBid", () => {
  it("is deterministic: same inputs produce identical totals", () => {
    const a = buildMessyBid("draft", 1, "BR-NJ-01");
    const b = buildMessyBid("draft", 1, "BR-NJ-01");
    expect(a.totals).toEqual(b.totals);
    expect(a.lines.length).toBe(b.lines.length);
  });

  it("has exactly 26 lines (matches rfp_messy fixture)", () => {
    const bid = seed();
    expect(bid.lines).toHaveLength(26);
  });

  it("tax = half-up(subtotal * tax_rate_ppm / 1_000_000)", () => {
    const bid = seed();
    const expected = Math.floor(
      (bid.totals.subtotal_cents * bid.totals.tax_rate_ppm + 500_000) / 1_000_000,
    );
    expect(bid.totals.tax_cents).toBe(expected);
  });
});

describe("applyOps — set_quantity", () => {
  it("recomputes extended_cents and bumps origin to user_overridden", () => {
    const bid = seed();
    const line = bid.lines.find((l) => l.sale_qty !== null && l.unit_price_cents !== null)!;
    const op: EditOp = { op: "set_quantity", line_id: line.line_id, sale_qty: "50" };
    const next = applyOps(bid, [op], new Map());
    const nextLine = next.lines.find((l) => l.line_id === line.line_id)!;
    expect(nextLine.sale_qty).toBe("50");
    expect(nextLine.extended_cents).toBe(50 * (line.unit_price_cents ?? 0));
    expect(nextLine.origins.quantity).toBe("user_overridden");
    expect(next.revision).toBe(bid.revision + 1);
  });

  it("recomputes the bid total", () => {
    const bid = seed();
    const line = bid.lines.find((l) => l.sale_qty !== null && l.unit_price_cents !== null)!;
    const oldExt = line.extended_cents ?? 0;
    const newQty = Number(line.sale_qty) + 1;
    const newExt = Math.round(newQty * (line.unit_price_cents ?? 0));
    const expectedSubtotal = bid.totals.subtotal_cents - oldExt + newExt;
    const next = applyOps(
      bid,
      [{ op: "set_quantity", line_id: line.line_id, sale_qty: String(newQty) }],
      new Map(),
    );
    expect(next.totals.subtotal_cents).toBe(expectedSubtotal);
  });
});

describe("applyOps — set_unit_price", () => {
  it("overrides the price, keeps catalog_unit_price_cents, flips origin", () => {
    const bid = seed();
    const line = bid.lines.find((l) => l.unit_price_cents !== null)!;
    const op: EditOp = {
      op: "set_unit_price",
      line_id: line.line_id,
      unit_price_cents: 9999,
      reason: "negotiated",
    };
    const next = applyOps(bid, [op], new Map());
    const nextLine = next.lines.find((l) => l.line_id === line.line_id)!;
    expect(nextLine.unit_price_cents).toBe(9999);
    expect(nextLine.catalog_unit_price_cents).toBe(line.unit_price_cents);
    expect(nextLine.override_reason).toBe("negotiated");
    expect(nextLine.origins.unit_price).toBe("user_overridden");
  });
});

describe("applyOps — accept_line", () => {
  it("resolves open review to resolved_by='accept'", () => {
    const bid = seed();
    const openLine = bid.lines.find((l) => l.review.state === "open")!;
    const next = applyOps(bid, [{ op: "accept_line", line_id: openLine.line_id }], new Map());
    const nextLine = next.lines.find((l) => l.line_id === openLine.line_id)!;
    expect(nextLine.review.state).toBe("resolved");
    expect(nextLine.review.resolved_by).toBe("accept");
  });
});

describe("applyOps — delete_line", () => {
  it("removes the line and recomputes totals", () => {
    const bid = seed();
    const line = bid.lines.find((l) => l.extended_cents !== null)!;
    const next = applyOps(bid, [{ op: "delete_line", line_id: line.line_id }], new Map());
    expect(next.lines.find((l) => l.line_id === line.line_id)).toBeUndefined();
    expect(next.totals.subtotal_cents).toBe(
      bid.totals.subtotal_cents - (line.extended_cents ?? 0),
    );
  });
});

describe("applyOps — add_line", () => {
  it("appends a user-added line with user_filled origins", () => {
    const bid = seed();
    const next = applyOps(
      bid,
      [
        {
          op: "add_line",
          description: "Scaffolding rental",
          sale_qty: "1",
          sale_unit: "WK",
          unit_price_cents: 50_000,
        },
      ],
      new Map(),
    );
    expect(next.lines).toHaveLength(bid.lines.length + 1);
    const added = next.lines[next.lines.length - 1]!;
    expect(added.description).toBe("Scaffolding rental");
    expect(added.origins.quantity).toBe("user_filled");
    expect(added.extended_cents).toBe(50_000);
  });
});

describe("applyOps — apply_reprice", () => {
  it("swaps in the proposal's `after` and marks origins back to agent", () => {
    const bid = seed();
    const line = bid.lines.find((l) => l.unit_price_cents !== null)!;
    const proposal: RepriceProposal = {
      proposal_id: "rep_1",
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
        sku: "NEW-SKU",
        product_name: "Reprice example",
        sale_qty: "5",
        sale_unit: "EA",
        unit_price_cents: 123,
        extended_cents: 615,
        confidence: "high",
      },
    };
    const store = new Map([[proposal.proposal_id, proposal]]);
    const next = applyOps(
      bid,
      [{ op: "apply_reprice", line_id: line.line_id, proposal_id: "rep_1" }],
      store,
    );
    const nextLine = next.lines.find((l) => l.line_id === line.line_id)!;
    expect(nextLine.sku).toBe("NEW-SKU");
    expect(nextLine.unit_price_cents).toBe(123);
    expect(nextLine.extended_cents).toBe(615);
    expect(nextLine.origins.unit_price).toBe("agent");
    expect(nextLine.origins.sku).toBe("agent");
  });
});

describe("fakeReprice + finalize", () => {
  it("fakeReprice returns a proposal with a ~5% nudged price", () => {
    const bid = seed();
    const line = bid.lines.find((l) => l.unit_price_cents !== null && l.unit_price_cents > 100)!;
    const proposal = fakeReprice(bid, line.line_id);
    expect(proposal).not.toBeNull();
    expect(proposal!.before.unit_price_cents).toBe(line.unit_price_cents);
    expect(proposal!.after.unit_price_cents).not.toBe(line.unit_price_cents);
  });

  it("finalize flips kind to 'final'", () => {
    const final = finalize(seed());
    expect(final.kind).toBe("final");
  });
});
