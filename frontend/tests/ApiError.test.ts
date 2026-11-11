// @vitest-environment node
/**
 * ApiError + apiErrorFromResponse — the single parsing path for
 * every unhappy fetch response. Known codes pass through, unknown
 * codes fall back to `server_error` (with the raw string in
 * details.raw_code), non-JSON falls back to `server_error`, and
 * network / fetch rejections are the caller's job (just construct
 * `new ApiError("network_error", "...", 0)`).
 */
import { describe, expect, it } from "vitest";

import { ApiError, apiErrorFromResponse, isApiError } from "../src/api/ApiError";

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("ApiError", () => {
  it("construct preserves code, message, status, details", () => {
    const e = new ApiError("pdf_encrypted", "Password-protected PDF.", 422, { foo: 1 });
    expect(e.code).toBe("pdf_encrypted");
    expect(e.message).toBe("Password-protected PDF.");
    expect(e.status).toBe(422);
    expect(e.details).toEqual({ foo: 1 });
    expect(isApiError(e)).toBe(true);
  });

  it("isApiError returns false for a plain Error", () => {
    expect(isApiError(new Error("x"))).toBe(false);
  });
});

describe("apiErrorFromResponse", () => {
  it("parses a well-formed envelope with a known code", async () => {
    const e = await apiErrorFromResponse(
      jsonRes(422, {
        error: { code: "too_large", message: "File is 21.0 MB.", details: { size_mb: 21 } },
      }),
    );
    expect(e.code).toBe("too_large");
    expect(e.message).toBe("File is 21.0 MB.");
    expect(e.status).toBe(422);
    expect(e.details).toEqual({ size_mb: 21 });
  });

  it("falls back to server_error for an unknown code, puts the raw code in details", async () => {
    const e = await apiErrorFromResponse(
      jsonRes(422, { error: { code: "flux_overflow", message: "???" } }),
    );
    expect(e.code).toBe("server_error");
    expect(e.status).toBe(422);
    expect(e.message).toBe("???");
    expect(e.details?.raw_code).toBe("flux_overflow");
  });

  it("falls back to server_error for non-JSON body (HTML 500)", async () => {
    const htmlRes = new Response("<html><body>Internal Server Error</body></html>", {
      status: 500,
      headers: { "content-type": "text/html" },
    });
    const e = await apiErrorFromResponse(htmlRes);
    expect(e.code).toBe("server_error");
    expect(e.status).toBe(500);
    expect(e.message).toMatch(/unexpected server response/i);
  });

  it("maps a bare HTTP 413 (no envelope) to too_large", async () => {
    // Edge layers / reverse proxies may reject big uploads before
    // the app ever sees them — the body is HTML / empty, status is
    // 413. The parser should still produce a `too_large` code so
    // the UI's size-limit messaging kicks in.
    const emptyRes = new Response("Payload Too Large", {
      status: 413,
      headers: { "content-type": "text/plain" },
    });
    const e = await apiErrorFromResponse(emptyRes);
    expect(e.code).toBe("too_large");
    expect(e.status).toBe(413);
  });

  it("falls back to server_error for JSON that isn't an envelope", async () => {
    const e = await apiErrorFromResponse(jsonRes(503, { oops: true }));
    expect(e.code).toBe("server_error");
    expect(e.status).toBe(503);
  });

  it("network_error is constructed directly by the caller (no Response)", () => {
    // Illustrative: fetch rejection has no Response to parse.
    const e = new ApiError("network_error", "Can't reach the server.", 0);
    expect(e.code).toBe("network_error");
    expect(e.status).toBe(0);
  });
});
