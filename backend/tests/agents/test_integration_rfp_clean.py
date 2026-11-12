"""End-to-end: run the Document Analyst against `rfp_clean.pdf`
with a scripted FakeLLM that mirrors
`samples/expected/rfp_clean.json`.

No real API calls. Proves:
  - Ingest + routing + extraction + verify compose.
  - Items from the FakeLLM get `source.verified=True` once code
    checks them against the real page text (SPEC §6.4).
  - `must_not_include` strings (header boilerplate) never land on
    items.

Phase 5a turned the orchestrator into a model-driven loop, so
this file calls the analyst via `delegate()` directly — the
assertions are about analyst behavior, not orchestration.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest_asyncio

from app.agents.analyst.agent import AnalystRequest, register_default_analyst
from app.contracts import AgentName, ItemView
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, delegate, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse
from tests._seeds import seed_run

_SAMPLES_ROOT = Path(__file__).resolve().parents[3] / "samples"


def _expected(name: str) -> dict[str, Any]:
    return json.loads((_SAMPLES_ROOT / "expected" / name).read_text(encoding="utf-8"))


def _fake_llm_from_expected(expected: dict[str, Any]) -> FakeLLM:
    """Build a FakeLLM that returns a `report_extraction` payload
    built from the expected JSON. Hand-typed counts stay out of
    the loop (review rule 8)."""

    items = [
        {
            "kind": "material",
            "description": item["description"],
            "quantity_text": item["quantity_text"],
            "unit_raw": item["unit_raw"],
            "source_page": item["source_page"],
            "source_quote": item["quote"],
        }
        for item in expected["items"]
    ]
    for excl in expected.get("exclusions", []):
        items.append(
            {
                "kind": "exclusion",
                "description": excl["description"],
                "quantity_text": None,
                "unit_raw": None,
                "source_page": excl["source_page"],
                "source_quote": excl["quote"],
            }
        )
    payload = {
        "items": items,
        "facts": {"project_name": None, "due_date_text": None, "general_notes": []},
        "unreadable_pages": [],
    }
    return FakeLLM(responses=[ScriptedResponse(content=payload, tokens_in=1000, tokens_out=500)])


@pytest_asyncio.fixture
async def ctx(db: Database, bus: EventBus):
    await seed_run(db)
    budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
    budget.start()
    yield AgentContext(run_id="run_abc", bus=bus, budget=budget)
    unregister_all()


async def _run_analyst(ctx: AgentContext, document_tag: str) -> list[ItemView]:
    result = await delegate(
        AgentName.document_analyst,
        AnalystRequest(
            kind="pdf",
            pdf_path=str(_SAMPLES_ROOT / "rfp_clean.pdf"),
            pasted_text=None,
            document_tag=document_tag,
        ),
        ctx=ctx,
    )
    assert result.status == "ok", (result.status, [e.message for e in result.errors])
    out = result.output.model_dump(mode="python")  # type: ignore[union-attr]
    return [ItemView.model_validate(i) for i in out["items"]]


class TestRfpCleanAnalyst:
    async def test_items_match_expected_and_verify(self, ctx: AgentContext) -> None:
        expected = _expected("rfp_clean.json")
        register_default_analyst(_fake_llm_from_expected(expected))
        items = await _run_analyst(ctx, "document_test_clean")

        expected_total = len(expected["items"]) + len(expected.get("exclusions", []))
        assert len(items) == expected_total

        for item in items:
            assert item.source.verified is True, (
                f"item {item.id} {item.description!r} failed to verify against real page text"
            )
            assert item.source.read_via == "text"
            assert "quote_not_found" not in item.notes

        for bad in expected["must_not_include"]:
            for item in items:
                assert bad not in item.description, f"'{bad}' leaked into {item.description!r}"
                assert bad not in (item.source.quote or "")
