"""Pre-model input guardrails (SPEC §6.1).

Runs before any LLM call and before the DB sees anything. Raises
`ApiError` subclasses so the POST /api/runs endpoint can convert
directly to the right envelope (SPEC §13). Pure, synchronous, and
deterministic — no network, no clock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pdfplumber

from app.contracts.errors import (
    ApiError,
    ErrorCode,
    TextTooLongError,
    TooLargeError,
    TooManyPagesError,
)

_PDF_MAGIC: Final[bytes] = b"%PDF-"
_MB: Final[int] = 1024 * 1024


# ---------------------------------------------------------------------------
# PDF checks
# ---------------------------------------------------------------------------


def check_pdf_magic_bytes(data: bytes) -> None:
    """Reject anything whose first bytes aren't %PDF- (SPEC §6.1).

    Checking magic bytes is cheaper and tighter than trusting the
    file extension — PDF uploads from the browser carry whatever
    MIME the OS attached.
    """

    if not data.startswith(_PDF_MAGIC):
        raise ApiError(
            ErrorCode.pdf_corrupt,
            "This file doesn't look like a PDF (missing %PDF- header).",
        )


def check_pdf_size(data: bytes, *, max_upload_mb: int) -> None:
    """Reject uploads over `max_upload_mb`. Both sides of the
    envelope are rounded up to 0.1 MB for consistent copy."""

    size_mb = len(data) / _MB
    if size_mb > max_upload_mb:
        raise TooLargeError(size_mb=size_mb, limit_mb=float(max_upload_mb))


def check_pdf_page_count(pdf_path: Path, *, max_pages: int) -> int:
    """Open with pdfplumber, count pages, close. Returns the count.

    An encrypted or corrupt PDF raises `ApiError(pdf_encrypted)` /
    `ApiError(pdf_corrupt)` — pdfplumber itself raises a variety of
    exceptions; we map them here so the API handler gets a clean
    typed error.
    """

    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
    except Exception as exc:
        msg = str(exc).lower()
        if "password" in msg or "encrypt" in msg:
            raise ApiError(
                ErrorCode.pdf_encrypted,
                "This PDF is password-protected. Export an unlocked copy and try again.",
            ) from exc
        raise ApiError(
            ErrorCode.pdf_corrupt,
            "Couldn't open this PDF. Try re-exporting it.",
        ) from exc

    if page_count > max_pages:
        raise TooManyPagesError(page_count=page_count, max_pages=max_pages)
    if page_count == 0:
        raise ApiError(ErrorCode.pdf_corrupt, "PDF has zero pages.")
    return page_count


# ---------------------------------------------------------------------------
# Text checks
# ---------------------------------------------------------------------------


def check_text(text: str, *, max_text_chars: int) -> str:
    """Return the trimmed text. Raises `empty_input` when the trim
    collapses to nothing, `text_too_long` when the code-point count
    exceeds the limit."""

    stripped = text.strip()
    if not stripped:
        raise ApiError(ErrorCode.empty_input, "Add a file or paste a list to start a run.")
    # Count code points — matches Python len() and the frontend's
    # `codePointLength` helper. A paste inflated by 4-byte emoji
    # or combining marks will be counted the same both sides.
    count = len(stripped)
    if count > max_text_chars:
        raise TextTooLongError(count=count, limit=max_text_chars)
    return stripped


# ---------------------------------------------------------------------------
# One-shot ingest helper
# ---------------------------------------------------------------------------


def validate_pdf_upload(
    data: bytes,
    stored_path: Path,
    *,
    max_upload_mb: int,
    max_pages: int,
) -> int:
    """Run all PDF checks and return the page count. Order matters:
    magic bytes before anything (reject non-PDF fast), size next
    (don't parse 500 MB), then open-and-count. Callers write
    `data` to disk at `stored_path` after this returns.
    """

    check_pdf_magic_bytes(data)
    check_pdf_size(data, max_upload_mb=max_upload_mb)
    return check_pdf_page_count(stored_path, max_pages=max_pages)
