"""`ApiError` envelope + `ErrorCode` enum + exception classes.

Per SPEC §13 (amended). Every non-2xx response uses the shape
`{"error": {"code": ..., "message": ..., "details?": ...}}`.

- Simple codes (no required details) use `ApiError` directly.
- Codes with required typed details get a subclass whose `__init__`
  forces callers to pass the right fields. This is what prevents an
  endpoint from raising `ApiError("too_large")` without a `size_mb`.
- `network_error` and `server_error` are **client-synthesized only** —
  the backend never raises them (SPEC §13). We still enumerate them in
  `ErrorCode` so the OpenAPI schema is complete.

HTTP status comes from a table keyed on code. The FastAPI exception
handler (Phase 2 step 6) renders the envelope.

**Step 6 TODO — unhandled-exception handler.** Any exception that
isn't an `ApiError` (programming bug, aiosqlite outage, ...) must
produce HTTP 500 with a generic body like `{"error": {"code":
"internal_error", "message": "Something went wrong."}}` and nothing
else — no traceback, no file paths, no stringified exception. The
frontend's `apiErrorFromResponse` maps such a response to
`server_error` locally. Log the traceback server-side; never leak
it over the wire.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    invalid_input = "invalid_input"
    pdf_encrypted = "pdf_encrypted"
    pdf_corrupt = "pdf_corrupt"
    too_large = "too_large"
    too_many_pages = "too_many_pages"
    empty_input = "empty_input"
    text_too_long = "text_too_long"
    unsupported_media = "unsupported_media"
    invalid_branch = "invalid_branch"
    run_not_found = "run_not_found"
    stale_revision = "stale_revision"
    open_flags = "open_flags"
    # Server-emittable catch-all for unhandled exceptions (HTTP 500).
    # The frontend maps ANY 500 response to `server_error` locally
    # (DECISIONS: one coordinated change across SPEC §13 + contracts +
    # http handler + frontend), so the body stays well-formed envelope
    # shape but the UI still routes to the `server_error` branch.
    internal_error = "internal_error"
    # Client-synthesized; the server never raises these.
    network_error = "network_error"
    server_error = "server_error"


# Server-raiseable codes → HTTP status (SPEC §13). `too_large` is 422 by
# default; the few call sites that want 413 raise `TooLargeError(http_status=413)`.
_HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.invalid_input: 422,
    ErrorCode.pdf_encrypted: 422,
    ErrorCode.pdf_corrupt: 422,
    ErrorCode.too_large: 422,
    ErrorCode.too_many_pages: 422,
    ErrorCode.empty_input: 422,
    ErrorCode.text_too_long: 422,
    ErrorCode.unsupported_media: 415,
    ErrorCode.invalid_branch: 422,
    ErrorCode.run_not_found: 404,
    ErrorCode.stale_revision: 409,
    ErrorCode.open_flags: 422,
    ErrorCode.internal_error: 500,
}

# Codes the server is never allowed to raise (SPEC §13). The exception
# base-class rejects construction with these codes so a backend bug
# can't synthesize them even by accident.
_CLIENT_ONLY_CODES: frozenset[ErrorCode] = frozenset(
    {ErrorCode.network_error, ErrorCode.server_error}
)


def _ceil_to_tenth(x: float) -> float:
    """Round UP to one decimal place — matches the frontend's size display
    (`validation.ts: ceilToTenthMb`). Keeps the user-facing number stable
    across client and server so "you sent 20.04 MB" doesn't contradict
    "limit 20.0 MB"."""

    return math.ceil(x * 10) / 10


class ApiError(Exception):
    """Server-side exception that the FastAPI handler maps to the envelope.

    Construct directly for simple codes:
        raise ApiError(ErrorCode.invalid_branch, "No such branch 'BR-ZZ'")

    For codes with required typed details, use the subclass:
        raise TooLargeError(size_mb=42.3, limit_mb=25.0)
    """

    code: ErrorCode
    message: str
    http_status: int
    details: dict[str, Any] | None

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        if code in _CLIENT_ONLY_CODES:
            raise ValueError(
                f"ErrorCode.{code.value} is client-synthesized only; "
                "the server must not raise it (SPEC §13)"
            )
        self.code = code
        self.message = message
        self.details = details
        self.http_status = http_status if http_status is not None else _HTTP_STATUS[code]
        super().__init__(message)

    def to_envelope(self) -> dict[str, Any]:
        """Render as the SPEC §13 shape."""

        body: dict[str, Any] = {"code": self.code.value, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return {"error": body}


# ---------------------------------------------------------------------------
# Typed subclasses — one per code with required details
# ---------------------------------------------------------------------------


class TooLargeError(ApiError):
    """`details = {size_mb, limit_mb}`. `size_mb` is ceil-rounded to 0.1 MB
    so the client and server agree on the display number (frontend's
    `ceilToTenthMb` does the same). Default 422; pass `http_status=413`
    for bare HTTP 413 passthrough scenarios."""

    def __init__(
        self,
        *,
        size_mb: float,
        limit_mb: float,
        message: str | None = None,
        http_status: int | None = None,
    ) -> None:
        rounded_size = _ceil_to_tenth(size_mb)
        rounded_limit = _ceil_to_tenth(limit_mb)
        super().__init__(
            ErrorCode.too_large,
            message or f"Upload is {rounded_size:.1f} MB (limit {rounded_limit:.1f} MB).",
            details={"size_mb": rounded_size, "limit_mb": rounded_limit},
            http_status=http_status,
        )


class TooManyPagesError(ApiError):
    """`details = {page_count, max_pages}`."""

    def __init__(
        self,
        *,
        page_count: int,
        max_pages: int,
        message: str | None = None,
    ) -> None:
        super().__init__(
            ErrorCode.too_many_pages,
            message or f"PDF has {page_count} pages (limit {max_pages}).",
            details={"page_count": page_count, "max_pages": max_pages},
        )


class TextTooLongError(ApiError):
    """`details = {count, limit}`; code-point counts (matches Python `len`)."""

    def __init__(self, *, count: int, limit: int, message: str | None = None) -> None:
        super().__init__(
            ErrorCode.text_too_long,
            message or f"Pasted text is {count} characters (limit {limit}).",
            details={"count": count, "limit": limit},
        )


class StaleRevisionError(ApiError):
    """`details.current` carries the server's current Bid so the UI can
    reconcile. `current` is kept as a dict here so this file doesn't
    need to import Bid (avoids an import cycle via bid.py); the caller
    passes `bid.model_dump(mode='json')`."""

    def __init__(self, *, current: dict[str, Any], message: str | None = None) -> None:
        super().__init__(
            ErrorCode.stale_revision,
            message or "Draft revision is stale; reload and try again.",
            details={"current": current},
        )


class OpenFlagsError(ApiError):
    """`details.open_line_ids` — the lines the UI must resolve before finalize."""

    def __init__(self, *, open_line_ids: list[str], message: str | None = None) -> None:
        super().__init__(
            ErrorCode.open_flags,
            message or f"Cannot finalize: {len(open_line_ids)} line(s) still open for review.",
            details={"open_line_ids": list(open_line_ids)},
        )
