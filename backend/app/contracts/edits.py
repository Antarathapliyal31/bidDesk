"""Edit-op contracts (SPEC §9.4, API_CONTRACT `EditOp`).

Discriminated union on `op` — snake_case per the Phase-6 contract
(`set_qty` / `accept_line` / `delete_line` / `set_unit_price` /
`set_sku` / `set_description` / `add_line` / `apply_proposal` /
`dismiss_proposal`). Pydantic validates each op at the API
boundary; `bid/edits.py` is pure and applies them over a `Bid`.

Strictness choices:
  - `extra='forbid'` so an unknown field in a client's op blows
    up with 422 instead of silently being accepted;
  - `set_unit_price.override_reason` is non-empty-string — SPEC
    §9.4 pins the audit requirement, and the API returns 422
    when it's missing/empty;
  - `sale_qty` arrives as a string (Decimal-as-string per
    SPEC §9.1) and is parsed in the applier.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _OpBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SetQty(_OpBase):
    op: Literal["set_qty"]
    line_id: str
    sale_qty: str = Field(min_length=1)  # Decimal-as-string; parsed in applier


class AcceptLine(_OpBase):
    op: Literal["accept_line"]
    line_id: str


class DeleteLine(_OpBase):
    op: Literal["delete_line"]
    line_id: str


class SetUnitPrice(_OpBase):
    op: Literal["set_unit_price"]
    line_id: str
    unit_price_cents: int = Field(ge=0)
    # SPEC §9.4: override reason is REQUIRED and non-empty.
    override_reason: str = Field(min_length=1, max_length=200)


class SetSku(_OpBase):
    op: Literal["set_sku"]
    line_id: str
    sku: str = Field(min_length=1)


class SetDescription(_OpBase):
    op: Literal["set_description"]
    line_id: str
    description: str = Field(min_length=1, max_length=300)


class AddLine(_OpBase):
    op: Literal["add_line"]
    description: str = Field(min_length=1, max_length=300)
    sale_qty: str = Field(min_length=1)  # Decimal-as-string
    sale_unit: str = Field(min_length=1)
    unit_price_cents: int = Field(ge=0)


class ApplyProposal(_OpBase):
    op: Literal["apply_proposal"]
    line_id: str
    proposal_id: str


class DismissProposal(_OpBase):
    op: Literal["dismiss_proposal"]
    line_id: str
    proposal_id: str


EditOp = Annotated[
    SetQty
    | AcceptLine
    | DeleteLine
    | SetUnitPrice
    | SetSku
    | SetDescription
    | AddLine
    | ApplyProposal
    | DismissProposal,
    Field(discriminator="op"),
]


class EditRequest(BaseModel):
    """Body of `PATCH /api/runs/{id}/bid`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    draft_revision: int = Field(ge=0)
    ops: list[EditOp] = Field(min_length=1, max_length=100)
