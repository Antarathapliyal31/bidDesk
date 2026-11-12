"""Page routing: text vs vision vs text+vision."""

from __future__ import annotations

from pathlib import Path

from app.agents.analyst.routing import (
    MIN_TEXT_CHARS,
    route_pasted_text,
    route_pdf,
)

_SAMPLES_ROOT = Path(__file__).resolve().parents[3].parent / "samples"


class TestRouteClean:
    def test_clean_rfp_all_text(self) -> None:
        pages = route_pdf(_SAMPLES_ROOT / "rfp_clean.pdf")
        assert pages, "sample PDF should have pages"
        assert all(p.route in ("text", "text+vision") for p in pages)
        assert all(p.text_source == "pdf_text_layer" for p in pages)

    def test_scanned_rfp_routes_to_vision(self) -> None:
        # rfp_scanned.pdf is the rasterised sample from Phase 1.
        pages = route_pdf(_SAMPLES_ROOT / "rfp_scanned.pdf")
        assert pages, "scanned sample should have pages"
        # Scanned pages have no text layer → route=vision.
        assert any(p.route == "vision" for p in pages)
        assert all(p.text_source == "vision_transcription" for p in pages if p.route == "vision")


class TestRoutePastedText:
    def test_long_text_routes_as_single_text_page(self) -> None:
        page = route_pasted_text("x" * (MIN_TEXT_CHARS + 50))
        assert page.page_no == 1
        assert page.route == "text"
        assert page.text_source == "pdf_text_layer"
        assert page.table_issue is False
