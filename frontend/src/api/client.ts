/**
 * Thin fetch helper shared by every hook. Handles the two shapes
 * of non-OK response — fetch rejection (network) and HTTP non-2xx
 * (envelope) — and normalises both into `ApiError` so callers can
 * `throw` once and let React Query surface the typed error.
 *
 * `USE_FIXTURES` is true when `import.meta.env.VITE_FIXTURES === "1"`.
 * Hooks branch on it to call the scripted fixture code (tests + the
 * Playwright e2e) or the real backend. Default (unset) is real.
 */
import { ApiError, apiErrorFromResponse } from "./ApiError";

export const USE_FIXTURES = import.meta.env.VITE_FIXTURES === "1";

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
