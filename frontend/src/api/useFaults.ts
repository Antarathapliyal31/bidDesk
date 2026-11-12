import { useQuery } from "@tanstack/react-query";

import { FIXTURE_FAULTS, type FxFault, type FxFaultKey, withDelay } from "@/dev-fixtures";

import { apiFetch, USE_FIXTURES } from "./client";

/**
 * Demo fault list + enable flag. Real path reads `GET /api/meta`
 * and maps `{ demo_faults_enabled, faults }` to the hook's
 * `{ enabled, faults }` shape (SPEC §10.6, §13).
 */
export type Fault = FxFault;
export type FaultKey = FxFaultKey;
export type FaultsMeta = { enabled: boolean; faults: Fault[] };

type MetaResponse = { demo_faults_enabled: boolean; faults: Fault[] };

export function useFaults() {
  return useQuery<FaultsMeta>({
    queryKey: ["faults"],
    queryFn: async () => {
      if (USE_FIXTURES) return withDelay({ enabled: true, faults: FIXTURE_FAULTS });
      const res = await apiFetch("/api/meta");
      const meta = (await res.json()) as MetaResponse;
      return { enabled: meta.demo_faults_enabled, faults: meta.faults };
    },
    staleTime: Infinity,
  });
}
