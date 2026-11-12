// @vitest-environment node
/**
 * Fixture-mode run → PDF URL mapping. The rules:
 *   - input_name = rfp_clean.pdf    → /samples/rfp_clean.pdf
 *   - input_name = rfp_messy.pdf    → /samples/rfp_messy.pdf
 *   - input_name = rfp_scanned.pdf  → /samples/rfp_scanned.pdf
 *   - any other .pdf name (incl. corrupt.pdf) → null
 *   - non-PDF input names → null
 *   - null input / paste runs → null
 *   - non-demo run id → null
 *
 * The map is keyed by input filename, NOT by run_demo_* prefix —
 * a history run submitted with rfp_clean.pdf must open its own
 * file, not whichever sample the fallback stream reuses.
 */
import { describe, expect, it } from "vitest";

import { pdfUrlForRun } from "@/features/run/source/pdfRunUrl";

describe("pdfUrlForRun", () => {
  it("rfp_messy.pdf → messy sample", () => {
    expect(pdfUrlForRun("run_demo_messy", "rfp_messy.pdf")).toBe("/samples/rfp_messy.pdf");
    // Fixture row ids too.
    expect(pdfUrlForRun("run_demo_a1", "rfp_messy.pdf")).toBe("/samples/rfp_messy.pdf");
    expect(pdfUrlForRun("run_demo_c3", "rfp_messy.pdf")).toBe("/samples/rfp_messy.pdf");
  });

  it("rfp_clean.pdf → clean sample (NOT fallback to messy)", () => {
    expect(pdfUrlForRun("run_demo_b2", "rfp_clean.pdf")).toBe("/samples/rfp_clean.pdf");
  });

  it("rfp_scanned.pdf → scanned sample", () => {
    expect(pdfUrlForRun("run_demo_x", "rfp_scanned.pdf")).toBe("/samples/rfp_scanned.pdf");
  });

  it("case-insensitive on the filename", () => {
    expect(pdfUrlForRun("run_demo_b2", "RFP_CLEAN.PDF")).toBe("/samples/rfp_clean.pdf");
  });

  it("unknown PDF filenames (e.g. corrupt.pdf) → null", () => {
    expect(pdfUrlForRun("run_demo_d4", "corrupt.pdf")).toBeNull();
    expect(pdfUrlForRun("run_demo_x", "somebody_else.pdf")).toBeNull();
  });

  it("returns null for paste runs (no input name)", () => {
    expect(pdfUrlForRun("run_demo_a1", null)).toBeNull();
  });

  it("returns null for non-PDF inputs even on a demo run", () => {
    expect(pdfUrlForRun("run_demo_a1", "paste.txt")).toBeNull();
  });

  it("returns null for non-demo runs (no fixture to serve)", () => {
    expect(pdfUrlForRun("run_prod_1234", "rfp_messy.pdf")).toBeNull();
  });
});
