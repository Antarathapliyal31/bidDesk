/**
 * Live-run stream hook (SPEC §11.3). Fixture-aware on this branch:
 * when `runId` is in the fixture SSE registry, the hook replays a
 * scripted event list via `playStream` instead of touching the
 * network. Phase 5 merge removes the fixture branch and wires the
 * real path below:
 *
 *   1. `GET /api/runs/{id}/events?after_id=0` — replay persisted
 *      events into the reducer.
 *   2. Open `EventSource('/api/runs/{id}/stream?after_id=<last>')`;
 *      every incoming message is JSON.parse'd and dispatched.
 *   3. On error, mark `reconnecting`; the browser retries SSE with
 *      `Last-Event-ID` set to our most-recent `id`.
 *
 * The real-path stub below returns `connection: "closed"` for any
 * runId not in the fixture registry — keeps the hook callable
 * without breaking tests that mount components with an unknown
 * runId.
 *
 * StrictMode note: on dev-mode double-invocation, the first effect
 * run's cleanup cancels pending `setTimeout`s but any already-
 * dispatched events have advanced `state.lastEventId`. The second
 * effect run calls `playStream` again starting from id 1; the
 * reducer's `id <= lastEventId` guard makes those replays
 * harmless. If we ever add StrictMode-sensitive side effects here
 * (e.g. real network calls), the fixture branch would need to
 * pass `fromId: state.lastEventId` to avoid redundant dispatches.
 */
import { useEffect, useReducer } from "react";

import { getFixtureStream, playStream } from "@/dev-fixtures/sse";
import {
  initialRunViewState,
  runReducer,
  type ConnectionState,
  type RunViewState,
} from "@/lib/runReducer";

export type UseRunStreamResult = {
  state: RunViewState;
  connection: ConnectionState;
};

export type UseRunStreamOptions = {
  /**
   * Delay between consecutive fixture events (ms). Default 150 in
   * dev so the RunPage demo has a visible rhythm; tests pass 0 to
   * drain the stream as fast as setTimeout allows.
   */
  intervalMs?: number;
};

export function useRunStream(
  runId: string,
  options: UseRunStreamOptions = {},
): UseRunStreamResult {
  const [state, dispatch] = useReducer(runReducer, initialRunViewState);

  // In fixture mode, `connection` is deterministic from `runId`:
  // either we have a scripted stream (→ "live") or we don't
  // (→ "closed"). Deriving avoids a setState-in-effect smell flagged
  // by react-hooks/set-state-in-effect. Phase 5 merge reintroduces
  // a `useState` here for the real "connecting → live → reconnecting"
  // transitions driven by `EventSource` events.
  const fixture = getFixtureStream(runId);
  const connection: ConnectionState = fixture ? "live" : "closed";

  const intervalMs = options.intervalMs;

  useEffect(() => {
    const fx = getFixtureStream(runId);
    if (!fx) return undefined;
    const stop = playStream(fx, dispatch, { intervalMs: intervalMs ?? 150 });
    return () => stop();
  }, [runId, intervalMs]);

  return { state, connection };
}
