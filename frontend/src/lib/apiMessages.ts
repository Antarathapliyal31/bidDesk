import { isApiError, type ApiError } from "@/api";

import { MAX_PAGES, MAX_UPLOAD_MB } from "./limits";

/**
 * Human-readable messages for every ApiError code. One map shared
 * between pages so the error wording stays consistent.
 *
 * `too_large` / `too_many_pages` use the actual values in
 * `err.details` (`size_mb`, `page_count`) when available, with a
 * fallback to the client-side maxima from `limits.ts`. See the
 * details-schema note in `docs/API_CONTRACT.md`.
 *
 * `invalid_input` prefers the server's own `message` — the backend's
 * catch-all can describe the problem better than a static string.
 *
 * Any non-ApiError error is logged to the console and reported as a
 * generic phrase; the caller still sees an error, we just don't
 * swallow the stack trace.
 */
export const API_ERROR_MESSAGES = {
  pdf_encrypted: "This PDF is password-protected. Export an unlocked copy and try again.",
  pdf_corrupt: "Couldn't read this PDF. Try re-exporting it.",
  too_large: `That file is over the ${MAX_UPLOAD_MB} MB limit.`,
  too_many_pages: `That PDF has more than ${MAX_PAGES} pages.`,
  empty_input: "Add a file or paste a list to start a run.",
  text_too_long: "Shorten the paste or upload a PDF.",
  unsupported_media: "Only PDF files are accepted.",
  invalid_branch: "That branch doesn't exist anymore — pick another.",
  invalid_input: "The server rejected the input. Check the file and try again.",
  run_not_found: "That run doesn't exist anymore.",
  stale_revision: "This bid changed somewhere else. Reload and try again.",
  open_flags: "There are review flags open. Resolve them before finalizing.",
  // `internal_error` is the backend's HTTP 500 code (SPEC §13) —
  // surfaces when a 500 response carries a well-formed envelope;
  // bare 500s fall through to `server_error`.
  internal_error: "The server hit an error handling that request. Try again.",
  network_error: "Can't reach the server — check your connection.",
  server_error: "Something went wrong on our side. Try again in a moment.",
} as const;

function tooLargeMessage(err: ApiError): string {
  const sizeMb = typeof err.details?.size_mb === "number" ? err.details.size_mb : null;
  // Prefer the server's current limit (details.limit_mb) over the
  // client constant — they usually match, but details wins if a
  // deploy raised the ceiling without a client bump.
  const limitMb =
    typeof err.details?.limit_mb === "number" ? err.details.limit_mb : MAX_UPLOAD_MB;
  if (sizeMb !== null) {
    return `This file is ${sizeMb} MB — the maximum is ${limitMb} MB.`;
  }
  return `That file is over the ${limitMb} MB limit.`;
}

function tooManyPagesMessage(err: ApiError): string {
  const n = typeof err.details?.page_count === "number" ? err.details.page_count : null;
  const limit =
    typeof err.details?.max_pages === "number" ? err.details.max_pages : MAX_PAGES;
  if (n !== null) {
    return `This PDF has ${n} pages — the maximum is ${limit}.`;
  }
  return `That PDF has more than ${limit} pages.`;
}

function textTooLongMessage(err: ApiError): string {
  const count = typeof err.details?.count === "number" ? err.details.count : null;
  const limit =
    typeof err.details?.limit === "number" ? err.details.limit : null;
  if (count !== null && limit !== null) {
    return `Paste is ${count.toLocaleString()} characters — the maximum is ${limit.toLocaleString()}. Shorten the list or upload a PDF.`;
  }
  return API_ERROR_MESSAGES.text_too_long;
}

export function messageForError(err: unknown): string {
  if (!isApiError(err)) {
    // Preserve the stack trace for console inspection; the user
    // still sees a clear-but-generic phrase.
    console.error(err);
    return "Something went wrong.";
  }
  switch (err.code) {
    case "too_large":
      return tooLargeMessage(err);
    case "too_many_pages":
      return tooManyPagesMessage(err);
    case "text_too_long":
      return textTooLongMessage(err);
    case "invalid_input":
      // Server knows the specific reason — prefer its message when
      // present, fall back to the generic.
      return err.message || API_ERROR_MESSAGES.invalid_input;
    default:
      return API_ERROR_MESSAGES[err.code] ?? err.message ?? "Something went wrong.";
  }
}
