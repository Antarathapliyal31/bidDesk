// @vitest-environment node
/**
 * `messageForError` is the single place a UI copy-writer touches
 * when an error hits the user. The tests pin:
 *   - too_large / too_many_pages interpolate details.size_mb /
 *     details.page_count, with fallback to the static message.
 *   - invalid_input prefers the server's own message.
 *   - non-ApiError → generic phrase + console.error.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../src/api/ApiError";
import { API_ERROR_MESSAGES, messageForError } from "../src/lib/apiMessages";
import { MAX_PAGES, MAX_UPLOAD_MB } from "../src/lib/limits";

afterEach(() => vi.restoreAllMocks());

describe("messageForError", () => {
  it("too_large: interpolates size_mb from details and names the limit (server limit preferred)", () => {
    const e = new ApiError("too_large", "file over limit", 422, { size_mb: 21.3, limit_mb: 25 });
    const msg = messageForError(e);
    expect(msg).toContain("21.3 MB");
    // details.limit_mb wins over the client constant.
    expect(msg).toContain("25 MB");
  });

  it("too_large: falls back to the client MAX_UPLOAD_MB when limit_mb is missing", () => {
    const e = new ApiError("too_large", "x", 422, { size_mb: 21.3 });
    const msg = messageForError(e);
    expect(msg).toContain("21.3 MB");
    expect(msg).toContain(`${MAX_UPLOAD_MB} MB`);
  });

  it("too_large: static-ish fallback (still uses the limit) when size_mb is missing", () => {
    const e = new ApiError("too_large", "x", 422);
    expect(messageForError(e)).toContain(`${MAX_UPLOAD_MB} MB`);
  });

  it("text_too_long: interpolates count + limit from details", () => {
    const e = new ApiError("text_too_long", "too long", 422, { count: 55_123, limit: 50_000 });
    const msg = messageForError(e);
    expect(msg).toContain("55,123");
    expect(msg).toContain("50,000");
  });

  it("text_too_long: falls back to the static message when details are missing", () => {
    const e = new ApiError("text_too_long", "x", 422);
    expect(messageForError(e)).toBe(API_ERROR_MESSAGES.text_too_long);
  });

  it("too_many_pages: interpolates page_count + max_pages (server limit preferred)", () => {
    const e = new ApiError("too_many_pages", "too many", 422, {
      page_count: 128,
      max_pages: 75,
    });
    const msg = messageForError(e);
    expect(msg).toContain("128 pages");
    expect(msg).toContain("maximum is 75");
  });

  it("too_many_pages: falls back to client MAX_PAGES when max_pages is missing", () => {
    const e = new ApiError("too_many_pages", "too many", 422, { page_count: 128 });
    const msg = messageForError(e);
    expect(msg).toContain("128 pages");
    expect(msg).toContain(`maximum is ${MAX_PAGES}`);
  });

  it("too_many_pages: fallback message still names the client limit when page_count is missing", () => {
    const e = new ApiError("too_many_pages", "x", 422);
    expect(messageForError(e)).toContain(`${MAX_PAGES} pages`);
  });

  it("invalid_input: prefers the server's own message when present", () => {
    const e = new ApiError("invalid_input", "branch_id is required", 422);
    expect(messageForError(e)).toBe("branch_id is required");
  });

  it("invalid_input: falls back to the static message when server omits one", () => {
    // ApiError requires a message, so simulate an empty one.
    const e = new ApiError("invalid_input", "", 422);
    expect(messageForError(e)).toBe(API_ERROR_MESSAGES.invalid_input);
  });

  it("network_error / server_error map to their static messages", () => {
    expect(messageForError(new ApiError("network_error", "x", 0))).toBe(
      API_ERROR_MESSAGES.network_error,
    );
    expect(messageForError(new ApiError("server_error", "x", 500))).toBe(
      API_ERROR_MESSAGES.server_error,
    );
  });

  it("non-ApiError: logs the error + returns a generic phrase", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    const raw = new Error("TypeError: cannot read of undefined");
    expect(messageForError(raw)).toBe("Something went wrong.");
    expect(spy).toHaveBeenCalledWith(raw);
  });

  it("non-Error values are also logged and get the generic phrase", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(messageForError("surprise string")).toBe("Something went wrong.");
    expect(spy).toHaveBeenCalledWith("surprise string");
  });
});
