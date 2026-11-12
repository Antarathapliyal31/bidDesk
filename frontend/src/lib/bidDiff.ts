/**
 * Line-level diff between the agent's original bid (`agent_bid`,
 * SPEC §9.1 `kind: "agent", revision: 0`) and the current draft
 * or final. Matches lines by `line_id`:
 *   - Draft line WITH a same-id agent line and ANY of the four
 *     editable fields differs → counts as "changed".
 *   - Draft line with NO matching agent line → "added".
 *   - Agent line with NO matching draft line → "deleted".
 *   - A draft line with `review.resolved_by === "accept"` →
 *     "accepted" (even if fields differ, SPEC §9.7).
 *
 * All functions are pure and can be called on every render.
 */
import type { Bid, BidDiff, BidLine } from "@/api";

export type DiffField = "description" | "sale_qty" | "sku" | "unit_price_cents";

export type FieldChange = {
  field: DiffField;
  from: string | null;
  to: string | null;
};

const EDITABLE_FIELDS: readonly DiffField[] = [
  "description",
  "sale_qty",
  "sku",
  "unit_price_cents",
];

function readField(line: BidLine, field: DiffField): string | null {
  switch (field) {
    case "description":
      return line.description;
    case "sale_qty":
      return line.sale_qty;
    case "sku":
      return line.sku;
    case "unit_price_cents":
      return line.unit_price_cents === null ? null : String(line.unit_price_cents);
  }
}

function normalize(v: string | null): string {
  return v ?? "";
}

/**
 * Per-field changes between a draft line and its agent
 * counterpart. Pass the already-resolved agent line (null = this
 * is a user-added line, no original to diff against).
 */
export function changedFields(
  draftLine: BidLine,
  agentLine: BidLine | null,
): FieldChange[] {
  if (!agentLine) return [];
  const out: FieldChange[] = [];
  for (const field of EDITABLE_FIELDS) {
    const to = readField(draftLine, field);
    const from = readField(agentLine, field);
    if (normalize(from) !== normalize(to)) {
      out.push({ field, from, to });
    }
  }
  return out;
}

/** Thin wrapper for the single-field render-site lookup. */
export function fieldChanged(
  draftLine: BidLine,
  agentLine: BidLine | null,
  field: DiffField,
): FieldChange | null {
  return changedFields(draftLine, agentLine).find((c) => c.field === field) ?? null;
}

/** Find the agent-origin line matching a draft line by id. */
export function findAgentLine(agentBid: Bid | null, lineId: string): BidLine | null {
  if (!agentBid) return null;
  return agentBid.lines.find((l) => l.line_id === lineId) ?? null;
}

/**
 * Line-count diff (SPEC §9.7). Compares by `line_id`:
 *   - agentOnly  → deleted
 *   - draftOnly  → added
 *   - both, resolved_by="accept" → accepted (never changed, even
 *                                  if fields differ)
 *   - both, any field differs    → changed
 */
export function computeBidDiff(bid: Bid, agentBid: Bid | null): BidDiff {
  if (!agentBid) return { changed: 0, added: 0, deleted: 0, accepted: 0 };
  const agentIds = new Set(agentBid.lines.map((l) => l.line_id));
  const draftIds = new Set(bid.lines.map((l) => l.line_id));
  let changed = 0;
  let accepted = 0;
  let added = 0;
  let deleted = 0;
  for (const line of bid.lines) {
    if (!agentIds.has(line.line_id)) {
      added += 1;
      continue;
    }
    if (line.review.resolved_by === "accept") {
      accepted += 1;
      continue;
    }
    const agentLine = agentBid.lines.find((l) => l.line_id === line.line_id) ?? null;
    if (changedFields(line, agentLine).length > 0) changed += 1;
  }
  for (const agentLine of agentBid.lines) {
    if (!draftIds.has(agentLine.line_id)) deleted += 1;
  }
  return { changed, added, deleted, accepted };
}
