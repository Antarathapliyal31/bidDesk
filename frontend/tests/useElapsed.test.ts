/**
 * `useElapsed` — seconds since `startIso`, ticking once a second.
 */
import { renderHook } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatElapsed, useElapsed } from "../src/app/useElapsed";

describe("formatElapsed", () => {
  it("formats small values as m:ss with zero-padding", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(3)).toBe("0:03");
    expect(formatElapsed(59)).toBe("0:59");
  });

  it("formats values across the minute boundary", () => {
    expect(formatElapsed(60)).toBe("1:00");
    expect(formatElapsed(84)).toBe("1:24");
    expect(formatElapsed(605)).toBe("10:05");
    expect(formatElapsed(4_392)).toBe("73:12");
  });
});

describe("useElapsed", () => {
  const BASE = Date.parse("2026-11-11T12:00:00Z");

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(BASE));
  });
  afterEach(() => vi.useRealTimers());

  it("returns 0 when startIso is null", () => {
    const { result } = renderHook(() => useElapsed(null));
    expect(result.current).toBe(0);
  });

  it("ticks forward once a second", () => {
    const { result } = renderHook(() => useElapsed(new Date(BASE).toISOString()));
    expect(result.current).toBe(0);
    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(result.current).toBe(3);
  });

  it("freeze=true stops the tick", () => {
    const { result, rerender } = renderHook(
      ({ freeze }: { freeze: boolean }) =>
        useElapsed(new Date(BASE).toISOString(), freeze),
      { initialProps: { freeze: false } },
    );
    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    expect(result.current).toBe(2);

    rerender({ freeze: true });
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    // Time passed on the wall clock but the hook stopped scheduling
    // new ticks; last visible value = 2 (frozen).
    expect(result.current).toBe(2);
  });
});
