/**
 * Live-run stream hook (SPEC §11.3). Fixture-aware on this branch;
 * three fixture paths (see `getFixtureStream`):
 *
 *   - "live"    — scripted stream played event-by-event via
 *                 `playStream`. The user watches the stream evolve
 *                 (rfp_messy canonical + New-bid fallback).
 *   - "instant" — scripted stream drained synchronously via
 *                 `useReducer`'s initializer so the page shows the
 *                 run's terminal state on first paint. Used for
 *                 History-row clicks (SPEC §11.3 "replay from DB"
 *                 for finished runs).
 *   - null      — unknown runId; connection: "closed", startedAt
 *                 stays null, RunPage shows the empty state.
 *
 * Phase 5 merge replaces all three with:
 *   1. `GET /api/runs/{id}/events?after_id=0` — replay persisted
 *      events into the reducer (initializer still drains; same
 *      invariant — reducer is the only way state is built).
 *   2. Open `EventSource('/api/runs/{id}/stream?after_id=<last>')`;
 *      every incoming message is JSON.parse'd and dispatched.
 *   3. On error, mark `reconnecting`; the browser retries SSE with
 *      `Last-Event-ID` set to our most-recent `id`.
 *
 * RunId-change handling: RunPage is keyed by `runId` (router.tsx)
 * so a new runId remounts this hook and re-runs the initializer.
 * Without that, the reducer's id-guard would silently drop the new
 * stream's events (they all have ids ≤ the previous run's).
 *
 * StrictMode note: on dev-mode double-invocation, the first effect
 * run's cleanup cancels pending `setTimeout`s but any already-
 * dispatched events have advanced `state.lastEventId`. The second
 * effect run calls `playStream` again starting from id 1; the
 * reducer's `id <= lastEventId` guard makes those replays
 * harmless.
 */
import { useEffect, useReducer } from "react";

import {
  getFixtureStream,
  isReusedStream as _isReusedStream,
  playStream,
} from "@/dev-fixtures/sse";
import {
  initialRunViewState,
  runReducer,
  type ConnectionState,
  type RunViewState,
} from "@/lib/runReducer";

/**
 * Re-exported here so features (`RunHeader`) can read the
 * "is this a reused fixture stream?" flag without reaching into
 * `@/dev-fixtures` directly — the ESLint no-restricted-imports
 * rule scopes dev-fixtures to `src/api/**` + `tests/**`.
 */
export const isReusedStream = _isReusedStream;

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
  // The initializer runs ONCE per mount. For "instant" fixture
  // streams (History-row clicks) we drain the whole event list
  // through the reducer here so the terminal state is on screen
  // before the first paint — no spinner, no empty state, no
  // mid-render event tick. For "live" and unknown runs it returns
  // the empty initial state; the useEffect below starts playStream
  // for "live".
  const [state, dispatch] = useReducer(runReducer, runId, (rid) => {
    const fx = getFixtureStream(rid);
    if (fx === null || fx.mode !== "instant") return initialRunViewState;
    return fx.events.reduce((s, e) => runReducer(s, e), initialRunViewState);
  });

  // `connection` is deterministic from the fixture result. Live
  // streams render as "live" while events are still arriving;
  // instant streams and unknown runs render as "closed" (there's
  // nothing more coming). Phase-5 merge reintroduces a useState
  // here for real EventSource transitions.
  const fixture = getFixtureStream(runId);
  const connection: ConnectionState =
    fixture === null ? "closed" : fixture.mode === "live" ? "live" : "closed";

  const intervalMs = options.intervalMs;

  useEffect(() => {
    const fx = getFixtureStream(runId);
    if (fx === null || fx.mode !== "live") return undefined;
    const stop = playStream(fx.events, dispatch, { intervalMs: intervalMs ?? 150 });
    return () => stop();
  }, [runId, intervalMs]);

  return { state, connection };
}
