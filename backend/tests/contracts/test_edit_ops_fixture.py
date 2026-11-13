"""Contract test: every entry in `docs/edit_op_fixtures.json`
parses through the real `EditRequest` Pydantic model.

Pairs with `frontend/tests/editOpContract.test.ts` which asserts
the TS discriminator + required fields on the same file. Keeps
the two sides from drifting (DECISIONS F13 — the real bug that
caused the UI's 422 loop was `set_quantity` vs `set_qty`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.contracts.edits import EditRequest

_FIXTURE_PATH = Path(__file__).resolve().parents[3] / "docs" / "edit_op_fixtures.json"


def _load_ops() -> list[dict]:
    with _FIXTURE_PATH.open() as f:
        data = json.load(f)
    return data["ops"]


@pytest.mark.parametrize(
    "entry",
    _load_ops(),
    ids=lambda e: e["name"],
)
def test_each_op_variant_parses(entry: dict) -> None:
    req = EditRequest.model_validate(
        {"draft_revision": 0, "ops": [entry["body"]]}
    )
    assert req.draft_revision == 0
    assert len(req.ops) == 1
    assert req.ops[0].op == entry["body"]["op"]


def test_fixture_file_covers_every_discriminator() -> None:
    """Every op literal in the EditOp union must have at least
    one fixture entry. Catches a new op shipping without a fixture."""

    from typing import get_args

    from app.contracts.edits import EditOp  # type: ignore[attr-defined]

    # EditOp is `Annotated[Union[...], Field(discriminator="op")]`.
    # The Union members are at get_args(EditOp)[0].
    union_members = get_args(get_args(EditOp)[0])
    expected_ops = {
        get_args(member.model_fields["op"].annotation)[0]  # Literal[...] inner
        for member in union_members
    }
    fixture_ops = {entry["body"]["op"] for entry in _load_ops()}
    missing = expected_ops - fixture_ops
    assert not missing, f"edit_op_fixtures.json missing: {missing}"
