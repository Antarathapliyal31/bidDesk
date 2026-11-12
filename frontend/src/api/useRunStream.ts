/**
 * Live-run stream hook (SPEC §11.3).
 *
 * Real path:
 *   1. `GET /api/runs/{id}/events?after_id=0` — replay persisted
 *      events through the reducer (drained in the useReducer
 *      initializer when the run is already finished, so the
 *      terminal state is on first paint).
 *   2. `EventSource('/api/runs/{id}/stream')` for live events;
 *      Last-Event-ID replay is handled by the browser and the
 *      backend honours it on reconnect. Every message is
 *      JSON.parse'd and dispatched.
 *   3. SSE errors flip `connection` to "reconnecting"; the browser
 *      handles retry automatically. We close on `run.finished`.
 *
 * Fixture path (`VITE_FIXTURES=1`):
 *   - "live"    → scripted stream played event-by-event.
 *   - "instant" → scripted stream drained in the initializer.
 *   - null      → unknown runId; connection: "closed".
 *
 * RunId-change handling: RunPage is keyed by `runId` (router.tsx)
 * so a new runId remounts this hook and re-runs the initializer.
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

import { apiFetch, USE_FIXTURES } from "./client";
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

  // Real branch: replay persisted events, then open SSE.
  useEffect(() => {
    if (USE_FIXTURES) return undefined;
    let cancelled = false;
    let es: EventSource | null = null;

    (async () => {
      try {
        const res = await apiFetch(`/api/runs/${runId}/events?after_id=0`);
        const events = (await res.json()) as RunEvent[];
        if (cancelled) return;
        for (const e of events) dispatch(e);
        // If the replay already contained `run.finished` the stream
        // is closed for good — no SSE needed.
        if (events.some((e) => e.type === "run.finished")) {
          setConnection("closed");
          return;
        }
      } catch {
        if (!cancelled) setConnection("closed");
        return;
      }

      if (cancelled) return;
      es = new EventSource(`/api/runs/${runId}/stream`);
      setConnection("live");
      es.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(ev.data) as RunEvent;
          dispatch(parsed);
          if (parsed.type === "run.finished") {
            es?.close();
            setConnection("closed");
          }
        } catch {
          // Malformed frame — surface via connection state; don't
          // throw out of a browser callback.
          setConnection("reconnecting");
        }
      };
      es.onerror = () => {
        // Browser will auto-retry with Last-Event-ID; flag it so
        // the UI can show a reconnect indicator.
        setConnection("reconnecting");
      };
    })();

    return () => {
      cancelled = true;
      es?.close();
    };
  }, [runId]);

  return { state, connection };
}
