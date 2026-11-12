/**
 * Live-run stream hook (SPEC §11.3).
 *
 * Real path: open `EventSource('/api/runs/{id}/stream')` and
 * dispatch every parsed frame into the reducer. The backend's
 * `/stream` route first replays every persisted event (after the
 * `Last-Event-ID` header or `?after_id=` query, defaulting to 0),
 * then flips to live without a gap. No separate `/events` fetch —
 * doing both would deliver each event twice. The reducer's
 * `event.id <= state.lastEventId` guard (runReducer.ts) is a belt
 * on top of the braces.
 *
 * Fixture path (`VITE_FIXTURES=1`):
 *   - "live"    → scripted stream played event-by-event.
 *   - "instant" → scripted stream drained in the initializer.
 *   - null      → unknown runId; connection: "closed".
 */
import { useEffect, useReducer, useState } from "react";

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

import { USE_FIXTURES } from "./client";
import type { RunEvent } from "./events";

export const isReusedStream = _isReusedStream;

export type UseRunStreamResult = {
  state: RunViewState;
  connection: ConnectionState;
};

export type UseRunStreamOptions = {
  /** Delay between fixture events (ms). Tests pass 0. */
  intervalMs?: number;
};

export function useRunStream(
  runId: string,
  options: UseRunStreamOptions = {},
): UseRunStreamResult {
  const [state, dispatch] = useReducer(runReducer, runId, (rid) => {
    if (!USE_FIXTURES) return initialRunViewState;
    const fx = getFixtureStream(rid);
    if (fx === null || fx.mode !== "instant") return initialRunViewState;
    return fx.events.reduce((s, e) => runReducer(s, e), initialRunViewState);
  });

  const [connection, setConnection] = useState<ConnectionState>(() => {
    if (!USE_FIXTURES) return "connecting";
    const fx = getFixtureStream(runId);
    return fx === null ? "closed" : fx.mode === "live" ? "live" : "closed";
  });

  const intervalMs = options.intervalMs;

  // Fixture branch.
  useEffect(() => {
    if (!USE_FIXTURES) return undefined;
    const fx = getFixtureStream(runId);
    if (fx === null || fx.mode !== "live") return undefined;
    const stop = playStream(fx.events, dispatch, { intervalMs: intervalMs ?? 150 });
    return () => stop();
  }, [runId, intervalMs]);

  // Real branch: one SSE connection carries replay + live.
  useEffect(() => {
    if (USE_FIXTURES) return undefined;
    const es = new EventSource(`/api/runs/${runId}/stream`);
    setConnection("connecting");
    es.onopen = () => setConnection("live");
    es.onmessage = (ev) => {
      try {
        const parsed = JSON.parse(ev.data) as RunEvent;
        dispatch(parsed);
        if (parsed.type === "run.finished") {
          es.close();
          setConnection("closed");
        }
      } catch {
        setConnection("reconnecting");
      }
    };
    es.onerror = () => {
      // Browser auto-retries with Last-Event-ID; flag it so the UI
      // can surface a reconnect indicator.
      setConnection("reconnecting");
    };
    return () => {
      es.close();
    };
  }, [runId]);

  return { state, connection };
}
