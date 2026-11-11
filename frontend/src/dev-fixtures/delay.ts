/**
 * Fake-async helpers so fixture-backed pages can exercise their
 * loading / error states on the real timings. Replaced when
 * TanStack Query hooks talk to the real API in Phase 6 proper.
 */

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function withDelay<T>(value: T, ms = 600): Promise<T> {
  await sleep(ms);
  return value;
}

export async function failAfter(ms = 400, message = "Fixture failure"): Promise<never> {
  await sleep(ms);
  throw new Error(message);
}
