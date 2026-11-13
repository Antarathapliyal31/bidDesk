"""FastAPI exception handlers.

Three paths:
  1. `ApiError` subclasses (`contracts/errors.py`): renders the
     canonical envelope `{"error": {code, message, details?}}` at
     the HTTP status the exception carries.
  2. FastAPI's own `RequestValidationError` (body/path/query didn't
     match the Pydantic model): rendered as the SPEC §13 envelope
     with `code="invalid_input"` and `details.ops` carrying the
     per-op errors so the frontend can surface a usable message
     (SPEC §13 pins the envelope shape for every non-2xx — the
     raw FastAPI `detail` array leaks through otherwise).
  3. Anything else: logged server-side, rendered through
     `ApiError(ErrorCode.internal_error)` so the body is a
     well-formed envelope with `code="internal_error"` at HTTP
     500. The message is generic and the envelope carries no
     `details`; no traceback/file paths/raw exception string ever
     leaves the server.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.contracts.errors import ApiError, ErrorCode

_logger = logging.getLogger(__name__)


def _per_op_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    """Group `RequestValidationError.errors()` entries that target
    `body.ops[<i>]` (any depth beneath) into one `{index, error}`
    dict per index. Non-op errors (missing `draft_revision`, etc.)
    land under `index: null` so the client still sees them."""

    buckets: dict[int | None, list[str]] = {}
    for err in exc.errors():
        loc = tuple(err.get("loc") or ())
        msg = str(err.get("msg") or "invalid input")
        idx: int | None = None
        for i, part in enumerate(loc):
            if isinstance(part, int) and not isinstance(part, bool):
                idx = int(part)
                tail = loc[i + 1 :]
                msg = (
                    f"{'.'.join(str(p) for p in tail)}: {msg}" if tail else msg
                )
                break
        buckets.setdefault(idx, []).append(msg)

    out: list[dict[str, Any]] = []
    for idx in sorted(buckets.keys(), key=lambda k: (k is None, k if k is not None else -1)):
        out.append({"index": idx, "error": "; ".join(buckets[idx])})
    return out


def install_exception_handlers(app: FastAPI) -> None:
    """Wire all three handlers on `app`. Call once during setup."""

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_envelope())

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        ops = _per_op_errors(exc)
        # First line of the first error becomes the human message;
        # the full breakdown lives under `details.ops`.
        headline = ops[0]["error"] if ops else "request body failed validation"
        err = ApiError(
            ErrorCode.invalid_input,
            headline,
            details={"ops": ops},
        )
        return JSONResponse(status_code=err.http_status, content=err.to_envelope())

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        _logger.exception("unhandled exception in request", exc_info=exc)
        err = ApiError(ErrorCode.internal_error, "Something went wrong on our end.")
        return JSONResponse(status_code=err.http_status, content=err.to_envelope())
