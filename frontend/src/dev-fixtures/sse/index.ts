/**
 * Registry of fixture SSE streams, keyed by exact `runId`.
 * `useRunStream` consults this before touching the network; a hit
 * drives the reducer without going through the real `GET /events`
 * + `EventSource`.
 *
 * Two kinds of stream:
 *   - "live"    — played event-by-event via the SSE driver so the
 *                 user watches the stream evolve (`run_demo_messy`,
 *                 and the rfp_messy fallback for New-bid submits).
 *   - "instant" — drained synchronously into the reducer so the
 *                 history row's terminal state is on screen the
 *                 instant the RunPage mounts (SPEC §11.3 "replay
 *                 from DB" — no animation for finished runs).
 *
 * Fallbacks + history detection:
 *   - exact match in REGISTRY → live.
 *   - matches a FIXTURE_RUNS id → instant, events built from the row.
 *   - starts with `run_demo_` → live, fallback to the messy stream.
 *   - else → null (shown as "we couldn't find that run").
 *
 * `isReusedStream(runId)` returns true for every `run_demo_*` id
 * so the RunHeader shows a "Demo data" chip on EVERY fixture-mode
 * run. The Phase-5 merge deletes this file, `rfp-messy.ts`,
 * `history.ts`, and the chip together.
 */
import type { RunEvent } from "@/api";

import { FIXTURE_RUNS } from "../runs";

import { historyRunEvents } from "./history";
import { RFP_MESSY_EVENTS, RFP_MESSY_RUN_ID } from "./rfp-messy";

/** How `useRunStream` should surface this stream to the reducer. */
export type FixtureStreamMode = "live" | "instant";

export type FixtureStreamResult = {
  events: readonly RunEvent[];
  mode: FixtureStreamMode;
};

const REGISTRY: Readonly<Record<string, readonly RunEvent[]>> = {
  [RFP_MESSY_RUN_ID]: RFP_MESSY_EVENTS,
};

export function getFixtureStream(runId: string): FixtureStreamResult | null {
  const exact = REGISTRY[runId];
  if (exact) return { events: exact, mode: "live" };

  const historyRun = FIXTURE_RUNS.find((r) => r.id === runId);
  if (historyRun) return { events: historyRunEvents(historyRun), mode: "instant" };

  if (runId.startsWith("run_demo_")) return { events: RFP_MESSY_EVENTS, mode: "live" };
  return null;
}

export function isReusedStream(runId: string): boolean {
  return runId.startsWith("run_demo_");
}

export { RFP_MESSY_EVENTS, RFP_MESSY_RUN_ID } from "./rfp-messy";
export { historyRunEvents } from "./history";
export {
  drainAll,
  playStream,
  type DispatchFn,
  type DrainAllOptions,
  type PlayStreamOptions,
} from "./driver";
