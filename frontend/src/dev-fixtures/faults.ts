import type { FxFault } from "./types";

/** Demo failure-injection controls (SPEC §10.6). */
export const FIXTURE_FAULTS: FxFault[] = [
  {
    key: "pricing_timeout_once",
    title: "Pricing timeout (once)",
    description: "First batch exceeds its 45 s timeout; retry succeeds.",
  },
  {
    key: "pricing_timeout_always",
    title: "Pricing timeout (always)",
    description: "One batch always times out; those lines are flagged, run continues.",
  },
  {
    key: "analyst_malformed_once",
    title: "Analyst malformed output",
    description: "First extraction returns invalid JSON; retry with feedback succeeds.",
  },
  {
    key: "pricing_hallucinated_sku",
    title: "Hallucinated SKU",
    description: "Model proposes a non-existent SKU; code downgrades to no_match.",
  },
  {
    key: "orchestrator_no_submit",
    title: "Orchestrator skips submit_bid",
    description: "Loop ends without a bid; fallback assembly runs.",
  },
];
