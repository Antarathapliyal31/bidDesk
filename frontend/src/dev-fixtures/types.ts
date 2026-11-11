/**
 * Temporary local types for the Phase 6 frontend-shell (SPEC §11).
 * Prefixed `Fx*` so an accidental import outside pages/fixtures is
 * grep-able. These will be replaced by `components["schemas"][*]`
 * from `src/api/schema.gen.ts` at the Phase 5 merge. See README.md.
 */

export type FxRunStatus = "running" | "completed" | "partial" | "failed" | "interrupted";

export type FxOutcome =
  | "bid_ready"
  | "no_materials"
  | "invalid_input"
  | "budget_exhausted"
  | "error";

export type FxInputKind = "pdf" | "text";

export type FxBranch = {
  id: string;
  name: string;
  city: string;
  state: string;
  /**
   * Parts per million (matches backend seed: 66250 == 6.625%). The
   * frontend never computes tax totals — the backend does — so this
   * value is for display only. Use `formatTaxRate` in src/lib/format.ts.
   */
  tax_rate_ppm: number;
};

export type FxSampleKey = "clean" | "messy" | "scanned" | "paste";

/**
 * Discriminated union on `kind`:
 *   - "pdf"   → the New-bid page fetches `file_url`, turns it into a
 *               File, and feeds it to the Pdf dropzone.
 *   - "paste" → the New-bid page sets the textarea value to `text`.
 *
 * `kind` is the UI flavor and doesn't collide with the API's
 * `FxInputKind` ("pdf" | "text") — those are the server-visible run
 * inputs; "paste" is a client affordance for the text path.
 */
export type FxSampleInput =
  | {
      kind: "pdf";
      key: FxSampleKey;
      name: string;
      description: string;
      size_label: string;
      file_url: string;
    }
  | {
      kind: "paste";
      key: FxSampleKey;
      name: string;
      description: string;
      size_label: string;
      text: string;
    };

/** SPEC §9.7: agent-rev-0 vs latest-final diff counts, incl. accepted lines. */
export type FxDiffSummary = {
  changed: number;
  added: number;
  deleted: number;
  accepted: number;
};

export type FxRunSummary = {
  id: string;
  status: FxRunStatus;
  outcome: FxOutcome | null;
  input_kind: FxInputKind;
  input_name: string;
  project_name: string | null;
  branch_name: string;
  priced_lines: number;
  open_flags: number;
  total_cents: number | null;
  created_at: string;
  finalized_at: string | null;
  diff: FxDiffSummary | null;
};

export type FxFaultKey =
  | "pricing_timeout_once"
  | "pricing_timeout_always"
  | "analyst_malformed_once"
  | "pricing_hallucinated_sku"
  | "orchestrator_no_submit";

export type FxFault = {
  key: FxFaultKey;
  title: string;
  description: string;
};

export type FxMeta = {
  demo_faults_enabled: boolean;
};
