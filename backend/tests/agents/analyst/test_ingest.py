"""Ingest guardrails: magic bytes, size, page count, text length."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.analyst.ingest import (
    check_pdf_magic_bytes,
    check_pdf_page_count,
    check_pdf_size,
    check_text,
    validate_pdf_upload,
)
from app.contracts.errors import ApiError, ErrorCode

_SAMPLES_ROOT = Path(__file__).resolve().parents[3].parent / "samples"


class TestPdfMagicBytes:
    def test_valid_pdf_passes(self) -> None:
        check_pdf_magic_bytes(b"%PDF-1.7\nrest of file")

    def test_html_bytes_rejected(self) -> None:
        with pytest.raises(ApiError) as exc:
            check_pdf_magic_bytes(b"<html>")
        assert exc.value.code == ErrorCode.pdf_corrupt

    def test_empty_bytes_rejected(self) -> None:
        with pytest.raises(ApiError) as exc:
            check_pdf_magic_bytes(b"")
        assert exc.value.code == ErrorCode.pdf_corrupt


class TestSize:
    def test_within_limit(self) -> None:
        check_pdf_size(b"x" * (1024 * 1024), max_upload_mb=5)  # 1 MB vs 5 MB

    def test_over_limit_raises_too_large(self) -> None:
        with pytest.raises(ApiError) as exc:
            # 6 MB when limit is 5.
            check_pdf_size(b"x" * (6 * 1024 * 1024), max_upload_mb=5)
        assert exc.value.code == ErrorCode.too_large


class TestPageCount:
    def test_counts_pages_of_sample(self) -> None:
        pdf = _SAMPLES_ROOT / "rfp_clean.pdf"
        assert pdf.exists()
        count = check_pdf_page_count(pdf, max_pages=60)
        assert count >= 1  # sample has multiple pages

    def test_over_limit_raises_too_many_pages(self) -> None:
        pdf = _SAMPLES_ROOT / "rfp_messy.pdf"  # multi-page sample
        with pytest.raises(ApiError) as exc:
            check_pdf_page_count(pdf, max_pages=1)
        assert exc.value.code == ErrorCode.too_many_pages

    def test_non_pdf_bytes_at_path_raises_pdf_corrupt(self, tmp_path: Path) -> None:
        fake = tmp_path / "fake.pdf"
        fake.write_bytes(b"not a pdf at all")
        with pytest.raises(ApiError) as exc:
            check_pdf_page_count(fake, max_pages=60)
        assert exc.value.code == ErrorCode.pdf_corrupt


class TestText:
    def test_valid_text_returns_trimmed(self) -> None:
        assert check_text("  hello  ", max_text_chars=100) == "hello"

    def test_empty_raises(self) -> None:
        with pytest.raises(ApiError) as exc:
            check_text("    \n\t  ", max_text_chars=100)
        assert exc.value.code == ErrorCode.empty_input

    def test_too_long_raises(self) -> None:
        with pytest.raises(ApiError) as exc:
            check_text("x" * 200, max_text_chars=100)
        assert exc.value.code == ErrorCode.text_too_long


class TestValidatePdfUpload:
    def test_sample_rfp_clean_validates(self, tmp_path: Path) -> None:
        pdf = _SAMPLES_ROOT / "rfp_clean.pdf"
        data = pdf.read_bytes()
        count = validate_pdf_upload(data, pdf, max_upload_mb=20, max_pages=60)
        assert count >= 1
