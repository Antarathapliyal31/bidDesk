/**
 * Map a run id + input name to a PDF URL for the SourcePanel.
 *
 * Fixture mode: `run_demo_messy` → `/samples/rfp_messy.pdf`, plus
 * any other `run_demo_*` id falls back to the messy sample (same
 * reasoning as `getFixtureStream`'s prefix fallback). Returns null
 * for inputs we have no PDF for (paste runs, uploaded files the
 * browser didn't keep a blob URL for).
 *
 * Phase 5 merge: this file is replaced with a one-liner
 * `return `/api/runs/${runId}/source.pdf``. Keeping the helper
 * co-located with the SourcePanel means the swap is scoped to one
 * file.
 */
/**
 * Demo-mode: every `run_demo_*` id resolves to the messy sample PDF
 * (same prefix-fallback as the SSE registry — see
 * `src/dev-fixtures/sse/index.ts`). Hard-coding the path here keeps
 * `features/` free of `@/dev-fixtures/*` imports (ESLint rule). The
 * Phase-5 merge deletes this file entirely.
 */
const DEMO_PDF_URL = "/samples/rfp_messy.pdf";

export function pdfUrlForRun(runId: string, inputName: string | null): string | null {
  // Paste runs have no PDF — surface the callout only.
  if (inputName === null) return null;
  if (!inputName.toLowerCase().endsWith(".pdf")) return null;

  if (runId.startsWith("run_demo_")) return DEMO_PDF_URL;
  return null;
}
