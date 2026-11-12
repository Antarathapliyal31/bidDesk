"""Orchestrator loop (SPEC §7.1) — minimal Phase-3 scope.

Hand-written, explainable. Each turn:
  1. budget.assert_alive() → may raise BudgetExhausted → fallback.
  2. Call LLM with tools; for Phase 3 the orchestrator runs
     `ask_document_analyst` → waits for the typed AnalystResult →
     calls `submit_bid` with a summary + flagged questions.

Phase 5 widens the tool set (classify_items, add_requirements,
price_items, flag_conflict, check_gap_rules) and swaps this
deterministic shim for a real LLM-driven tool loop. For Phase 3
we execute a fixed sequence so the end-to-end test proves the
delegate boundary + verify + spotlight all work together.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.analyst.agent import AnalystRequest
from app.agents.orchestrator.tools import (
    ask_document_analyst,
    build_submit_bid_payload,
)
from app.contracts import AgentName, ItemView
from app.runtime.delegate import AgentContext


@dataclass(frozen=True)
class RunPlan:
    """What the orchestrator was asked to do. Phase 5 widens this
    into a message-threaded state; Phase 3 just needs the input
    shape."""

    kind: str  # "pdf" | "text"
    pdf_path: str | None
    pasted_text: str | None
    branch_id: str
    document_tag: str


@dataclass
class OrchestratorOutcome:
    """Returned from one end-to-end pass. Carries the verified
    items + the submit_bid shell so the HTTP layer can assemble
    the Bid (bid assembly is Phase 4+)."""

    items: list[ItemView]
    page_routes: list[dict[str, Any]]
    unreadable_pages: list[int]
    project_name: str | None
    due_date_text: str | None
    summary: str
    contractor_questions: list[dict[str, Any]]


async def run_orchestrator(plan: RunPlan, *, ctx: AgentContext) -> OrchestratorOutcome:
    """Execute Phase-3 orchestrator loop against the registered
    `document_analyst` agent. Pricing (Phase 4) and the full tool
    loop (Phase 5) are not wired yet; this proves the wiring is
    typed and verified end-to-end."""

    ctx.budget.assert_alive()

    analyst_result = await ask_document_analyst(
        AnalystRequest(
            kind=plan.kind,
            pdf_path=plan.pdf_path,
            pasted_text=plan.pasted_text,
            document_tag=plan.document_tag,
        ),
        ctx=ctx,
    )
    # analyst_result is the raw dict from the AgentResult.output
    # (contracts.Contract instances serialize to dicts in
    # model_dump mode='python'). Rehydrate items for callers that
    # want the typed view.
    items = [ItemView.model_validate(item) for item in analyst_result["items"]]

    # Phase-3 placeholder submit_bid: summarize + list open
    # review flags as contractor questions. Phase 5 replaces this
    # with a model-driven submit_bid.
    payload = build_submit_bid_payload(items)

    # Mark one turn against the budget so budgets are exercised
    # even in the Phase-3 shim path.
    ctx.budget.charge(turns=1)

    _ = AgentName  # ensure import kept — Phase 5 uses it directly

    return OrchestratorOutcome(
        items=items,
        page_routes=analyst_result["page_routes"],
        unreadable_pages=analyst_result["unreadable_pages"],
        project_name=analyst_result["project_name"],
        due_date_text=analyst_result["due_date_text"],
        summary=payload["summary"],
        contractor_questions=payload["contractor_questions"],
    )
