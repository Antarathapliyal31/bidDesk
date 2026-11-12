"""`verify.py` tests (SPEC §8.3 — code decides).

Note: `PricedLineView.sale_qty` is `DecimalStr` — a Decimal
at runtime, serialized to string only when going through JSON.
Tests compare against `Decimal`, not `str`.

Covers:
  - Happy path: real catalog SKU, unit conversion, confidence, stock.
  - Candidate-set check (Antara addition #2): on first attempt the
    violation RAISES CandidateSetViolation; on retry
    (`is_retry=True`) the item soft-fails with verify_note
    "candidate_set" and the failure is reported in
    `VerifyOutcome.candidate_set_failures` so the handler can add
    an `ItemError`.
  - Hallucinated SKU (not in DB): status downgrades to `no_match`.
  - `no_match` proposal (sku=None) is a valid answer.
  - `partial_item_ids` from the loop become per-item `failed`.
  - Attribute mismatches populated when a requirement differs
    from the chosen catalog row.
  - Sanity range check flags a line whose sale_qty blows past
    the configured max via verify_notes.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from app.agents.pricing.extraction import PricingLoopOutcome
from app.agents.pricing.verify import (
    CandidateSetViolation,
    MissingQueryViolation,
    needs_length_but_missing,
    verify_batch,
)
from app.contracts import ItemView
from app.contracts.common import ConfidenceLevel, ItemKind, MatchStatus, SourceRef
from app.contracts.pricing_agent import (
    MatchProposal,
    PricingItemRequest,
    RequirementSpec,
    SubmitMatchesPayload,
)


def _item(id_: str, description: str, qty: str, unit: str) -> ItemView:
    return ItemView(
        id=id_,
        kind=ItemKind.material,
        description=description,
        quantity_text=qty,
        unit_raw=unit,
        source=SourceRef(page=1, quote="...", read_via="text", verified=True),
        notes=[],
    )


def _pick_spf_2x4x8(catalog_db: Path) -> str:
    conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT sku FROM products "
            "WHERE nominal_size = '2x4' AND length_ft = 8 "
            "AND species = 'SPF' AND active = 1 LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return row[0]


class TestHappyPath:
    def test_matched_pulls_price_from_catalog(self, bundle, catalog_db: Path) -> None:
        sku = _pick_spf_2x4x8(catalog_db)
        request = PricingItemRequest(
            item=_item("it_1", "SPF 2x4x8 studs", qty="40", unit="pc"),
            requirements=[],
        )
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=sku, searched="x")]
            ),
            candidate_rows_by_item={"it_1": [{"sku": sku, "length_ft": None}]},
            queried_item_ids={"it_1"},
        )
        verify = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        )
        [result] = verify.results
        assert verify.candidate_set_failures == []
        assert result.status == "matched"
        assert result.priced.status == MatchStatus.matched
        assert result.priced.sku == sku
        assert result.priced.unit_price_cents > 0
        assert result.priced.sale_qty == Decimal("40")
        assert result.priced.sale_unit == "EA"
        assert result.priced.confidence == ConfidenceLevel.high


class TestCandidateSetFirstAttempt:
    def test_sku_not_in_candidates_raises_on_first_attempt(self, bundle, catalog_db: Path) -> None:
        """Even if the SKU exists in catalog, if the agent didn't
        see it via query_catalog it's rejected on the first pass."""

        sku = _pick_spf_2x4x8(catalog_db)
        request = PricingItemRequest(item=_item("it_1", "studs", "40", "pc"), requirements=[])
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=sku, searched="x")]
            ),
            candidate_rows_by_item={"it_1": []},
            queried_item_ids={"it_1"},
        )
        with pytest.raises(CandidateSetViolation) as exc:
            verify_batch(
                outcome,
                request_items=[request],
                bundle=bundle,
                catalog_db_path=catalog_db,
                branch_id="BR-NJ-01",
                is_retry=False,
            )
        assert exc.value.item_id == "it_1"


class TestCandidateSetRetry:
    def test_sku_not_in_candidates_soft_fails_on_retry(self, bundle, catalog_db: Path) -> None:
        """On the retry pass (Antara addition #2 + #2-partial):
        no raise; that single item is failed with
        verify_note 'candidate_set', the rest of the batch keeps
        going, and the failure surfaces in candidate_set_failures."""

        sku = _pick_spf_2x4x8(catalog_db)
        # Two items: one legit (in candidates), one hallucinated-feeling
        # (SKU exists but not in that item's candidate set).
        req_good = PricingItemRequest(item=_item("it_good", "studs", "40", "pc"), requirements=[])
        req_bad = PricingItemRequest(item=_item("it_bad", "studs", "40", "pc"), requirements=[])
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[
                    MatchProposal(item_id="it_good", sku=sku, searched="x"),
                    MatchProposal(item_id="it_bad", sku=sku, searched="x"),
                ]
            ),
            candidate_rows_by_item={
                "it_good": [{"sku": sku, "length_ft": None}],
                "it_bad": [],
            },
            queried_item_ids={"it_good", "it_bad"},
        )
        verify = verify_batch(
            outcome,
            request_items=[req_good, req_bad],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
            is_retry=True,
        )
        assert verify.candidate_set_failures == [("it_bad", sku)]
        by_id = {r.item_id: r for r in verify.results}
        assert by_id["it_good"].status == "matched"
        assert by_id["it_bad"].status == "failed"
        assert "candidate_set" in by_id["it_bad"].verify_notes


