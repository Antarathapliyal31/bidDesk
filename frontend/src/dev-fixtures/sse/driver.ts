/**
 * Dispatch helpers for scripted SSE streams (fixture mode).
 *
 * Two entry points, both pure in the "no side effects beyond
 * dispatch + the returned stop" sense:
 *   - `drainAll(events, dispatch, { fromId? })` — fire every event
 *     synchronously in order. For tests that want to assert the
 *     terminal state without setTimeout plumbing.
 *   - `playStream(events, dispatch, { intervalMs?, fromId? })` —
 *     schedule each event at `i * intervalMs` via `setTimeout`.
 *     Returns a cleanup that cancels still-pending dispatches;
 *     safe to call multiple times (idempotent).
 *
 * `fromId` makes both helpers skip events with `id <= fromId`,
 * mimicking an `EventSource` reconnect with `Last-Event-ID` set to
 * the client's most-recent `id`.
 *
 * Phase 5 merge: this module AND its tests are deleted alongside
 * the fixture SSE registry when `useRunStream` switches to a real
 * `EventSource`.
 */
import type { RunEvent } from "@/api";

export type DispatchFn = (event: RunEvent) => void;

export type PlayStreamOptions = {
  /** Delay between successive events, in ms. Default 150. */
  intervalMs?: number;
  /** Skip events whose id is ≤ this value. */
  fromId?: number;
};

export type DrainAllOptions = {
  fromId?: number;
};

function filterFrom(events: readonly RunEvent[], fromId: number | undefined): RunEvent[] {
  if (fromId === undefined) return [...events];
  return events.filter((e) => e.id > fromId);
}

export function drainAll(
  events: readonly RunEvent[],
  dispatch: DispatchFn,
  options: DrainAllOptions = {},
): void {
  for (const e of filterFrom(events, options.fromId)) dispatch(e);
}

export function playStream(
  events: readonly RunEvent[],
  dispatch: DispatchFn,
  options: PlayStreamOptions = {},
): () => void {
  const interval = options.intervalMs ?? 150;
  const eligible = filterFrom(events, options.fromId);
  const timers: ReturnType<typeof setTimeout>[] = [];
  eligible.forEach((e, i) => {
    timers.push(setTimeout(() => dispatch(e), i * interval));
  });
  let stopped = false;
  return () => {
    if (stopped) return;
    stopped = true;
    for (const t of timers) clearTimeout(t);
  };
}
