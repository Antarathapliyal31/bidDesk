import { QueryClient } from "@tanstack/react-query";

/**
 * TanStack Query client. Provider wired now so Phase 6 proper can
 * drop in `useRuns()` / `useBid()` hooks without touching the shell.
 *
 * Retry policy: at most one retry, and ONLY on transport errors
 * (fetch threw / no response) or 5xx responses. Never on 4xx — a
 * 404/409/422 means the server answered and the answer is "no", so
 * retrying would mask the real failure. Mutations never auto-retry.
 */

/**
 * Error shape fetch wrappers throw. `src/api/client.ts` lands in
 * Phase 6 proper and will attach `status` to every error it raises.
 * This structural type lets us start honoring it today.
 */
type HttpishError = Error & {
  status?: number;
  response?: { status?: number } | undefined;
};

function readStatus(err: unknown): number | undefined {
  if (err && typeof err === "object") {
    const e = err as HttpishError;
    if (typeof e.status === "number") return e.status;
    if (e.response && typeof e.response.status === "number") return e.response.status;
  }
  return undefined;
}

export function shouldRetry(failureCount: number, err: unknown): boolean {
  if (failureCount >= 1) return false;
  const status = readStatus(err);
  if (status === undefined) {
    // Transport error — fetch threw, no response arrived.
    return true;
  }
  return status >= 500 && status <= 599;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: shouldRetry,
        refetchOnWindowFocus: true,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}

/** Deterministic client for tests: no retries, no window-focus refetch. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: Infinity,
        retry: false,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}
