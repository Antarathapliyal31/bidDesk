"""Verify: quote match, quantity-in-quote, unit normalization."""

from __future__ import annotations

from app.agents.analyst.extraction import ItemDraft
from app.agents.analyst.routing import PageExtract
from app.agents.analyst.verify import verify_items


def _page(text: str) -> PageExtract:
    return PageExtract(
        page_no=1,
        text=text,
        route="text",
        text_source="pdf_text_layer",
        table_issue=False,
    )


class TestQuoteVerification:
    def test_exact_match_verified_text(self) -> None:
        page = _page("Section 06 10 00 — Rough Carpentry\n(40) 2x6x16 PT #2")
        drafts = [
            ItemDraft(
                kind="material",
                description="2x6x16 PT",
                quantity_text="40",
                unit_raw="EA",
                source_page=1,
                source_quote="(40) 2x6x16 PT #2",
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert items[0].source.verified is True
        assert items[0].source.read_via == "text"
        assert "quote_not_found" not in items[0].notes

    def test_normalized_match_handles_curly_quotes_and_hyphens(self) -> None:
        page = _page("4 squares of 3-tab shingles")  # ASCII hyphen
        drafts = [
            ItemDraft(
                kind="material",
                description="3-tab shingles",
                quantity_text="4",
                unit_raw="SQ",
                source_page=1,
                source_quote="4 squares of 3–tab shingles",  # en-dash
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert items[0].source.verified is True

    def test_miss_marks_quote_not_found_and_unverified(self) -> None:
        page = _page("some other content")
        drafts = [
            ItemDraft(
                kind="material",
                description="2x6",
                quantity_text="40",
                unit_raw="EA",
                source_page=1,
                source_quote="(40) 2x6x16 PT #2",
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert items[0].source.verified is False
        assert "quote_not_found" in items[0].notes


class TestQuantityInQuote:
    def test_quantity_present_no_note(self) -> None:
        page = _page("(40) 2x6x16 PT")
        drafts = [
            ItemDraft(
                kind="material",
                description="xx",
                quantity_text="40",
                unit_raw="EA",
                source_page=1,
                source_quote="(40) 2x6x16 PT",
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert "quantity_not_in_quote" not in items[0].notes

    def test_quantity_absent_marks_quantity_not_in_quote(self) -> None:
        page = _page("2x6x16 PT (quantity TBD)")
        drafts = [
            ItemDraft(
                kind="material",
                description="xx",
                quantity_text="40",
                unit_raw="EA",
                source_page=1,
                source_quote="2x6x16 PT (quantity TBD)",
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert "quantity_not_in_quote" in items[0].notes

    def test_quantity_with_commas_normalizes(self) -> None:
        page = _page("1200 SF roofing")
        drafts = [
            ItemDraft(
                kind="material",
                description="xx",
                quantity_text="1,200",
                unit_raw="SF",
                source_page=1,
                source_quote="1200 SF roofing",
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert "quantity_not_in_quote" not in items[0].notes


class TestUnitNormalization:
    def test_table_unit_no_note(self) -> None:
        page = _page("40 EA 2x6x16 PT")
        drafts = [
            ItemDraft(
                kind="material",
                description="xx",
                quantity_text="40",
                unit_raw="EA",
                source_page=1,
                source_quote="40 EA 2x6x16 PT",
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert all("unit" not in n for n in items[0].notes)

    def test_unknown_unit_marks_unknown_unit(self) -> None:
        page = _page("40 PINGPONG 2x6")
        drafts = [
            ItemDraft(
                kind="material",
                description="xx",
                quantity_text="40",
                unit_raw="pingpong",
                source_page=1,
                source_quote="40 PINGPONG 2x6",
            )
        ]
        items = verify_items(drafts, pages=[page])
        assert "unknown_unit" in items[0].notes


class TestIdAssignment:
    def test_ids_are_item_1_through_n_in_order(self) -> None:
        page = _page("line one; line two; line three")
        drafts = [
            ItemDraft(
                kind="material",
                description="AA",
                quantity_text="1",
                unit_raw="EA",
                source_page=1,
                source_quote="line one",
            ),
            ItemDraft(
                kind="material",
                description="BB",
                quantity_text="1",
                unit_raw="EA",
                source_page=1,
                source_quote="line two",
            ),
            ItemDraft(
                kind="material",
                description="CC",
                quantity_text="1",
                unit_raw="EA",
                source_page=1,
                source_quote="line three",
            ),
        ]
        items = verify_items(drafts, pages=[page])
        assert [i.id for i in items] == ["item_1", "item_2", "item_3"]