class TestHallucinatedSku:
    def test_unknown_sku_in_candidates_becomes_failed(self, bundle, catalog_db: Path) -> None:
        """Edge case: SKU was in the candidate set (query_catalog
        returned it) but has since been removed from catalog. The
        candidate-set check runs BEFORE catalog-existence, so if
        we land here the SKU WAS seen at some point. Per the
        SPEC-refinement in Phase 4, this is NOT a no_match — it's
        a per-item FAILURE with verify_note 'hallucinated_sku'.
        `no_match` is reserved for `sku=None` submissions.
        """

        request = PricingItemRequest(item=_item("it_1", "studs", "40", "pc"), requirements=[])
        fake_sku = "LUM-NOPE-0000"
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=fake_sku, searched="x")]
            ),
            candidate_rows_by_item={"it_1": [{"sku": fake_sku, "length_ft": None}]},
            queried_item_ids={"it_1"},
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.status == "failed"
        assert "hallucinated_sku" in result.verify_notes
        assert result.priced.sku == fake_sku  # recorded for the trace

    def test_invented_sku_caught_by_candidate_set_first(self, bundle, catalog_db: Path) -> None:
        """Typical invented-SKU flow: the model fabricates a SKU
        the catalog never returned. The candidate-set check runs
        FIRST (before any catalog lookup), so the invented SKU
        is caught as candidate_set — on retry, soft-fails that
        item without ever touching `_fetch_product`."""

        request = PricingItemRequest(item=_item("it_1", "studs", "40", "pc"), requirements=[])
        invented = "LUM-INVENTED-1234"
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=invented, searched="x")]
            ),
            # Empty candidate set — model never got this SKU from query_catalog.
            candidate_rows_by_item={"it_1": []},
            queried_item_ids={"it_1"},
        )
        # Retry path: no raise; soft-fails.
        verify = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
            is_retry=True,
        )
        assert verify.candidate_set_failures == [("it_1", invented)]
        [r] = verify.results
        assert r.status == "failed"
        assert "candidate_set" in r.verify_notes
        # Never no_match.
        assert r.priced.status != MatchStatus.no_match


class TestNoMatch:
    def test_sku_none_with_empty_candidates_is_no_match(self, bundle, catalog_db: Path) -> None:
        """Model returned sku=None AND query_catalog came back
        empty → legitimate no_match. The catalog genuinely has
        nothing suitable."""

        request = PricingItemRequest(
            item=_item("it_1", "something odd", "1", "ea"), requirements=[]
        )
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=None, searched="nothing")]
            ),
            candidate_rows_by_item={"it_1": []},
            queried_item_ids={"it_1"},
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.status == "no_match"


class TestNeedsReviewPromotion:
    def test_sku_none_but_candidates_exist_becomes_needs_review(
        self, bundle, catalog_db: Path
    ) -> None:
        """Model returned sku=None but query_catalog DID return
        candidates for this item → code promotes to needs_review
        (Antara refinement). verify_note 'candidates_available'
        carries the fact that options existed."""

        sku = _pick_spf_2x4x8(catalog_db)
        request = PricingItemRequest(item=_item("it_1", "studs", "40", "pc"), requirements=[])
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=None, searched="x")]
            ),
            # Non-empty candidates — the agent saw options but
            # didn't pick one.
            candidate_rows_by_item={
                "it_1": [{"sku": sku, "length_ft": 8, "category": "dimensional_lumber"}]
            },
            queried_item_ids={"it_1"},
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.status == "needs_review"
        assert result.priced.sku is None
        assert "candidates_available" in result.verify_notes


