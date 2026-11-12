/**
 * The error envelope returned by the backend (SPEC §13,
 * docs/API_CONTRACT.md). Hooks reject with `ApiError` instances so
 * callers can branch on `error.code` to surface a human-readable
 * message and so retry logic has something structured to act on.
 *
 * `status` is the HTTP status: 0 for transport failures (fetch
 * threw; no response arrived). `network_error` is the code for that
 * case; `server_error` is the catch-all for anything else that
 * wasn't a well-formed envelope (HTML 500 pages, unknown codes).
 *
 * `internal_error` is the SERVER-emitted code on HTTP 500 (added to
 * SPEC §13 in Phase 2). It's listed here for cross-layer parity
 * with the backend contract, but `apiErrorFromResponse` folds any
 * HTTP 500 into `server_error` locally so the UI only ever needs
 * one 500-shaped error state — the backend's `internal_error` body
 * is captured in `details.raw_code` for logs.
 */
export type ApiErrorCode =
  | "invalid_input"
  | "pdf_encrypted"
  | "pdf_corrupt"
  | "too_large"
  | "too_many_pages"
  | "empty_input"
  | "text_too_long"
  | "unsupported_media"
  | "invalid_branch"
  | "run_not_found"
  | "stale_revision"
  | "open_flags"
  | "internal_error"
  | "network_error"
  | "server_error"
  // SPEC §13 / Phase 8 review: the backend may surface an
  // `internal_error` for unexpected 5xx cases that DO carry an
  // envelope (vs `server_error` which is the client's fallback
  // when the body isn't a well-formed envelope). Distinct codes
  // make incident triage easier — a flood of `internal_error`
  // means the server is throwing; a flood of `server_error`
  // means something upstream is stripping envelopes.
  | "internal_error";

const KNOWN_CODES = new Set<ApiErrorCode>([
  "invalid_input",
  "pdf_encrypted",
  "pdf_corrupt",
  "too_large",
  "too_many_pages",
  "empty_input",
  "text_too_long",
  "unsupported_media",
  "invalid_branch",
  "run_not_found",
  "stale_revision",
  "open_flags",
  "internal_error",
  "network_error",
  "server_error",
  "internal_error",
]);

export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status: number;
  readonly details: Record<string, unknown> | undefined;
  constructor(
    code: ApiErrorCode,
    message: string,
    status: number,
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError;
}

/**
 * Parse an unhappy `fetch` response into an `ApiError`. Pass the
 * Response directly — this function reads it exactly once and
 * handles:
 *   - well-formed `{ error: { code, message, details? } }` envelope
 *     with a known code → that code, response.status, details.
 *   - envelope with an unknown code → `server_error`, with the
 *     raw string in `details.raw_code`.
 *   - non-JSON body (HTML 500, bare text) → `server_error`.
 *   - JSON that isn't an error envelope → `server_error`.
 *
 * For `fetch` rejections (no Response), callers should construct
 * an `ApiError("network_error", "...", 0)` directly.
 */
export async function apiErrorFromResponse(res: Response): Promise<ApiError> {
  const status = res.status;
  let body: unknown;
  let parsedOk = true;
  try {
    body = await res.json();
  } catch {
    parsedOk = false;
  }

  // Any HTTP 500 → server_error locally, regardless of the
  // backend's envelope code (SPEC §13 coordinated change: the
  // server emits `internal_error` on 500; the UI collapses that
  // into the one 500-shaped error state). `details.raw_code`
  // preserves the backend code for logs so a mis-mapping is
  // still diagnosable.
  if (status === 500) {
    let rawCode: string | null = null;
    let message = "Something went wrong on our end.";
    if (
      parsedOk &&
      body &&
      typeof body === "object" &&
      "error" in body &&
      body.error &&
      typeof body.error === "object"
    ) {
      const env = (body as { error: unknown }).error as {
        code?: unknown;
        message?: unknown;
      };
      if (typeof env.code === "string") rawCode = env.code;
      if (typeof env.message === "string") message = env.message;
    }
    return new ApiError("server_error", message, status, { raw_code: rawCode });
  }

  if (!parsedOk) {
    // Bare HTTP 413 (Payload Too Large) — common from edge
    // layers / reverse proxies that reject the request before
    // the app ever sees it, so there's no envelope.
    if (status === 413) {
      return new ApiError(
        "too_large",
        "This file is over the server's size limit.",
        status,
      );
    }
    // Any other no-envelope response, including bare HTTP 500 from
    // an upstream proxy, maps to `server_error`. (Well-formed 5xx
    // envelopes from the app go through the known-code path below
    // and surface as `internal_error` when the server names it.)
    return new ApiError("server_error", "Unexpected server response.", status);
  }
  if (
    body &&
    typeof body === "object" &&
    "error" in body &&
    body.error &&
    typeof body.error === "object"
  ) {
    const env = (body as { error: unknown }).error as {
      code?: unknown;
      message?: unknown;
      details?: unknown;
    };
    const rawCode = typeof env.code === "string" ? env.code : undefined;
    const message = typeof env.message === "string" ? env.message : "Something went wrong.";
    const details =
      env.details && typeof env.details === "object"
        ? (env.details as Record<string, unknown>)
        : undefined;
    if (rawCode && KNOWN_CODES.has(rawCode as ApiErrorCode)) {
      return new ApiError(rawCode as ApiErrorCode, message, status, details);
    }
    return new ApiError("server_error", message, status, {
      ...(details ?? {}),
      raw_code: rawCode ?? null,
    });
  }
  return new ApiError("server_error", "Unexpected server response.", status);
}
