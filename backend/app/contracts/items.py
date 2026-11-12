"""`ItemView` — the payload of `item.extracted` on the SSE stream.

Per SPEC §6.4 / §10.3. The document analyst emits one per extracted
line; the orchestrator reads these, triages, and attaches requirements.

**SPEC §6.4 wins** (DECISIONS #17): an item whose quote can't be
text-verified is KEPT with `source.verified=false` + note
`quote_not_found`. The bid-assembly path turns these into open-flag
lines (review reason `unverified`) so the estimator sees them. Only
*requirements* and *conflicts* that would rest on an unverifiable
quote go to `rejected_claims`.

`notes` vocabulary:
  - `quote_not_found` — the item's source.quote didn't verify against
    page text (analyst flagged for review, line is still kept).
  - `quantity_not_in_quote` — quantity field doesn't appear in the
    matched quote.
  - `unknown_unit` — unit_raw didn't normalise through our table or
    the pint allowlist.
  - `unit_pint_convertible` — unit normalised via the pint fallback,
    not our table.
  - `read_via_vision` — the item was extracted from a page routed
    `vision` or `text+vision`; the quote was not byte-matched to a
    text layer, so `source.verified` is always False and the line
    surfaces as `unverified` for estimator review (DECISIONS #26).
    Distinct from `quote_not_found`: a vision item isn't "quote
    missing from text" — it's "no trustworthy text to match against".
  - `invalid_spec:<field>` — a specific extracted field failed its
    own validation (e.g. `invalid_spec:length_ft`); `<field>` is a
    lowercase identifier matching `[a-z_][a-z0-9_]*`.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, model_validator

from .common import Contract, ItemKind, SourceRef

_ITEM_NOTE_RE = re.compile(
    r"^(quote_not_found|quantity_not_in_quote|unknown_unit|unit_pint_convertible"
    r"|read_via_vision"
    r"|invalid_spec:[a-z_][a-z0-9_]*)$"
)


def _validate_item_note(v: str) -> str:
    if not _ITEM_NOTE_RE.match(v):
        raise ValueError(
            f"unknown ItemNote: {v!r}. Allowed: quote_not_found, "
            "quantity_not_in_quote, unknown_unit, unit_pint_convertible, "
            "read_via_vision, "
            "or invalid_spec:<field> with <field> matching [a-z_][a-z0-9_]*"
        )
    return v


ItemNote = Annotated[str, AfterValidator(_validate_item_note)]


class ItemView(Contract):
    id: str
    kind: ItemKind
    description: str
    quantity_text: str | None
    unit_raw: str | None
    source: SourceRef
    notes: list[ItemNote]

    @model_validator(mode="after")
    def _invariants(self) -> ItemView:
        # The 'quote_not_found' note promises that source.verified is False —
        # a verified SourceRef by definition did find its quote.
        if "quote_not_found" in self.notes and self.source.verified:
            raise ValueError(
                f"ItemView[{self.id!r}]: note 'quote_not_found' is incompatible "
                f"with source.verified=True"
            )
        # 'read_via_vision' is the companion note to a vision read.
        # SourceRef already forbids verified=True on vision; this
        # catches the inverse: claiming a vision read on a line
        # whose source wasn't actually read via vision.
        if "read_via_vision" in self.notes and self.source.read_via != "vision":
            raise ValueError(
                f"ItemView[{self.id!r}]: note 'read_via_vision' requires "
                f"source.read_via='vision' (got {self.source.read_via!r})"
            )
        return self