class TestAmbiguousFlag:
    def test_ambiguous_pick_drops_confidence_to_medium_with_note(
        self, bundle, catalog_db: Path
    ) -> None:
        """A valid match that the model flagged `ambiguous=True`
        stays matched (price is right), but confidence drops to
        medium and verify_notes carries `ambiguous_pick` so the
        bid-assembly layer routes it to review."""

        from app.contracts.pricing_agent import AmbiguousFlag

        sku = _pick_spf_2x4x8(catalog_db)
        request = PricingItemRequest(
            item=_item("it_1", "SPF 2x4x8 studs", qty="40", unit="pc"),
            requirements=[],
        )
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[
                    MatchProposal(
                        item_id="it_1",
                        sku=sku,
                        searched="SPF 2x4x8",
                        ambiguous=AmbiguousFlag(
                            is_ambiguous=True,
                            reason="treatment unspecified; defaulted to untreated",
                        ),
                    )
                ]
            ),
            candidate_rows_by_item={"it_1": [{"sku": sku, "length_ft": 8}]},
            queried_item_ids={"it_1"},
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.status == "matched"
        assert "ambiguous_pick" in result.verify_notes
        assert result.priced.confidence == ConfidenceLevel.medium
        assert any("Ambiguous" in reason for reason in result.priced.confidence_reasons)


class TestLengthPickerOverride:
    def test_code_overrides_length_for_linear_item(self, bundle, catalog_db: Path) -> None:
        """Item is 12 LF of 2x6 SPF. Model submitted the 8-ft SKU
        (its first guess); code swaps to the 12-ft SKU (zero
        waste) and records length_ft=12 assumption + the
        `code_chose_length` verify_note."""

        import sqlite3

        conn = sqlite3.connect(f"file:{catalog_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT sku, nominal_size, species, grade, treatment, "
                    "fire_rating, moisture_resistant, category, length_ft "
                    "FROM products "
                    "WHERE nominal_size = '2x6' AND species = 'SPF' "
                    "AND treatment = 'none' AND active = 1"
                ).fetchall()
            ]
        finally:
            conn.close()

        submitted = next(r for r in rows if r["length_ft"] == 8)["sku"]
        expected = next(r for r in rows if r["length_ft"] == 12)["sku"]
        request = PricingItemRequest(
            item=_item("it_1", "2x6 SPF", qty="12", unit="LF"),
            requirements=[],
        )
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=submitted, searched="2x6 SPF")]
            ),
            candidate_rows_by_item={"it_1": rows},
            queried_item_ids={"it_1"},
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.status == "matched"
        assert result.priced.sku == expected
        assert str(result.priced.sale_qty) == "1"
        assert "code_chose_length" in result.verify_notes
        assert any(a.field == "length_ft" and a.value == 12 for a in result.assumptions)


class TestPartial:
    def test_partial_item_id_becomes_failed(self, bundle, catalog_db: Path) -> None:
        """Items hitting max_steps return as failed with
        verify_note 'max_steps_exceeded'."""

        request = PricingItemRequest(item=_item("it_1", "x", "1", "ea"), requirements=[])
        outcome = PricingLoopOutcome(
            submit=None,
            candidate_rows_by_item={"it_1": []},
            queried_item_ids={"it_1"},
            partial_item_ids=["it_1"],
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.status == "failed"
        assert result.priced.sku is None
        assert "max_steps_exceeded" in result.verify_notes


class TestAttributeMismatch:
    def test_mismatch_recorded_and_drops_to_low(self, bundle, catalog_db: Path) -> None:
        """Requirement says PT-ground-contact; catalog row the
        model chose is untreated SPF — mismatch recorded,
        confidence drops to low per §8.3 step 8."""

        sku = _pick_spf_2x4x8(catalog_db)  # untreated SPF
        request = PricingItemRequest(
            item=_item("it_1", "sill plate 2x4x8", "40", "pc"),
            requirements=[
                RequirementSpec(field="treatment", value="pressure_treated_ground_contact")
            ],
        )
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=sku, searched="x")]
            ),
            candidate_rows_by_item={"it_1": [{"sku": sku, "length_ft": None}]},
            queried_item_ids={"it_1"},
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.attribute_mismatches
        assert any("treatment" in m for m in result.attribute_mismatches)
        assert result.priced.confidence == ConfidenceLevel.low


class TestSanityRange:
    def test_insane_quantity_flags_sanity_range(self, bundle, catalog_db: Path) -> None:
        """Dimensional lumber EA max is 20,000 — 50,000 trips the check."""

        sku = _pick_spf_2x4x8(catalog_db)
        request = PricingItemRequest(
            item=_item("it_1", "studs", qty="50000", unit="pc"),
            requirements=[],
        )
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=sku, searched="x")]
            ),
            candidate_rows_by_item={"it_1": [{"sku": sku, "length_ft": None}]},
            queried_item_ids={"it_1"},
        )
        [result] = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        ).results
        assert result.priced.sale_qty == Decimal("50000")
        assert any(n.startswith("sanity_range:") for n in result.verify_notes), (
            f"expected sanity_range in verify_notes, got {result.verify_notes}"
        )
        assert result.status == "matched"


