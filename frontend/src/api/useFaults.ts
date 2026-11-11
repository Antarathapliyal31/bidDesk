import { useQuery } from "@tanstack/react-query";

import { FIXTURE_FAULTS, type FxFault, type FxFaultKey, withDelay } from "@/dev-fixtures";

/**
 * Demo fault list + enable flag (SPEC §10.6, §13 `/api/meta`). The
 * server decides whether to expose fault controls (`demo_faults_enabled`
 * — SPEC §14 + config F5 in DECISIONS.md: off by default in prod).
 *
 * Phase 7: swap the `queryFn` to `GET /api/meta` and return the
 * server's `{ demo_faults_enabled, faults }` mapped to `{ enabled,
 * faults }`. Static resource, so `staleTime: Infinity`.
 */
export type Fault = FxFault;
export type FaultKey = FxFaultKey;
export type FaultsMeta = { enabled: boolean; faults: Fault[] };

export function useFaults() {
  return useQuery<FaultsMeta>({
    queryKey: ["faults"],
    queryFn: () => withDelay({ enabled: true, faults: FIXTURE_FAULTS }),
    staleTime: Infinity,
  });
}
