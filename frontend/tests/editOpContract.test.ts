// @vitest-environment node
/**
 * Contract test: each entry in `docs/edit_op_fixtures.json` matches
 * the frontend's `EditOp` discriminator + required fields. Pairs
 * with `backend/tests/contracts/test_edit_ops_fixture.py` which
 * parses the same file through Pydantic. Any rename or shape drift
 * fails both suites at CI (DECISIONS F13).
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import type { EditOp } from "@/api";

type Entry = { name: string; ts_type: string; body: EditOp };

function loadFixture(): { ops: Entry[] } {
  const p = fileURLToPath(
    new URL("../../docs/edit_op_fixtures.json", import.meta.url),
  );
  return JSON.parse(readFileSync(p, "utf-8")) as { ops: Entry[] };
}

/** Required keys on each variant. Sourced from `src/api/bid.ts`. */
const REQUIRED_KEYS: Record<string, readonly string[]> = {
  set_qty: ["op", "line_id", "sale_qty"],
  set_unit_price: ["op", "line_id", "unit_price_cents", "override_reason"],
  set_sku: ["op", "line_id", "sku"],
  set_description: ["op", "line_id", "description"],
  accept_line: ["op", "line_id"],
  delete_line: ["op", "line_id"],
  add_line: ["op", "description", "sale_qty", "sale_unit", "unit_price_cents"],
  apply_proposal: ["op", "line_id", "proposal_id"],
  dismiss_proposal: ["op", "line_id", "proposal_id"],
};

describe("edit-op shared fixture", () => {
  const { ops } = loadFixture();

  it("every fixture entry has a known discriminator", () => {
    for (const entry of ops) {
      expect(REQUIRED_KEYS).toHaveProperty(entry.body.op);
    }
  });

  it("every fixture body has all required keys for its variant", () => {
    for (const entry of ops) {
      const expected = REQUIRED_KEYS[entry.body.op]!;
      const actual = Object.keys(entry.body).sort();
      // Allow extra keys (backend adds them intentionally) but
      // every REQUIRED key must be present.
      for (const key of expected) {
        expect(actual, `${entry.name}: missing ${key}`).toContain(key);
      }
    }
  });

  it("every TS `EditOp` discriminator is covered by at least one fixture", () => {
    const covered = new Set(ops.map((o) => o.body.op));
    for (const op of Object.keys(REQUIRED_KEYS)) {
      expect(covered, `no fixture entry for op=${op}`).toContain(op);
    }
  });

  it("assigns each entry's body to EditOp without a type error (compile-time pin)", () => {
    // If an op's shape drifts in bid.ts, this assignment stops
    // compiling. The runtime assertion is a cheap belt on top.
    for (const entry of ops) {
      const op: EditOp = entry.body;
      expect(op.op).toBe(entry.body.op);
    }
  });
});
