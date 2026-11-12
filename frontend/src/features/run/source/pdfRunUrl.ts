/**
 * Map a run id + input name to a PDF URL for the SourcePanel.
 *
 * Fixture mode: look up by the input filename, not the runId
 * prefix — a history run that was submitted with rfp_clean.pdf
 * must open rfp_clean.pdf, not whichever sample the fallback
 * stream happens to reuse (bug: run_demo_b2 opened rfp_messy).
 *
 * The three sample PDFs live under `/samples/` and are shipped
 * from `frontend/public/samples/`. Unknown filenames (e.g. the
 * `corrupt.pdf` fixture row) return null; the SourcePanel then
 * renders its "no PDF available" placeholder.
 *
 * Phase 5 merge: this file becomes a one-liner
 *   return `/api/runs/${runId}/source.pdf`;
 * and the backend decides what to serve. Keeping the helper
 * co-located with the SourcePanel means the swap is scoped to one
 * file.
 */

const SAMPLE_PDFS: Readonly<Record<string, string>> = {
  "rfp_clean.pdf": "/samples/rfp_clean.pdf",
  "rfp_messy.pdf": "/samples/rfp_messy.pdf",
  "rfp_scanned.pdf": "/samples/rfp_scanned.pdf",
};

export function pdfUrlForRun(runId: string, inputName: string | null): string | null {
  // Paste runs + non-PDF inputs have nothing to show.
  if (inputName === null) return null;
  const lower = inputName.toLowerCase();
  if (!lower.endsWith(".pdf")) return null;

  // Only fixture-mode (demo) runs get a sample PDF. Non-demo
  // runs would need `/api/runs/:id/source.pdf` — Phase 5.
  if (!runId.startsWith("run_demo_")) return null;

  return SAMPLE_PDFS[lower] ?? null;
}
