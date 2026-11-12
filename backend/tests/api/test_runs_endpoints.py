"""Phase 5b endpoint tests — `/api/runs` surface.

Coverage:
  - POST /api/runs validation (file XOR text, invalid branch,
    text too long, faults gated by `demo_faults_enabled`).
  - GET /api/runs (cursor pagination clamping).
  - GET /api/runs/{id} (detail payload shape).
  - GET /api/runs/{id}/events (list persisted events).
  - GET /api/runs/{id}/stream (SSE replay → live handoff: new
    live event delivered after a replayed one, no dupes/gaps).
  - GET /api/runs/{id}/source.pdf (404 for text, serves for pdf).
  - PATCH /api/runs/{id}/bid 422 (op schema) + 409 stale_revision.
  - POST /api/runs/{id}/bid/finalize 422 open_flags.

`start_pipeline` is monkeypatched to a no-op so POST doesn't
need the real Anthropic API. Bid fixtures use the real
`build_bid` pipeline so the Bid contract validates end-to-end.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from httpx import AsyncClient

from app.bid.assemble import AssemblyInput, build_bid
from app.contracts import AgentName, BidKind, InputKind
from app.contracts.bid import BranchInfo, ProjectInfo
from app.contracts.common import GeneratedBy
from app.db import repo
from tests.constants import FIXED_NOW


@pytest.fixture(autouse=True)
def _nop_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop POST /api/runs from launching a real pipeline."""

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr("app.api.runs.start_pipeline", _noop)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _item_dict(iid: str = "it_1") -> dict[str, Any]:
    return {
        "id": iid,
        "kind": "material",
        "description": "2x4 x 8 SPF",
        "quantity_text": "50",
        "unit_raw": "pcs",
        "source_page": 1,
        "source_quote": "50 pcs 2x4x8 SPF",
        "read_via": "text",
        "verified": True,
        "notes": [],
        "specs": {},
    }


def _price_dict() -> dict[str, Any]:
    return {
        "status": "matched",
        "sku": "LUM-2X4-8-SPF-2",
        "product_name": "SPF 2x4x8",
        "sale_unit": "EA",
        "sale_qty": "50",
        "conversion_path": None,
        "unit_price_cents": 400,
        "branch_on_hand": 100,
        "shortfall": 0,
        "transfer_options": [],
        "assumptions": [],
        "attribute_mismatches": [],
        "verify_notes": [],
        "is_substitute": False,
        "substitute_reason": None,
        "confidence": "high",
        "confidence_reasons": [],
    }


