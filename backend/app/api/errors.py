"""FastAPI exception handlers.

Two paths:
  1. `ApiError` subclasses (`contracts/errors.py`): renders the
     canonical envelope `{"error": {code, message, details?}}` at
     the HTTP status the exception carries.
  2. Anything else: logged server-side, rendered through
     `ApiError(ErrorCode.internal_error)` so the body is a
     well-formed envelope with `code="internal_error"` at HTTP
     500. The frontend maps any 500 response to `server_error`
     locally — one coordinated change across SPEC §13 + contracts
     + this handler + frontend. The message is generic and the
     envelope carries no `details`; no traceback/file paths/raw
     exception string ever leaves the server.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.contracts.errors import ApiError, ErrorCode

_logger = logging.getLogger(__name__)


def install_exception_handlers(app: FastAPI) -> None:
    """Wire both handlers on `app`. Call once during setup."""

    @app.exception_handler(ApiError)
    async def _api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=exc.to_envelope())

    @app.exception_handler(Exception)
    async def _unhandled_handler(_request: Request, exc: Exception) -> JSONResponse:
        _logger.exception("unhandled exception in request", exc_info=exc)
        err = ApiError(ErrorCode.internal_error, "Something went wrong on our end.")
        return JSONResponse(status_code=err.http_status, content=err.to_envelope())
