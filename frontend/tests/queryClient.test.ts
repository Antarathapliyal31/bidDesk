// @vitest-environment node
/**
 * The TanStack Query retry predicate must retry transport errors and
 * 5xx exactly once, and never retry 4xx. Mutations never retry.
 */
import { describe, expect, it } from "vitest";

import { createQueryClient, createTestQueryClient, shouldRetry } from "../src/app/queryClient";

describe("query retry policy", () => {
  it("retries transport errors (no response)", () => {
    const err = new Error("ECONNRESET");
    expect(shouldRetry(0, err)).toBe(true);
  });

  it("retries 5xx exactly once", () => {
    const err = Object.assign(new Error("Internal"), { status: 503 });
    expect(shouldRetry(0, err)).toBe(true);
    expect(shouldRetry(1, err)).toBe(false);
  });

  it("does not retry 404", () => {
    const err = Object.assign(new Error("Not found"), { status: 404 });
    expect(shouldRetry(0, err)).toBe(false);
  });

  it("does not retry 409 (edit conflict)", () => {
    const err = Object.assign(new Error("Conflict"), { status: 409 });
    expect(shouldRetry(0, err)).toBe(false);
  });

  it("does not retry 422 (validation)", () => {
    const err = Object.assign(new Error("Unprocessable"), { status: 422 });
    expect(shouldRetry(0, err)).toBe(false);
  });

  it("honors the status on err.response.status (fetch-error style)", () => {
    const err = Object.assign(new Error("Bad"), { response: { status: 500 } });
    expect(shouldRetry(0, err)).toBe(true);
  });

  it("factory returns a configured client (production defaults)", () => {
    const qc = createQueryClient();
    const defs = qc.getDefaultOptions();
    expect(defs.queries?.staleTime).toBe(30_000);
    expect(typeof defs.queries?.retry).toBe("function");
    expect(defs.mutations?.retry).toBe(0);
  });

  it("test factory disables retries and focus-refetch", () => {
    const qc = createTestQueryClient();
    const defs = qc.getDefaultOptions();
    expect(defs.queries?.retry).toBe(false);
    expect(defs.queries?.refetchOnWindowFocus).toBe(false);
    expect(defs.queries?.staleTime).toBe(Infinity);
  });
});
