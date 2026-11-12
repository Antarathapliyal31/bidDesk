/**
 * Live-run stream hook (SPEC §11.3).
 *
 * Real path: open `EventSource('/api/runs/{id}/stream')` and
 * subscribe one shared handler per event-type name. The backend
 * emits NAMED SSE frames (`event: run.started`, `event: item.priced`,
 * etc.); `EventSource.onmessage` only fires for the unnamed default
 * event, so without `addEventListener(type, …)` the UI goes "Live"
 * but receives nothing. The backend's `/stream` route first replays
 * every persisted event (respecting `Last-Event-ID` / `?after_id=`),
 * then flips to live without a gap. The reducer's
 * `event.id <= state.lastEventId` guard (`runReducer.ts`) is the
 * belt on top of the single-connection design.
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

import { isFixtureMode } from "./client";
import { RUN_EVENT_TYPES, type RunEvent } from "./events";

export const isReusedStream = _isReusedStream;

export type UseRunStreamResult = {
  state: RunViewState;
  connection: ConnectionState;
};

export type UseRunStreamOptions = {
  /** Delay between fixture events (ms). Tests pass 0. */
  intervalMs?: number;
  /**
   * Inject a custom EventSource factory. Lets tests mock a
   * NAMED-event stream without relying on jsdom's EventSource.
   */
  eventSourceFactory?: (url: string) => EventSource;
};

export function useRunStream(
  runId: string,
  options: UseRunStreamOptions = {},
): UseRunStreamResult {
  const [state, dispatch] = useReducer(runReducer, runId, (rid) => {
    if (!isFixtureMode()) return initialRunViewState;
    const fx = getFixtureStream(rid);
    if (fx === null || fx.mode !== "instant") return initialRunViewState;
    return fx.events.reduce((s, e) => runReducer(s, e), initialRunViewState);
  });

  const [connection, setConnection] = useState<ConnectionState>(() => {
    if (!isFixtureMode()) return "connecting";
    const fx = getFixtureStream(runId);
    return fx === null ? "closed" : fx.mode === "live" ? "live" : "closed";
  });

  const intervalMs = options.intervalMs;
  const eventSourceFactory = options.eventSourceFactory;

  // Fixture branch.
  useEffect(() => {
    if (!isFixtureMode()) return undefined;
    const fx = getFixtureStream(runId);
    if (fx === null || fx.mode !== "live") return undefined;
    const stop = playStream(fx.events, dispatch, { intervalMs: intervalMs ?? 150 });
    return () => stop();
  }, [runId, intervalMs]);

  // Real branch: one SSE connection carries replay + live. Named
  // listeners (one per event type in the union) parse each frame
  // and dispatch into the reducer.
  useEffect(() => {
    if (isFixtureMode()) return undefined;
    const url = `/api/runs/${runId}/stream`;
    const es = eventSourceFactory ? eventSourceFactory(url) : new EventSource(url);
    setConnection("connecting");

    const onOpen = () => setConnection("live");
    const onError = () => {
      // Browser auto-retries with Last-Event-ID; flag it so the UI
      // can surface a reconnect indicator.
      setConnection("reconnecting");
    };
    const onFrame = (ev: MessageEvent<string>) => {
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

    es.addEventListener("open", onOpen);
    es.addEventListener("error", onError);
    for (const type of RUN_EVENT_TYPES) {
      es.addEventListener(type, onFrame as EventListener);
    }

    return () => {
      es.removeEventListener("open", onOpen);
      es.removeEventListener("error", onError);
      for (const type of RUN_EVENT_TYPES) {
        es.removeEventListener(type, onFrame as EventListener);
      }
      es.close();
    };
  }, [runId, eventSourceFactory]);

  return { state, connection };
}
