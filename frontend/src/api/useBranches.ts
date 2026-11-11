import { useQuery } from "@tanstack/react-query";

import { FIXTURE_BRANCHES, type FxBranch, withDelay } from "@/dev-fixtures";

/**
 * Branches data access. Already a real `useQuery` so the New-bid
 * form's loading / error / data branches exist today — in Phase 7
 * only the `queryFn` changes (fetch `GET /api/branches` instead of
 * resolving a fixture).
 *
 * TEMPORARY: this is one of the few files allowed to import
 * `@/dev-fixtures/*`. ESLint `no-restricted-imports` scopes those
 * imports to `src/api/**` + `tests/**` (see eslint.config.js);
 * Phase 7 removes the fixture import here entirely.
 */
export type Branch = FxBranch;

export function useBranches() {
  return useQuery<Branch[]>({
    queryKey: ["branches"],
    queryFn: () => withDelay(FIXTURE_BRANCHES),
    // Branches are a stable catalog resource — refetch only when the
    // query is explicitly invalidated, not on every mount / focus.
    staleTime: Infinity,
  });
}