class TestNoQueryViolation:
    """Guard: `sku=None` (or any proposal) for an item the model
    never queried → protocol violation (first attempt raises;
    retry soft-fails). Distinguishes "agent searched and found
    nothing" (legit no_match) from "agent skipped the search
    entirely."""

    def test_sku_none_without_query_raises_on_first_attempt(self, bundle, catalog_db: Path) -> None:
        request = PricingItemRequest(item=_item("it_1", "studs", "40", "pc"), requirements=[])
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=None, searched="x")]
            ),
            candidate_rows_by_item={"it_1": []},
            queried_item_ids=set(),  # ← model never queried
        )
        with pytest.raises(MissingQueryViolation) as exc:
            verify_batch(
                outcome,
                request_items=[request],
                bundle=bundle,
                catalog_db_path=catalog_db,
                branch_id="BR-NJ-01",
                is_retry=False,
            )
        assert exc.value.item_ids == ["it_1"]

    def test_sku_none_without_query_soft_fails_on_retry(self, bundle, catalog_db: Path) -> None:
        """Retry pass: don't raise; soft-fail the offending item
        with verify_note 'no_query' so the rest of the batch
        keeps its priced lines."""

        request = PricingItemRequest(item=_item("it_1", "studs", "40", "pc"), requirements=[])
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=None, searched="x")]
            ),
            candidate_rows_by_item={"it_1": []},
            queried_item_ids=set(),
        )
        verify = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
            is_retry=True,
        )
        [result] = verify.results
        assert result.status == "failed"
        assert "no_query" in result.verify_notes

    def test_queried_zero_candidates_sku_none_is_legit_no_match(
        self, bundle, catalog_db: Path
    ) -> None:
        """Explicit no-regression test (per Antara's refinement):
        `queried_item_ids` contains the item AND
        `candidate_rows_by_item[item]` is empty → the model
        genuinely searched and found nothing → no_match, NOT a
        violation. 'queried' is not the same as 'returned rows'."""

        request = PricingItemRequest(
            item=_item("it_1", "cedar shakes", "10", "sq"), requirements=[]
        )
        outcome = PricingLoopOutcome(
            submit=SubmitMatchesPayload(
                results=[MatchProposal(item_id="it_1", sku=None, searched="cedar")]
            ),
            candidate_rows_by_item={"it_1": []},
            queried_item_ids={"it_1"},  # queried, zero results
        )
        verify = verify_batch(
            outcome,
            request_items=[request],
            bundle=bundle,
            catalog_db_path=catalog_db,
            branch_id="BR-NJ-01",
        )
        [result] = verify.results
        assert verify.candidate_set_failures == []
        assert result.status == "no_match"
        assert "no_query" not in result.verify_notes


class TestLengthMissingGuard:
    """Code-side pre-filter (SPEC §8.3 refinement): piece-counted
    framing lumber with no stated length is skipped before the
    model sees it. Hardware with a nominal_size in its name
    (joist hangers 2x8, hurricane ties 2x6) is NOT caught."""

    def test_deck_ledger_no_length_is_flagged(self) -> None:
        item = _item("it_1", "Deck ledger at south wall (2x10)", "1", "ea")
        assert needs_length_but_missing(item, []) is True

    def test_joist_2x10_no_length_is_flagged(self) -> None:
        item = _item("it_1", "2x10 floor joist", "10", "pcs")
        assert needs_length_but_missing(item, []) is True

    def test_length_in_description_passes(self) -> None:
        item = _item("it_1", "2x10 x 12' floor joist", "10", "pcs")
        assert needs_length_but_missing(item, []) is False

    def test_length_in_requirement_passes(self) -> None:
        item = _item("it_1", "2x10 floor joist", "10", "pcs")
        reqs = [RequirementSpec(field="length_ft", value=12)]
        assert needs_length_but_missing(item, reqs) is False

    def test_joist_hangers_not_flagged(self) -> None:
        """Hardware keyword 'hanger' disqualifies the item even
        though '2x8' nominal size is in the description."""

        item = _item("it_1", "Joist hangers 2x8", "40", "ea")
        assert needs_length_but_missing(item, []) is False

    def test_non_lumber_nominal_size_not_flagged(self) -> None:
        """Nominal size present but no lumber keyword — leave
        the call to the agent + its query."""

        item = _item("it_1", "2x4 widget", "10", "ea")
        assert needs_length_but_missing(item, []) is False

    def test_non_ea_unit_not_flagged(self) -> None:
        item = _item("it_1", "2x10 ledger", "12", "LF")
        # Linear quantity — length picker handles it later.
        assert needs_length_but_missing(item, []) is False