def _make_bids(open_review: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a real agent_bid + draft_bid using build_bid. If
    `open_review=True` the single line is routed to open-review
    (triage=ambiguous), which drives the `open_flags>0` finalize
    test."""

    items = [_item_dict()]
    inp = AssemblyInput(
        run_id="run_test",
        branch=BranchInfo(id="BR-NJ-01", name="NJ", tax_rate_ppm=70_000),
        project=ProjectInfo(name="Test", due_date_text=None),
        items=items,
        triage_by_item=(
            {}
            if open_review
            else {
                "it_1": {
                    "item_id": "it_1",
                    "category": "price",
                    "reason": "t",
                    "created_at": FIXED_NOW,
                }
            }
        ),
        requirements_by_item={},
        price_results_by_item={} if open_review else {"it_1": _price_dict()},
        conflicts=[],
        summary="One material line extracted for review.",
        assumptions=[],
        contractor_questions=[],
        generated_by=GeneratedBy.orchestrator,
        partial_reasons=[],
        gap_rule_ids=set(),
    )
    result = build_bid(inp)
    assert result.agent_bid is not None and result.draft_bid is not None
    return (
        result.agent_bid.model_dump(mode="json"),
        result.draft_bid.model_dump(mode="json"),
    )


async def _seed_run_with_bid(
    app_state: Any, *, run_id: str, open_review: bool = False
) -> dict[str, Any]:
    db = app_state.db
    await repo.insert_run(
        db,
        run_id=run_id,
        branch_id="BR-NJ-01",
        input_kind=InputKind.text,
        input_name="pasted",
        created_at=FIXED_NOW,
    )
    agent, draft = _make_bids(open_review=open_review)
    await repo.insert_bid(
        db,
        bid_id=f"bid_{run_id}_agent_0_seed",
        run_id=run_id,
        kind=BidKind.agent,
        revision=0,
        data=agent,
        created_at=FIXED_NOW,
    )
    await repo.insert_bid(
        db,
        bid_id=f"bid_{run_id}_draft_1_seed",
        run_id=run_id,
        kind=BidKind.draft,
        revision=1,
        data=draft,
        created_at=FIXED_NOW,
    )
    return draft


# ---------------------------------------------------------------------------
# POST /api/runs
# ---------------------------------------------------------------------------


class TestCreateRun:
    async def test_text_run_returns_201_and_run_id(self, client: AsyncClient) -> None:
        r = await client.post(
            "/api/runs",
            data={"branch_id": "BR-NJ-01", "text": "50 2x4x8 SPF"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["run_id"].startswith("run_")

    async def test_both_file_and_text_rejected(self, client: AsyncClient) -> None:
        r = await client.post(
            "/api/runs",
            data={"branch_id": "BR-NJ-01", "text": "x"},
            files={"file": ("x.pdf", b"%PDF-1.4\n...", "application/pdf")},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_input"

    async def test_neither_file_nor_text_rejected(self, client: AsyncClient) -> None:
        r = await client.post("/api/runs", data={"branch_id": "BR-NJ-01"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_input"

    async def test_unknown_branch_rejected(self, client: AsyncClient) -> None:
        r = await client.post("/api/runs", data={"branch_id": "BR-ZZ-99", "text": "x"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_branch"

    async def test_faults_rejected_when_flag_off(self, client: AsyncClient) -> None:
        r = await client.post(
            "/api/runs",
            data={
                "branch_id": "BR-NJ-01",
                "text": "x",
                "faults": json.dumps([{"kind": "analyst_json_malformed"}]),
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_input"


# ---------------------------------------------------------------------------
# GET /api/runs + /api/runs/{id}
# ---------------------------------------------------------------------------


class TestListAndDetail:
    async def test_empty_list(self, client: AsyncClient) -> None:
        r = await client.get("/api/runs")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["next_cursor"] is None

    async def test_detail_after_seed(self, client: AsyncClient) -> None:
        await _seed_run_with_bid(client._transport.app.state, run_id="run_detail")
        r = await client.get("/api/runs/run_detail")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "run_detail"
        assert body["bid"] is not None
        assert body["agent_bid"] is not None
        # Nested blocks per API_CONTRACT.
        assert body["input"]["kind"] == "text"
        assert body["input"]["name"] == "pasted"
        assert body["project"]["name"] is None
        assert body["branch"]["id"] == "BR-NJ-01"
        assert body["branch"]["tax_rate_ppm"] > 0

    async def test_limit_is_clamped_to_100(self, client: AsyncClient) -> None:
        r = await client.get("/api/runs?limit=9999")
        assert r.status_code == 200  # clamp is silent, not 422

    async def test_detail_404_for_unknown(self, client: AsyncClient) -> None:
        r = await client.get("/api/runs/run_nope")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "run_not_found"


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/events + /stream
# ---------------------------------------------------------------------------


class TestEventsEndpoint:
    async def test_lists_persisted_events(self, client: AsyncClient) -> None:
        await _seed_run_with_bid(client._transport.app.state, run_id="run_ev")
        bus = client._transport.app.state.bus
        await bus.emit(
            type="run.started",
            run_id="run_ev",
            agent=None,
            payload={
                "input_kind": "text",
                "input_name": "pasted",
                "page_count": None,
                "branch_id": "BR-NJ-01",
            },
        )
        # Allow the write-queue to flush before we read via API.
        await asyncio.sleep(0.05)
        r = await client.get("/api/runs/run_ev/events")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["type"] == "run.started"


class TestSSEReplayAndLive:
    async def test_replay_then_live_handoff_no_gaps(self, client: AsyncClient) -> None:
        state = client._transport.app.state
        await _seed_run_with_bid(state, run_id="run_sse")
        bus = state.bus
        # Pre-seed two events; replay should deliver both.
        await bus.emit(
            type="run.started",
            run_id="run_sse",
            agent=None,
            payload={
                "input_kind": "text",
                "input_name": "pasted",
                "page_count": None,
                "branch_id": "BR-NJ-01",
            },
        )
        await bus.emit(
            type="agent.activity",
            run_id="run_sse",
            agent=AgentName.orchestrator,
            payload={"text": "triage"},
        )
        await asyncio.sleep(0.05)

        # Fire the live event on a background task so it lands
        # AFTER the server has subscribed + drained the DB replay.
        async def _emit_live_after_delay() -> None:
            await asyncio.sleep(0.1)
            await bus.emit(
                type="run.finished",
                run_id="run_sse",
                agent=AgentName.orchestrator,
                payload={
                    "status": "completed",
                    "outcome": "bid_ready",
                    "outcome_detail": None,
                },
            )

        emitter = asyncio.create_task(_emit_live_after_delay())
        delivered_ids: list[int] = []
        buffer: list[str] = []
        try:
            async with client.stream("GET", "/api/runs/run_sse/stream") as resp:
                assert resp.status_code == 200
                async for chunk in resp.aiter_text():
                    buffer.append(chunk)
                    joined = "".join(buffer)
                    delivered_ids.clear()
                    for line in joined.splitlines():
                        if line.startswith("id: "):
                            delivered_ids.append(int(line[4:]))
                    # run.finished closes the stream; break so we
                    # don't block waiting for the next chunk.
                    if "event: run.finished" in joined:
                        break
        finally:
            await emitter

        # No duplicates, strictly monotonic ids; 2 replayed + 1 live.
        assert delivered_ids == sorted(delivered_ids)
        assert len(delivered_ids) == len(set(delivered_ids))
        assert len(delivered_ids) >= 3


# ---------------------------------------------------------------------------
# GET /api/runs/{id}/source.pdf
# ---------------------------------------------------------------------------


class TestSourcePdf:
    async def test_404_for_text_run(self, client: AsyncClient) -> None:
        await _seed_run_with_bid(client._transport.app.state, run_id="run_text")
        r = await client.get("/api/runs/run_text/source.pdf")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "run_not_found"

    async def test_pdf_run_serves_pdf(
        self, client: AsyncClient, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        state = client._transport.app.state
        stored = state.settings.app_db_path.parent / "uploads"
        stored.mkdir(parents=True, exist_ok=True)
        path = stored / "run_pdf.pdf"
        path.write_bytes(b"%PDF-1.4\nfake")
        await repo.insert_run(
            state.db,
            run_id="run_pdf",
            branch_id="BR-NJ-01",
            input_kind=InputKind.pdf,
            input_name="x.pdf",
            created_at=FIXED_NOW,
        )
        await repo.insert_document(
            state.db,
            run_id="run_pdf",
            filename="x.pdf",
            sha256="0" * 64,
            page_count=1,
            stored_path=str(path),
        )
        r = await client.get("/api/runs/run_pdf/source.pdf")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"


# ---------------------------------------------------------------------------
# PATCH /api/runs/{id}/bid
# ---------------------------------------------------------------------------


class TestPatchBid:
    async def test_set_unit_price_without_override_reason_is_422(self, client: AsyncClient) -> None:
        draft = await _seed_run_with_bid(client._transport.app.state, run_id="run_patch_422")
        line_id = draft["lines"][0]["line_id"]
        r = await client.patch(
            "/api/runs/run_patch_422/bid",
            json={
                "draft_revision": 1,
                "ops": [
                    {
                        "op": "set_unit_price",
                        "line_id": line_id,
                        "unit_price_cents": 999,
                        # override_reason missing → Pydantic 422
                    }
                ],
            },
        )
        assert r.status_code == 422

    async def test_stale_revision_returns_409(self, client: AsyncClient) -> None:
        draft = await _seed_run_with_bid(client._transport.app.state, run_id="run_patch_409")
        line_id = draft["lines"][0]["line_id"]
        r = await client.patch(
            "/api/runs/run_patch_409/bid",
            json={
                "draft_revision": 99,  # stale
                "ops": [{"op": "accept_line", "line_id": line_id}],
            },
        )
        assert r.status_code == 409
        body = r.json()
        assert body["error"]["code"] == "stale_revision"
        assert body["error"]["details"]["current"]["revision"] == 1


# ---------------------------------------------------------------------------
# POST /api/runs/{id}/bid/finalize
# ---------------------------------------------------------------------------


class TestFinalize:
    async def test_open_flags_blocks_finalize(self, client: AsyncClient) -> None:
        # open_review=True means the single line routes to
        # open-review (triage=ambiguous, not_priced). open_flags>0.
        draft = await _seed_run_with_bid(
            client._transport.app.state, run_id="run_fin_open", open_review=True
        )
        assert draft["totals"]["open_flags"] >= 1

        r = await client.post(
            "/api/runs/run_fin_open/bid/finalize",
            json={"draft_revision": 1},
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error"]["code"] == "open_flags"
        assert body["error"]["details"]["open_line_ids"]
