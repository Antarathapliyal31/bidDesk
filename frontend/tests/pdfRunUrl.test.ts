// @vitest-environment node
/**
 * Fixture-mode run → PDF URL mapping. The rules:
 *   - exact run_demo_messy → /samples/rfp_messy.pdf
 *   - any run_demo_* with a .pdf input name → fallback to messy
 *   - .pdf input + non-demo run id → null (no fixture PDF)
 *   - paste runs / null input → null
 *   - non-PDF input names (even for demo runs) → null
 */
import { describe, expect, it } from "vitest";

import { pdfUrlForRun } from "@/features/run/source/pdfRunUrl";

describe("pdfUrlForRun", () => {
  it("exact run_demo_messy maps to the messy sample", () => {
    expect(pdfUrlForRun("run_demo_messy", "rfp_messy.pdf")).toBe("/samples/rfp_messy.pdf");
  });

  it("other run_demo_* ids fall back to the messy sample (prefix fallback)", () => {
    expect(pdfUrlForRun("run_demo_a1", "rfp_clean.pdf")).toBe("/samples/rfp_messy.pdf");
    expect(pdfUrlForRun("run_demo_scanned", "rfp_scanned.pdf")).toBe("/samples/rfp_messy.pdf");
  });

  it("returns null for paste runs (no input name)", () => {
    expect(pdfUrlForRun("run_demo_a1", null)).toBeNull();
  });

  it("returns null for non-PDF inputs even on a demo run", () => {
    expect(pdfUrlForRun("run_demo_a1", "paste.txt")).toBeNull();
  });

  it("returns null for non-demo runs (no fixture to serve)", () => {
    expect(pdfUrlForRun("run_prod_1234", "some.pdf")).toBeNull();
  });
});
