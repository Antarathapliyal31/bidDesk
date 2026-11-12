/**
 * Map a run id + input name to a PDF URL for the SourcePanel.
 *
 * Real mode: the backend serves the stored upload at
 * `GET /api/runs/{id}/source.pdf` (returns 404 for paste runs).
 *
 * Fixture mode (VITE_FIXTURES=1): fall back to the static sample
 * PDFs under `/samples/` by input filename so the Playwright e2e
 * + manual demo keep working.
 */
import { USE_FIXTURES } from "@/api/client";

const SAMPLE_PDFS: Readonly<Record<string, string>> = {
  "rfp_clean.pdf": "/samples/rfp_clean.pdf",
  "rfp_messy.pdf": "/samples/rfp_messy.pdf",
  "rfp_scanned.pdf": "/samples/rfp_scanned.pdf",
};

export function pdfUrlForRun(runId: string, inputName: string | null): string | null {
  if (inputName === null) return null;
  const lower = inputName.toLowerCase();
  if (!lower.endsWith(".pdf")) return null;

  if (USE_FIXTURES) {
    if (!runId.startsWith("run_demo_")) return null;
    return SAMPLE_PDFS[lower] ?? null;
  }
  return `/api/runs/${runId}/source.pdf`;
}
