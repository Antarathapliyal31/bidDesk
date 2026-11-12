"""Code-verification of extracted items (SPEC §6.4).

Every quote, quantity, and unit is verified by this module — the
model NEVER decides what's real. Pure functions; easy to test.

Order of operations per item:
  1. Quote verification: normalized quote must be a substring of
     normalized page text (NFKC, lowercase, curly→straight,
     en/em-dash→hyphen, de-hyphenate line breaks, collapse
     whitespace). Pass → `verified=True`, `read_via='text'`;
     fail → `verified=False`, note `quote_not_found`.
  2. Quantity-in-quote: strip commas, parse int/decimal/fractions;
     the parsed number must appear in the raw quote. Miss →
     note `quantity_not_in_quote`.
  3. Unit normalization: `app.catalog.units.normalize_unit`.
     Table hit → OK. Pint-allowlist hit → note
     `unit_pint_convertible`. Miss → note `unknown_unit`.

The SourceRef contract already guards that `verified=True`
requires `read_via='text'` + a non-empty quote + a page; this
module produces values consistent with that.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.agents.analyst.extraction import ItemDraft
from app.agents.analyst.routing import PageExtract
from app.catalog.units import normalize_unit
from app.contracts import ItemKind, ItemView, SourceRef

# ---------------------------------------------------------------------------
# Text normalisation for quote matching (SPEC §6.4.1)
# ---------------------------------------------------------------------------


_DE_HYPHENATE = re.compile(r"-\s*\n\s*")
_WHITESPACE = re.compile(r"\s+")


def _normalize_for_match(s: str) -> str:
    """Collapse whitespace, NFKC-fold, lowercase, swap curly
    quotes for straight, swap en/em-dash for hyphen, de-hyphenate
    line breaks.

    Idempotent: `_normalize_for_match(_normalize_for_match(x)) == _normalize_for_match(x)`.
    """

    # NFKC first — "½" → "1⁄2", "ſ" → "s", etc.
    s = unicodedata.normalize("NFKC", s)
    # Curly quotes → straight
    s = s.translate(str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'}))
    # En/em dash → hyphen
    s = s.replace("–", "-").replace("—", "-")
    # De-hyphenate line breaks: "pres-\nsure" → "pressure"
    s = _DE_HYPHENATE.sub("", s)
    # Collapse whitespace
    s = _WHITESPACE.sub(" ", s)
    return s.strip().lower()


# ---------------------------------------------------------------------------
# Quantity parsing
# ---------------------------------------------------------------------------


_FRACTION_PART = re.compile(r"(\d+)\s*/\s*(\d+)")


def _parse_quantity(text: str) -> float | None:
    """Parse "1,200", "3.5", "1 1/2" to a float. None if unparsable.

    This parses — doesn't validate. The caller decides whether
    zero / negative is acceptable (§6.4.4 says they become
    `unreadable` items)."""

    stripped = text.replace(",", "").strip()
    if not stripped:
        return None
    # "1 1/2" or "1 1 / 2"
    if " " in stripped and "/" in stripped:
        whole, _, frac = stripped.partition(" ")
        try:
            whole_f = float(whole)
            match = _FRACTION_PART.fullmatch(frac.strip())
            if match is None:
                return None
            num, denom = int(match.group(1)), int(match.group(2))
            if denom == 0:
                return None
            return whole_f + num / denom
        except (TypeError, ValueError):
            return None
    # "3/4"
    match = _FRACTION_PART.fullmatch(stripped)
    if match is not None:
        num, denom = int(match.group(1)), int(match.group(2))
        if denom == 0:
            return None
        return num / denom
    # Plain int/decimal
    try:
        return float(stripped)
    except ValueError:
        return None


def _quantity_appears_in_quote(quantity_text: str, quote: str) -> bool:
    """True iff the parsed quantity (as a bare number token)
    shows up in the quote. Loose: we test both the raw string
    and the normalized number (so "1,200" matches "1200")."""

    if not quantity_text:
        return False
    normalized_quote = _normalize_for_match(quote)
    # Match the raw (minus commas) and the integer form.
    bare = quantity_text.replace(",", "").strip()
    if bare and bare.lower() in normalized_quote:
        return True
    parsed = _parse_quantity(quantity_text)
    if parsed is not None:
        if parsed.is_integer() and str(int(parsed)) in normalized_quote:
            return True
        if str(parsed) in normalized_quote:
            return True
    return False


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RejectedClaim:
    """A requirement / conflict that failed verification goes to
    `rejected_claims` (SPEC §10.1 layer 3). Items don't go here
    — they're kept with notes."""

    kind: str
    payload: dict[str, object]
    reason: str


def verify_item(
    draft: ItemDraft,
    *,
    pages_by_no: dict[int, PageExtract],
    item_id: str,
) -> ItemView:
    """Apply §6.4 checks and return a verified `ItemView`.

    Items whose quote fails to verify are KEPT with
    `source.verified=False` + note `quote_not_found` (DECISIONS
    #17, SPEC §6.4). Only requirements / conflicts with
    unverifiable quotes go to `rejected_claims`.
    """

    notes: list[str] = []

    page = pages_by_no.get(draft.source_page)
    read_via = None
    verified = False
    if page is not None:
        read_via = "text" if page.route in ("text", "text+vision") else "vision"
        if read_via == "text":
            normalized_page = _normalize_for_match(page.text)
            normalized_quote = _normalize_for_match(draft.source_quote)
            if normalized_quote and normalized_quote in normalized_page:
                verified = True
            else:
                notes.append("quote_not_found")
        # Vision pages can't be text-verified here; downstream
        # transcription pass would put us back on the text path.
    else:
        notes.append("quote_not_found")

    # Quantity in quote
    if draft.quantity_text and not _quantity_appears_in_quote(
        draft.quantity_text, draft.source_quote
    ):
        notes.append("quantity_not_in_quote")

    # Unit normalisation
    sale_unit = None  # unused here; kept for symmetry with pricing
    if draft.unit_raw:
        normalized = normalize_unit(draft.unit_raw)
        if normalized.unit is None:
            if normalized.pint_convertible:
                notes.append("unit_pint_convertible")
            else:
                notes.append("unknown_unit")
        # When our table hits, no note — the unit is handled downstream.

    source = SourceRef(
        page=draft.source_page,
        quote=draft.source_quote,
        read_via=read_via,
        verified=verified,
    )
    _ = sale_unit

    return ItemView(
        id=item_id,
        kind=ItemKind(draft.kind),
        description=draft.description,
        quantity_text=draft.quantity_text,
        unit_raw=draft.unit_raw,
        source=source,
        notes=notes,
    )


def verify_items(
    drafts: list[ItemDraft],
    *,
    pages: list[PageExtract],
) -> list[ItemView]:
    """Verify every draft. IDs are assigned `item_1..n` in input
    order (document order, per SPEC §6.4.7)."""

    pages_by_no = {p.page_no: p for p in pages}
    return [
        verify_item(draft, pages_by_no=pages_by_no, item_id=f"item_{i + 1}")
        for i, draft in enumerate(drafts)
    ]
