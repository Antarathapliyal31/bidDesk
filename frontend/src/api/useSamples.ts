import { useQuery } from "@tanstack/react-query";

import { FIXTURE_SAMPLES, type FxSampleInput, withDelay } from "@/dev-fixtures";

/**
 * Sample inputs for the New-bid page. Phase 7: swap the `queryFn`
 * to `GET /api/samples`. Static catalog resource, so `staleTime:
 * Infinity` — no refetch on focus or remount.
 */
export type Sample = FxSampleInput;

export function useSamples() {
  return useQuery<Sample[]>({
    queryKey: ["samples"],
    queryFn: () => withDelay(FIXTURE_SAMPLES),
    staleTime: Infinity,
  });
}
