// @vitest-environment node
/**
 * The rfp_messy fixture SSE stream must stay in sync with
 * `samples/expected/rfp_messy.json` (the real analyst's golden
 * output) — otherwise the RunPage demo renders a different world
 * from what Phase 5's backend will produce against the same PDF.
 *
 * `source.verified` is only ever true when `read_via === "text"` —
 * this is a system rule (see `SourceRef` in `src/api/events.ts`),
 * not a fixture policy. Vision quotes are matched against the
 * analyst's own transcription, which the estimator still has to
 * accept before the line can be finalized.
 *
 * Checks:
 *   - item count, item ids, SKUs, source metadata (page, quote,
 *     quantity_text, unit_raw), page_routes all match the JSON.
 *   - every text quote contains its `quantity_text` (unless notes
 *     include `quantity_not_in_quote`, which is the SPEC §6.4
 *     escape hatch).
 *   - the open-review set derived from the reducer matches a
 *     hardcoded list per SPEC §9.3, and `bid.ready.open_flags` is
 *     the length of that list.
 *   - reducing the full stream yields `status: "completed"`,
 *     `outcome: "bid_ready"`, every lane ends in "done".
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  initialRunViewState,
  runReducer,
  type RunViewState,
} from "../../src/lib/runReducer";
import {
  PAGE_ROUTES,
  PRICED_LINES,
  RFP_MESSY_EVENTS,
} from "../../src/dev-fixtures/sse/rfp-messy";

type ExpectedItem = {
  line_no: number;
  description: string;
  quantity_text: string | null;
  unit_raw: string | null;
  source_page: number;
  quote: string;
  expected_sku: string | null;
};

type ExpectedJson = {
  pdf: string;
  page_count: number;
  page_routes: Record<string, string>;
  items: ExpectedItem[];
};

function loadExpected(): ExpectedJson {
  const p = fileURLToPath(
    new URL("../../../samples/expected/rfp_messy.json", import.meta.url),
  );
  return JSON.parse(readFileSync(p, "utf-8")) as ExpectedJson;
}

/**
 * Expected open-review items (SPEC §9.3 bullet 3). Hardcoded here
 * so the test catches both fixture drift AND reducer-derived
 * divergence. Reasons (one per line, deduped into a set):
 *   item_3   substitute (17ft → 18ft)
 *   item_4   substitute (SPF → SYP for PT)
 *   item_8   involved in conflict conf_1
 *   item_14  involved in conflict conf_2
 *   item_18  no_match (cedar shakes)
 *   item_20  shortfall > 0 (underlayment)
 *   item_21  unverified (vision page 7)
 *   item_22  unverified (vision page 7)
 *   item_23  unverified (vision page 7)
 *   item_24  involved in conflict conf_1
 *   item_25  not priced (skipped — no length)
 *   item_26  not priced (skipped) + quantity_not_in_quote note
 *
 * Medium-confidence lines (item_5, item_13, item_17, item_19) are
 * NOT open — SPEC §9.3 only names `confidence: "low"`.
 */
const EXPECTED_OPEN_IDS: readonly string[] = [
  "item_3",
  "item_4",
  "item_8",
  "item_14",
  "item_18",
  "item_20",
  "item_21",
  "item_22",
  "item_23",
  "item_24",
  "item_25",
  "item_26",
];

/** Derive the open-review item ids from a reducer end state per SPEC §9.3. */
function openIdsFromState(state: RunViewState): Set<string> {
  const open = new Set<string>();
  for (const item of Object.values(state.items)) {
    if (!item.verified) open.add(item.id);
    if (item.notes.includes("quantity_not_in_quote")) open.add(item.id);
    if (item.notes.includes("unknown_unit")) open.add(item.id);
    if (item.flags.length > 0) open.add(item.id);
    const p = item.priced;
    if (p) {
      if (
        p.status === "no_match" ||
        p.status === "failed" ||
        p.status === "timeout" ||
        p.status === "skipped"
      ) {
        open.add(item.id);
      }
      if (p.is_substitute) open.add(item.id);
      if ((p.shortfall ?? 0) > 0) open.add(item.id);
      if (p.confidence === "low") open.add(item.id);
    } else {
      // "not priced" is an open reason too; if an item has no
      // priced record AND the run is finished, it's still open.
      // We'll only mark these open at the terminal check.
    }
  }
  for (const c of state.conflicts) {
    if (c.side_a.item_id) open.add(c.side_a.item_id);
    if (c.side_b.item_id) open.add(c.side_b.item_id);
  }
  return open;
}

