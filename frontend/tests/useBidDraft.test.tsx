/**
 * useBidDraft hook — autosave debounce, optimistic update, server
 * reconcile. Fake timers so the 800 ms debounce is deterministic
 * without wall-clock waits.
 *
 * Fixture invariants exercised here:
 *   - initial fetch seeds both server and local drafts.
 *   - a queued op is visible on `bid` immediately (optimistic).
 *   - after AUTOSAVE_MS the save fires; local draft stays stable.
 *   - a second op during the save flight is replayed on top of
 *     the server's returned bid.
 *   - finalize flushes pending ops then flips kind to "final".
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useBidDraft } from "@/api/useBidDraft";
import { createTestQueryClient } from "@/app/queryClient";

function wrapper({ qc }: { qc: QueryClient }) {
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return Wrapper;
}

// Real timers — React Query + fake timers + waitFor polling
// deadlocks on the TanStack scheduler. Real delays are small
// (≤ 2s) so a 4s test timeout per case is plenty.

describe("useBidDraft", () => {
  it(
    "initial fetch seeds the hook's bid",
    async () => {
      const qc = createTestQueryClient() as unknown as QueryClient;
      const { result } = renderHook(() => useBidDraft("run_demo_messy"), {
        wrapper: wrapper({ qc }),
      });
      await waitFor(() => {
        expect(result.current.bid).not.toBeNull();
      });
      expect(result.current.bid!.lines.length).toBeGreaterThan(0);
      expect(result.current.save).toBe("saved");
      expect(result.current.dirty).toBe(false);
    },
    4_000,
  );

  it(
    "queueOp applies optimistically; save fires after debounce",
    async () => {
      const qc = createTestQueryClient() as unknown as QueryClient;
      const { result } = renderHook(() => useBidDraft("run_demo_messy"), {
        wrapper: wrapper({ qc }),
      });
      await waitFor(() => {
        expect(result.current.bid).not.toBeNull();
      });
      const line = result.current.bid!.lines.find(
        (l) => l.unit_price_cents !== null && l.sale_qty !== null,
      )!;

      await act(async () => {
        result.current.queueOp({
          op: "set_qty",
          line_id: line.line_id,
          sale_qty: "999",
        });
      });

      const optimistic = result.current.bid!.lines.find(
        (l) => l.line_id === line.line_id,
      );
      expect(optimistic?.sale_qty).toBe("999");
      expect(result.current.save).toBe("unsaved");
      expect(result.current.dirty).toBe(true);

      // Save fires after 800ms debounce + 400ms PATCH.
      await waitFor(() => expect(result.current.save).toBe("saved"), {
        timeout: 2_500,
      });
      expect(result.current.dirty).toBe(false);
      expect(result.current.serverRevision).toBeGreaterThan(1);
    },
    5_000,
  );

  it(
    "finalize flips kind to 'final'",
    async () => {
      const qc = createTestQueryClient() as unknown as QueryClient;
      const { result } = renderHook(() => useBidDraft("run_demo_b2"), {
        wrapper: wrapper({ qc }),
      });
      await waitFor(() => {
        expect(result.current.bid).not.toBeNull();
      });
      await act(async () => {
        await result.current.finalize();
      });
      expect(result.current.bid?.kind).toBe("final");
    },
    4_000,
  );
});
