/**
 * Thin fetch helper shared by every hook. Handles the two shapes
 * of non-OK response — fetch rejection (network) and HTTP non-2xx
 * (envelope) — and normalises both into `ApiError` so callers can
 * `throw` once and let React Query surface the typed error.
 *
 * `isFixtureMode()` reads `import.meta.env.VITE_FIXTURES` on every
 * call so `vi.stubEnv` can flip the branch mid-test. A module-level
 * const would freeze the value at import.
 */
import { ApiError, apiErrorFromResponse } from "./ApiError";

export function isFixtureMode(): boolean {
  return import.meta.env.VITE_FIXTURES === "1";
}

export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  let res: Response;
  try {
    res = await fetch(input, init);
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Network error";
    throw new ApiError("network_error", msg, 0);
  }
  if (!res.ok) throw await apiErrorFromResponse(res);
  return res;
}
