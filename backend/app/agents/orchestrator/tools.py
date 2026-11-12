"""Orchestrator tools (SPEC §7.2) — Phase-3 subset.

For Phase 3 we ship `ask_document_analyst` (the only tool the
Phase-3 shim needs) and the deterministic `build_submit_bid_payload`
helper that converts verified items into a summary + contractor
questions. Phase 5 adds `classify_items`, `add_requirements`,
`price_items`, `flag_conflict`, `check_gap_rules`.
"""

from __future__ import annotations

from typing import Any

from app.agents.analyst.agent import AnalystRequest
from app.contracts import AgentName, ItemView
from app.runtime.delegate import AgentContext, delegate


async def ask_document_analyst(
    request: AnalystRequest,
    *,
    ctx: AgentContext,
) -> dict[str, Any]:
    """Delegate to the Document Analyst. Returns the raw payload
    dict (callers rehydrate typed views as needed).

    Raises if the result status isn't `ok` so the orchestrator
    layer above sees analyst failures rather than silently
    getting an empty items list. Phase 5 widens this to handle
    partial / failed / timeout statuses separately.
    """

    result = await delegate(AgentName.document_analyst, request, ctx=ctx)
    if result.status != "ok" or result.output is None:
        raise RuntimeError(
            f"Document Analyst returned status={result.status!r}: "
            f"{[e.message for e in result.errors]}"
        )
    return result.output.model_dump(mode="python")


def build_submit_bid_payload(items: list[ItemView]) -> dict[str, Any]:
    """Phase-3 deterministic `submit_bid` shim.

    Builds the contractor-questions list from the review flags
    already carried on each item (quote_not_found,
    quantity_not_in_quote, unknown_unit). Produces a short summary
    naming the counts. Phase 5 replaces this with a model-authored
    summary that goes through `submit_bid` validation (SPEC §9.2).
    """

    material = sum(1 for i in items if i.kind.value == "material")
    labor = sum(1 for i in items if i.kind.value == "labor")
    exclusions = sum(1 for i in items if i.kind.value == "exclusion")
    notes = sum(1 for i in items if i.kind.value == "note")

    questions: list[dict[str, Any]] = []
    for item in items:
        # Only item-scope review-prompts surface here. Bid-scope
        # rules (missing underlayment, etc.) land in Phase 5.
        for note in item.notes:
            if note == "quote_not_found":
                questions.append(
                    {
                        "text": (
                            f"Quote for {item.description!r} on page "
                            f"{item.source.page} didn't verify against page text. "
                            "Please confirm."
                        ),
                        "basis": {"kind": "flagged_line", "ref_id": item.id},
                    }
                )
            elif note == "quantity_not_in_quote":
                questions.append(
                    {
                        "text": (
                            f"Quantity for {item.description!r} doesn't appear in "
                            f"its source quote. Confirm the number."
                        ),
                        "basis": {"kind": "flagged_line", "ref_id": item.id},
                    }
                )
            elif note == "unknown_unit":
                questions.append(
                    {
                        "text": (
                            f"Unit {item.unit_raw!r} on {item.description!r} "
                            "isn't in our table. Clarify."
                        ),
                        "basis": {"kind": "flagged_line", "ref_id": item.id},
                    }
                )

    summary_parts = [
        f"Extracted {material} material",
        f"{labor} labor" if labor else "",
        f"{exclusions} exclusion" if exclusions else "",
        f"{notes} note" if notes else "",
    ]
    summary = ", ".join(p for p in summary_parts if p)
    if questions:
        summary += f". {len(questions)} line(s) need estimator review."

    return {
        "summary": summary,
        "contractor_questions": questions,
        "assumptions": [],
    }
