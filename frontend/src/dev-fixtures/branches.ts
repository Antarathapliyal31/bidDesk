import type { FxBranch } from "./types";

/**
 * Three deterministic fixture branches (SPEC §5.2). Mirrors
 * backend/app/catalog/seed.py BRANCHES; keep in sync when Phase 1
 * lands. The IDs and tax rates here are the canonical values the
 * backend seed will use — the fixture → real swap in Phase 6 proper
 * is data-identical for the New-bid form.
 */
export const FIXTURE_BRANCHES: FxBranch[] = [
  {
    id: "BR-NJ-01",
    name: "BidDesk North Jersey",
    city: "Edison",
    state: "NJ",
    tax_rate_ppm: 66250,
  },
  {
    id: "BR-NY-01",
    name: "BidDesk Long Island",
    city: "Islip",
    state: "NY",
    tax_rate_ppm: 86250,
  },
  {
    id: "BR-PA-01",
    name: "BidDesk Lehigh Valley",
    city: "Allentown",
    state: "PA",
    tax_rate_ppm: 60000,
  },
];

/** Default branch for new bids (first item in the list). */
export const FIXTURE_DEFAULT_BRANCH_ID = FIXTURE_BRANCHES[0]!.id;
