import { useQuery } from "@tanstack/react-query";

import { FIXTURE_BRANCHES, type FxBranch, withDelay } from "@/dev-fixtures";

import type { ApiError } from "./ApiError";
import { apiFetch, isFixtureMode } from "./client";

/**
 * Branches data access. Real `useQuery` — fixture mode swaps the
 * resolver only. Shape is pinned by `GET /api/branches` in the
 * backend (`app/api/branches.py`); `tax_rate_ppm` is display-only.
 */
export type Branch = FxBranch;

export function useBranches() {
  return useQuery<Branch[], ApiError>({
    queryKey: ["branches"],
    queryFn: async () => {
      if (isFixtureMode()) return withDelay(FIXTURE_BRANCHES);
      const res = await apiFetch("/api/branches");
      return (await res.json()) as Branch[];
    },
    staleTime: Infinity,
  });
}
