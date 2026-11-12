// @vitest-environment node
/**
 * Fixture quotes must be verbatim from the PDF so the SourcePanel's
 * callout + `customTextRenderer` highlight both work (R2 + B2 from
 * the manual pass). A reworded quote (e.g. "12 LF — 2x6 PT...") has
 * no substring overlap with the on-page text, so the <mark>
 * rendering misses entirely.
 *
 * This test guards against drift by holding the fixture quotes up
 * against the PDF text as published in `samples/make_samples.py`.
 * If someone edits either side without the other, the mismatch
 * fails here with a clear diff.
 */
import { describe, expect, it } from "vitest";

import { RFP_MESSY_EVENTS } from "../src/dev-fixtures/sse/rfp-messy";

// ─── Ground truth: the exact page text drawn by
// `samples/make_samples.py`. Kept in sync by hand until Phase 5
// where the backend's PDF extractor becomes the single source.
//
// Items 3 and 9 normalize double-spaces in the raw PDF to single
// spaces (both forms highlight identically via normalizeQuote's
// whitespace collapse, and this is what the samples/expected JSON
// also records). Item 5 wraps across two PDF lines; its verbatim
// quote is the concatenation the analyst produces.

const PDF_TEXT_BY_ITEM_ID: Readonly<Record<string, string>> = {
  item_1: "1. (100) 2x4 x 8' SPF #2",
  item_2: "2. (40) 2x6 x 16' pressure-treated",
  item_3: "3. Qty: 20 — 2x10 x 17' SPF",
  item_4: "4. twenty (20) 4x4 x 8' SPF #2 (ground contact)",
  item_5: "5. 2x6 PT for sill plates on the concrete slab ................... 12 LF",
  item_6: "6. 2x8 x 12' SPF .................................. 20 pcs",
  item_7: "7. 2x12 x 14' SPF #2 .............................. 10 pcs",
  item_8: "8. 7/16 in OSB for roof sheathing ................. 960 sq ft",
  item_9: "9. 1/2 in CDX plywood for exterior wall sheathing 320 sq ft",
  item_10: "10. 1/2 in drywall for upstairs bathrooms ......... 800 sq ft",
  item_11: "11. 1/2 in drywall for bedrooms ................... 1200 sq ft",
  item_12: "12. 5/8 in Type X drywall at garage ceiling ....... 400 sq ft",
  item_13: "13. 5/8 in drywall for ceilings ................... 1200 sq ft",
  item_14: "14. R-19 insulation ............................... 1200 sq ft",
  item_15: "15. R-30 insulation for attic ..................... 800 sq ft",
  item_16: "16. R-13 insulation for 2x4 walls ................. 400 sq ft",
  item_17: "17. Fascia (1x6 trim) ............................. 12 yards",
  item_18: "18. Cedar shakes .................................. 10 SQ",
  item_19: "19. Asphalt shingles .............................. 15 SQ",
  item_20: "20. Underlayment roll ............................. 4 rolls",
  // items 21-23 are on the vision page — they don't have verbatim
  // text to match against (the analyst sees the pixels, not the
  // text layer). Test skips them.
  item_24: "21. 1/2 in OSB for roof sheathing ................. 640 sq ft",
  item_25: "22. Deck ledger at south wall (2x10) .............. 1 ea",
  item_26: "23. Roof rafters: 2x8 at 16 in OC",
};

describe("rfp_messy fixture quotes are verbatim to the PDF", () => {
  const itemEvents = RFP_MESSY_EVENTS.filter(
    (e): e is Extract<typeof e, { type: "item.extracted" }> => e.type === "item.extracted",
  );

  for (const [itemId, pdfText] of Object.entries(PDF_TEXT_BY_ITEM_ID)) {
    it(`${itemId} quote matches the PDF line verbatim`, () => {
      const event = itemEvents.find((e) => e.payload.item.id === itemId);
      expect(event).toBeDefined();
      expect(event!.payload.item.source.quote).toBe(pdfText);
    });
  }

  it("vision items (21-23) carry read_via='vision' and skip the verbatim check", () => {
    const visionIds = ["item_21", "item_22", "item_23"];
    for (const id of visionIds) {
      const event = itemEvents.find((e) => e.payload.item.id === id);
      expect(event, `event for ${id} exists`).toBeDefined();
      expect(event!.payload.item.source.read_via).toBe("vision");
    }
  });
});
