"""Transfer options (SPEC §8.3 step 7) — code-owned, pure+DB.

When `shortfall > 0`, the agent handler asks this module to
produce a sorted list of branches that could help cover the gap.
The model is never involved — stock is a code concern (SPEC
§8.2: `stock_all_branches` is code-only, not an agent tool).

Rules:
  - **Exclude the run's own branch.** It already supplied
    `on_hand`; listing it again is noise.
  - **Full-cover branches first** (on_hand >= shortfall), sorted
    `on_hand DESC, branch_id ASC`.
  - **Partial-cover branches after**, same sort; carry
    `cover: "N of M"` where N = min(on_hand, shortfall), M =
    shortfall, so the UI can render e.g. "12 of 20".
  - Branches with `on_hand == 0` are not returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.catalog.query_builder import run_named_query


@dataclass(frozen=True)
class TransferRow:
    """Row in `transfer_options_json` (SPEC §8.3 step 7).

    `cover` is `None` when the branch fully covers the shortfall;
    a `"N of M"` string when it only partially covers. This shape
    matches the SPEC field exactly — the view/bid-assembly layer
    adapts to the richer `TransferOption` Pydantic contract if
    needed.
    """

    branch_id: str
    branch_name: str
    on_hand: int
    cover: str | None  # None for full cover; "N of M" for partial


def transfer_options_for(
    sku: str,
    *,
    shortfall: int,
    home_branch_id: str,
    catalog_db_path: Path,
) -> list[TransferRow]:
    """Return the transfer rows for `sku` to cover `shortfall` at
    `home_branch_id`. Returns `[]` when shortfall is 0 — no gap,
    nothing to transfer."""

    if shortfall <= 0:
        return []

    rows = run_named_query("stock_all_branches", [sku], catalog_db_path=catalog_db_path)

    candidates = [
        r for r in rows if r.get("branch_id") != home_branch_id and int(r.get("on_hand") or 0) > 0
    ]

    full: list[TransferRow] = []
    partial: list[TransferRow] = []
    for r in candidates:
        on_hand = int(r["on_hand"])
        tr = TransferRow(
            branch_id=str(r["branch_id"]),
            branch_name=str(r.get("branch_name") or ""),
            on_hand=on_hand,
            cover=None if on_hand >= shortfall else f"{min(on_hand, shortfall)} of {shortfall}",
        )
        (full if tr.cover is None else partial).append(tr)

    # SPEC §8.3: full-cover first, within each tier sort on_hand DESC, branch_id ASC.
    full.sort(key=lambda t: (-t.on_hand, t.branch_id))
    partial.sort(key=lambda t: (-t.on_hand, t.branch_id))
    return full + partial
