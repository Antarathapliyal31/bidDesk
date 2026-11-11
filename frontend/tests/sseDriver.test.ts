// @vitest-environment node
/**
 * Fixture SSE driver — `drainAll` + `playStream`. Fake timers so
 * we can assert exact scheduling without waiting.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RunEvent } from "@/api";

import { drainAll, playStream } from "../src/dev-fixtures/sse/driver";

function mk(id: number): RunEvent {
  return {
    id,
    run_id: "run_test",
    ts: "2026-11-11T00:00:00Z",
    agent: null,
    span_id: null,
    parent_span_id: null,
    type: "run.status",
    payload: { status: "running", current_step: null },
  };
}

describe("drainAll", () => {
  it("fires every event synchronously, in order", () => {
    const events = [mk(1), mk(2), mk(3)];
    const seen: number[] = [];
    drainAll(events, (e) => seen.push(e.id));
    expect(seen).toEqual([1, 2, 3]);
  });

  it("fromId skips events with id ≤ fromId", () => {
    const events = [mk(1), mk(5), mk(10), mk(11), mk(20)];
    const seen: number[] = [];
    drainAll(events, (e) => seen.push(e.id), { fromId: 10 });
    expect(seen).toEqual([11, 20]);
  });

  it("fromId greater than the last id dispatches nothing", () => {
    const events = [mk(1), mk(2), mk(3)];
    const seen: number[] = [];
    drainAll(events, (e) => seen.push(e.id), { fromId: 99 });
    expect(seen).toEqual([]);
  });
});

describe("playStream", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("schedules each event at i * intervalMs and dispatches in order", () => {
    const events = [mk(1), mk(2), mk(3)];
    const seen: number[] = [];
    playStream(events, (e) => seen.push(e.id), { intervalMs: 100 });

    // Before any timers advance: nothing dispatched.
    expect(seen).toEqual([]);

    vi.advanceTimersByTime(0);
    expect(seen).toEqual([1]);

    vi.advanceTimersByTime(100);
    expect(seen).toEqual([1, 2]);

    vi.advanceTimersByTime(100);
    expect(seen).toEqual([1, 2, 3]);
  });

  it("stop() mid-stream cancels remaining dispatches", () => {
    const events = [mk(1), mk(2), mk(3), mk(4)];
    const seen: number[] = [];
    const stop = playStream(events, (e) => seen.push(e.id), { intervalMs: 100 });

    vi.advanceTimersByTime(100); // fires id 1 (t=0) + id 2 (t=100)
    expect(seen).toEqual([1, 2]);

    stop();
    vi.advanceTimersByTime(10_000);
    expect(seen).toEqual([1, 2]);
  });

  it("stop() called twice is safe (idempotent)", () => {
    const events = [mk(1), mk(2)];
    const stop = playStream(events, () => {}, { intervalMs: 100 });
    expect(() => {
      stop();
      stop();
    }).not.toThrow();
  });

  it("fromId skips events with id ≤ fromId; first dispatched is id (fromId+1 or later)", () => {
    const events = [mk(1), mk(5), mk(10), mk(11), mk(20)];
    const seen: number[] = [];
    playStream(events, (e) => seen.push(e.id), { intervalMs: 50, fromId: 10 });

    vi.advanceTimersByTime(0);
    expect(seen).toEqual([11]); // skipped 1, 5, 10
    vi.advanceTimersByTime(50);
    expect(seen).toEqual([11, 20]);
  });

  it("fromId greater than the last id dispatches nothing; stop() is still safe", () => {
    const events = [mk(1), mk(2), mk(3)];
    const seen: number[] = [];
    const stop = playStream(events, (e) => seen.push(e.id), { intervalMs: 50, fromId: 99 });
    vi.advanceTimersByTime(10_000);
    expect(seen).toEqual([]);
    expect(() => stop()).not.toThrow();
  });
});