describe("rfp_messy SSE fixture stream", () => {
  const expected = loadExpected();
  const final = RFP_MESSY_EVENTS.reduce(
    (s, e) => runReducer(s, e),
    initialRunViewState,
  );

  it("item count matches samples/expected/rfp_messy.json", () => {
    expect(PRICED_LINES).toHaveLength(expected.items.length);
    expect(expected.items).toHaveLength(26);
  });

  it("every item id maps to the JSON's expected_sku (or null)", () => {
    PRICED_LINES.forEach((line, i) => {
      const expectedItem = expected.items[i]!;
      expect(line.item_id).toBe(`item_${expectedItem.line_no}`);
      expect(line.sku).toBe(expectedItem.expected_sku);
    });
  });

  it("23 priced with SKU, 1 no_match, 2 skipped (total = 26)", () => {
    const skuCount = PRICED_LINES.filter((l) => l.sku !== null).length;
    const noMatch = PRICED_LINES.filter((l) => l.status === "no_match").length;
    const skipped = PRICED_LINES.filter((l) => l.status === "skipped").length;
    expect(skuCount).toBe(23);
    expect(noMatch).toBe(1);
    expect(skipped).toBe(2);
    expect(skuCount + noMatch + skipped).toBe(26);
  });

  it("fixture page_routes exactly equal the JSON page_routes map", () => {
    const built: Record<string, string> = {};
    PAGE_ROUTES.forEach((route, i) => {
      built[String(i + 1)] = route;
    });
    expect(built).toEqual(expected.page_routes);
  });

  it("every item.extracted matches the JSON item's source metadata", () => {
    const extracts = RFP_MESSY_EVENTS.filter((e) => e.type === "item.extracted");
    expect(extracts).toHaveLength(expected.items.length);
    extracts.forEach((ev, i) => {
      if (ev.type !== "item.extracted") return; // narrowing
      const item = ev.payload.item;
      const want = expected.items[i]!;
      expect(item.id).toBe(`item_${want.line_no}`);
      expect(item.quantity_text).toBe(want.quantity_text);
      expect(item.unit_raw).toBe(want.unit_raw);
      expect(item.source.page).toBe(want.source_page);
      // Quote may be extended on our side (e.g. item_5 carries the
      // wrapped "12 LF" prefix); we require the JSON's quote to be a
      // substring, which captures the "same source span" invariant
      // without pinning exact whitespace.
      expect(item.source.quote).toContain(want.quote);
    });
  });

  it("every TEXT-quoted item's quote contains its quantity_text (SPEC §6.4) unless note says otherwise", () => {
    for (const ev of RFP_MESSY_EVENTS) {
      if (ev.type !== "item.extracted") continue;
      const { source, quantity_text, notes, id } = ev.payload.item;
      if (source.read_via !== "text") continue;
      if (quantity_text === null) continue;
      if (notes.includes("quantity_not_in_quote")) continue;
      expect(
        source.quote ?? "",
        `${id}: quote should include the quantity_text, or notes should flag quantity_not_in_quote`,
      ).toContain(quantity_text);
    }
  });

  it("vision-page items (page 7) are unverified (system rule, not fixture-local)", () => {
    const visionItems = RFP_MESSY_EVENTS.filter(
      (ev) => ev.type === "item.extracted" && ev.payload.item.source.page === 7,
    );
    expect(visionItems.length).toBeGreaterThan(0);
    for (const ev of visionItems) {
      if (ev.type !== "item.extracted") continue;
      expect(ev.payload.item.source.read_via).toBe("vision");
      expect(ev.payload.item.source.verified).toBe(false);
    }
  });

  it("every source with verified=true has read_via='text' (SourceRef invariant)", () => {
    for (const ev of RFP_MESSY_EVENTS) {
      if (ev.type !== "item.extracted") continue;
      const { source } = ev.payload.item;
      if (source.verified) expect(source.read_via).toBe("text");
    }
  });

  it("reducer-derived open set === the hardcoded expected set, AND bid.ready.open_flags === its length", () => {
    const derived = openIdsFromState(final);
    expect([...derived].sort()).toEqual([...EXPECTED_OPEN_IDS].sort());
    expect(final.bidReady?.open_flags).toBe(EXPECTED_OPEN_IDS.length);
  });

  it("reducing the full stream yields status=completed, outcome=bid_ready, all lanes done", () => {
    expect(final.status).toBe("completed");
    expect(final.outcome).toBe("bid_ready");
    expect(final.agents.orchestrator.state).toBe("done");
    expect(final.agents.document_analyst.state).toBe("done");
    expect(final.agents.pricing_agent.state).toBe("done");
    expect(final.itemOrder).toHaveLength(26);
    expect(final.conflicts).toHaveLength(2);
    expect(final.flags.some((f) => f.item_id === "item_26")).toBe(true);
  });
});
