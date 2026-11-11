/**
 * Registry of fixture SSE streams, keyed by exact `runId`.
 * `useRunStream` consults this registry before touching the
 * network; a hit drives the reducer via the driver and skips
 * the real `GET /events` + `EventSource` entirely.
 *
 * Phase 5 merge: this file + rfp-messy.ts + driver.ts (and their
 * tests) are deleted together.
 */
import type { RunEvent } from "@/api";

import { RFP_MESSY_EVENTS, RFP_MESSY_RUN_ID } from "./rfp-messy";

const REGISTRY: Readonly<Record<string, readonly RunEvent[]>> = {
  [RFP_MESSY_RUN_ID]: RFP_MESSY_EVENTS,
};

export function getFixtureStream(runId: string): readonly RunEvent[] | null {
  return REGISTRY[runId] ?? null;
}

export { RFP_MESSY_RUN_ID } from "./rfp-messy";
export {
  drainAll,
  playStream,
  type DispatchFn,
  type DrainAllOptions,
  type PlayStreamOptions,
} from "./driver";
