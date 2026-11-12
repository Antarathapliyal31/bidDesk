"""Phase 5b regression tests.

1. `bids.id` is a global PRIMARY KEY — two runs each saving a
   draft rev 1 must both succeed (id must include run_id).

2. PATCH atomicity — two concurrent PATCHes on the same draft
   revision: exactly one succeeds, the other raises
   `StaleRevisionConflict` (→ 409 at the API layer).
"""

from __future__ import annotations

import asyncio

import pytest

from app.contracts import BidKind
from app.db import repo
from app.db.init import Database
from app.db.repo import StaleRevisionConflict, apply_patch_atomically
from tests._seeds import seed_run
from tests.constants import FIXED_NOW


def _bid_data(rev: int) -> dict:
    """Minimal opaque JSON blob — the DB column is TEXT, so we
    don't need a full Bid contract to exercise id uniqueness or
    the atomic-patch path."""

    return {"revision": rev, "lines": [], "totals": {"total_cents": 0}}


class TestBidIdUniqueAcrossRuns:
    async def test_two_runs_each_save_draft_rev_1(self, db: Database) -> None:
        await seed_run(db, run_id="run_a")
        await seed_run(db, run_id="run_b")

        # Under the old "bid_draft_{rev}" scheme both ids would
        # collide on the global PRIMARY KEY. New scheme embeds
        # the run id, so both inserts coexist.
        await repo.insert_bid(
            db,
            bid_id=f"bid_run_a_draft_1_{'ab' * 4}",
            run_id="run_a",
            kind=BidKind.draft,
            revision=1,
            data=_bid_data(1),
            created_at=FIXED_NOW,
        )
        await repo.insert_bid(
            db,
            bid_id=f"bid_run_b_draft_1_{'cd' * 4}",
            run_id="run_b",
            kind=BidKind.draft,
            revision=1,
            data=_bid_data(1),
            created_at=FIXED_NOW,
        )

        a = await repo.get_bid(db, run_id="run_a", kind="draft")
        b = await repo.get_bid(db, run_id="run_b", kind="draft")
        assert a is not None and b is not None
        assert a["revision"] == 1 and b["revision"] == 1


class TestPatchAtomicity:
    async def test_two_concurrent_patches_one_wins(self, db: Database) -> None:
        await seed_run(db, run_id="run_x")
        await repo.insert_bid(
            db,
            bid_id="bid_run_x_draft_1_seed",
            run_id="run_x",
            kind=BidKind.draft,
            revision=1,
            data=_bid_data(1),
            created_at=FIXED_NOW,
        )

        def _apply_bump(current: dict) -> tuple[dict, list[dict]]:
            new = dict(current)
            new["revision"] = int(current["revision"]) + 1
            return new, [{"op": "noop", "revision": new["revision"]}]

        async def _patch() -> dict | Exception:
            try:
                return await apply_patch_atomically(
                    db,
                    run_id="run_x",
                    expected_draft_revision=1,
                    apply=_apply_bump,
                    created_at=FIXED_NOW,
                )
            except StaleRevisionConflict as exc:
                return exc

        a, b = await asyncio.gather(_patch(), _patch())
        results = [a, b]
        winners = [r for r in results if isinstance(r, dict)]
        losers = [r for r in results if isinstance(r, StaleRevisionConflict)]
        assert len(winners) == 1
        assert len(losers) == 1
        assert winners[0]["revision"] == 2
        # Loser carries the CURRENT draft at the time of conflict
        # so the client can reconcile without a second GET.
        assert losers[0].current["revision"] in (1, 2)

        final = await repo.get_bid(db, run_id="run_x", kind="draft")
        assert final is not None and final["revision"] == 2

    async def test_stale_revision_mismatch_raises(self, db: Database) -> None:
        await seed_run(db, run_id="run_y")
        await repo.insert_bid(
            db,
            bid_id="bid_run_y_draft_3_seed",
            run_id="run_y",
            kind=BidKind.draft,
            revision=3,
            data=_bid_data(3),
            created_at=FIXED_NOW,
        )

        def _noop(current: dict) -> tuple[dict, list[dict]]:
            new = dict(current)
            new["revision"] = int(current["revision"]) + 1
            return new, []

        with pytest.raises(StaleRevisionConflict) as exc:
            await apply_patch_atomically(
                db,
                run_id="run_y",
                expected_draft_revision=1,  # stale
                apply=_noop,
                created_at=FIXED_NOW,
            )
        assert exc.value.current["revision"] == 3
