"""Pricing-agent tool loop + submit_matches extraction (SPEC §8.2).

The pricing agent's conversation is multi-turn (unlike the
analyst's one-shot): the model calls `query_catalog` some number
of times, each one is executed by code, the result is fed back as
`tool_result`, and the conversation ends when the model calls
`submit_matches`. This module owns that loop.

Guardrails:
  - Only the two tools from `prompts.py` are offered.
  - `query_catalog` runs through `catalog.query_builder.query_catalog`
    — the model never writes SQL.
  - **Requirements are merged by code** (not the model). Each
    `query_catalog` call must carry `item_id`; this module looks
    up that item's `requirements` and injects them as filters
    before building the SQL. If the model omits a hard constraint
    (e.g. `treatment=pressure_treated_...`), the code adds it back.
    A model-supplied value that conflicts with a requirement is
    overwritten by the requirement, with a note returned to the
    model in the tool_result.
  - **Candidate set per item** is tracked across every query in
    the loop. `verify.py` rejects a submitted SKU that this set
    doesn't contain (prevents fabricated SKUs from slipping in via
    other means than query_catalog).
  - A `QueryBuilderError` becomes a `tool_result` with
    `is_error=True`; the model gets to recover (e.g. drop a bad
    filter) within the same loop.
  - `max_steps` caps the loop so a confused model can't spin
    forever on query_catalog turns. **At the cap, items that did
    get submit_matches entries are returned; unpriced items are
    reported as `partial_item_ids`** — the agent handler surfaces
    them as per-item `failed` status with a `max_steps_exceeded`
    verify_note, not as a whole-batch failure.
  - `BudgetExhausted` (turns or tokens) propagates and ends the
    run (that's a run-level limit, not a batch-level one).
  - Spotlight: the item batch is wrapped through `Spotlighted`
    (the only type the prompt builder accepts).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.pricing.prompts import (
    QUERY_CATALOG_TOOL,
    SUBMIT_MATCHES_TOOL,
    system_prompt,
    user_prompt,
)
from app.catalog.query_builder import (
    CatalogQuery,
    QueryBuilderError,
    query_catalog,
)
from app.catalog.semantic_layer import SemanticBundle
from app.contracts.pricing_agent import (
    PricingItemRequest,
    SubmitMatchesPayload,
)
from app.runtime.budgets import Budget
from app.runtime.events import EventBus
from app.runtime.spotlight import spotlight


class PricingShapeError(Exception):
    """Raised when the model's response is unusable (bad tool_use
    shape, text-only reply, or malformed submit_matches payload).
    `delegate()` converts this one layer up into a ValidationError
    so the model gets a retry with structured feedback.

    NOT raised when `max_steps` is hit — that's a soft outcome
    reported via `PricingLoopOutcome.partial_item_ids`.
    """


@dataclass
class PricingLoopInput:
    run_id: str
    branch_id: str
    items: list[PricingItemRequest]
    document_tag: str
    bundle: SemanticBundle
    catalog_db_path: Path
    max_steps: int = 6  # cap on query_catalog turns before forcing submit
    # ValidationFeedback.errors from the previous attempt, if any.
    # The user_prompt builder injects these as a leading "fix these"
    # section so the model sees exactly what to self-correct.
    feedback: list[str] | None = None


def _format_items_for_prompt(items: list[PricingItemRequest]) -> str:
    """Stringify the batch in a compact, numbered way. The items
    object is already Spotlighted by the caller — this just
    arranges the fields."""

    lines: list[str] = []
    for i, req in enumerate(items, start=1):
        it = req.item
        reqs = (
            "; ".join(f"{r.field}={r.value}" for r in req.requirements)
            if req.requirements
            else "(none)"
        )
        lines.append(
            f"{i}. item_id={it.id}\n"
            f"   description: {it.description}\n"
            f"   quantity_text: {it.quantity_text}\n"
            f"   unit_raw: {it.unit_raw}\n"
            f"   source_page: {it.source.page}\n"
            f"   source_quote: {it.source.quote}\n"
            f"   requirements: {reqs}"
        )
    return "\n\n".join(lines)


def _tool_use_blocks(response: Any) -> list[Any]:
    """Return the tool_use content blocks on a response (SDK or
    FakeLLM shape)."""

    content = getattr(response, "content", None)
    if isinstance(content, list):
        return [b for b in content if getattr(b, "type", None) == "tool_use"]
    return []


def _find_submit_matches(response: Any) -> dict[str, Any] | None:
    """Return the `submit_matches` tool_use input dict, or None if
    the response didn't call it."""

    for block in _tool_use_blocks(response):
        if getattr(block, "name", None) == "submit_matches":
            return dict(getattr(block, "input", {}) or {})
    # FakeLLM may return a raw dict as content for single-turn tests.
    content = getattr(response, "content", None)
    if isinstance(content, dict) and content.get("_tool") == "submit_matches":
        return {k: v for k, v in content.items() if k != "_tool"}
    return None


def _response_to_assistant_message(response: Any) -> dict[str, Any]:
    """Normalize an SDK/FakeLLM response into the assistant turn we
    feed back into messages=[]. Preserves tool_use blocks verbatim
    (the model will refer to them by id in the next turn)."""

    content = getattr(response, "content", None)
    blocks: list[dict[str, Any]] = []
    if isinstance(content, list):
        for b in content:
            t = getattr(b, "type", None)
            if t == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": getattr(b, "id", ""),
                        "name": getattr(b, "name", ""),
                        "input": getattr(b, "input", {}) or {},
                    }
                )
            elif t == "text":
                blocks.append({"type": "text", "text": getattr(b, "text", "")})
    return {"role": "assistant", "content": blocks}


def _merge_requirements(
    item: PricingItemRequest, filters: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Inject `item.requirements` into `filters`. Returns
    (merged_filters, notes). Requirements WIN over conflicting
    model-supplied filters — the note tells the model what changed
    so the next turn can adapt."""

    merged = dict(filters)
    notes: list[str] = []
    for req in item.requirements:
        if req.field in merged and merged[req.field] != req.value:
            notes.append(
                f"requirement {req.field}={req.value!r} overrode "
                f"your filter {req.field}={merged[req.field]!r}"
            )
        elif req.field not in merged:
            notes.append(f"requirement {req.field}={req.value!r} was added to your filters")
        merged[req.field] = req.value
    return merged, notes


def _run_query_catalog(
    tool_input: dict[str, Any],
    *,
    items_by_id: dict[str, PricingItemRequest],
    bundle: SemanticBundle,
    catalog_db_path: Path,
    branch_id: str,
) -> tuple[Any, bool, list[dict[str, Any]]]:
    """Execute one query_catalog tool call. Returns
    `(payload, is_error, rows_returned)`.

    The handler:
      1. Pulls `item_id` out of `tool_input` and rejects the call
         if it's missing or unknown (model error → is_error=True).
      2. Merges that item's requirements into filters (code-side —
         the model cannot drop them).
      3. Builds + validates `CatalogQuery` from the result.
      4. Executes against the semantic layer; enum/type violations
         return `is_error=True` so the model can self-correct.

    On success, also returns the list of SKUs the row set
    contained — the caller tracks these in a per-item candidate
    set so verify.py can enforce "SKU must come from a query in
    this run".
    """

    item_id = tool_input.get("item_id")
    if not isinstance(item_id, str) or not item_id:
        return (
            {"error": "query_catalog requires `item_id` (the item you're matching)."},
            True,
            [],
        )
    item = items_by_id.get(item_id)
    if item is None:
        return (
            {"error": (f"item_id {item_id!r} is not in this batch; known: {sorted(items_by_id)}")},
            True,
            [],
        )

    raw_filters = tool_input.get("filters") or {}
    if not isinstance(raw_filters, dict):
        return ({"error": "filters must be an object"}, True, [])

    merged_filters, requirement_notes = _merge_requirements(item, raw_filters)

    # Build the CatalogQuery (drop item_id, use merged filters).
    try:
        query = CatalogQuery.model_validate(
            {
                "entity": tool_input.get("entity"),
                "filters": merged_filters,
                "text_search": tool_input.get("text_search"),
                "limit": tool_input.get("limit", 20),
            }
        )
    except ValidationError as exc:
        return (
            {"error": f"query_catalog input invalid: {exc.errors()[:3]}"},
            True,
            [],
        )

    try:
        rows = query_catalog(
            bundle,
            query,
            catalog_db_path=catalog_db_path,
            branch_id=branch_id,
        )
    except QueryBuilderError as exc:
        return ({"error": str(exc)}, True, [])

    payload: dict[str, Any] = {"rows": rows}
    if requirement_notes:
        payload["requirement_notes"] = requirement_notes
    return payload, False, rows


@dataclass
class PricingLoopOutcome:
    """Everything `verify.py` and the agent handler need post-loop."""

    submit: SubmitMatchesPayload | None
    steps: int = 0
    # Row dicts (sku + length_ft + category + ...) returned by
    # query_catalog, keyed by item_id. `verify.py` uses this to
    # (1) enforce the candidate-set rule (derive the SKU set),
    # (2) feed the least-waste length picker, and
    # (3) decide `no_match` vs `needs_review` (candidates exist?).
    candidate_rows_by_item: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # item_ids that had at least one `query_catalog` call tagged
    # with that id, INCLUDING calls whose SQL errored or returned
    # zero rows. Used by verify.py to distinguish "agent searched
    # and found nothing" (legit no_match) from "agent never
    # queried" (protocol violation — feedback retry).
    queried_item_ids: set[str] = field(default_factory=set)
    # Each entry: {"tool": "query_catalog", "input": {...}, "rows": int}.
    # For the trace span; verify.py doesn't read it.
    query_history: list[dict[str, Any]] = field(default_factory=list)
    # item_ids the loop ran out of steps on — reported as partial.
    partial_item_ids: list[str] = field(default_factory=list)

    @property
    def candidates_by_item(self) -> dict[str, set[str]]:
        """Backward-compatible SKU-set view over the row dicts.
        `verify.py`'s candidate-set check still reads this."""

        return {
            iid: {r["sku"] for r in rows if "sku" in r}
            for iid, rows in self.candidate_rows_by_item.items()
        }


async def run_pricing_loop(
    llm: Any,
    *,
    input_: PricingLoopInput,
    bus: EventBus,
    budget: Budget,
) -> PricingLoopOutcome:
    """Run the pricing tool loop. One LLM call per step; the loop
    ends when `submit_matches` is called, or when `max_steps` is
    reached without one (soft outcome — unpriced items surface as
    `partial_item_ids`)."""

    items_by_id = {req.item.id: req for req in input_.items}
    items_block = spotlight(_format_items_for_prompt(input_.items), tag=input_.document_tag)
    sys = system_prompt()
    user = user_prompt(items_block, input_.branch_id, feedback=input_.feedback)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

    tools = [QUERY_CATALOG_TOOL, SUBMIT_MATCHES_TOOL]
    query_history: list[dict[str, Any]] = []
    # Full row dicts per item_id, deduped by SKU so repeated
    # queries don't inflate the candidate set.
    candidate_rows_by_item: dict[str, list[dict[str, Any]]] = {iid: [] for iid in items_by_id}
    seen_skus_by_item: dict[str, set[str]] = {iid: set() for iid in items_by_id}
    queried_item_ids: set[str] = set()

    for step in range(input_.max_steps + 1):
        budget.assert_alive()
        response = await llm.call(
            "",  # ignored when messages= is set
            bus=bus,
            run_id=input_.run_id,
            budget=budget,
            system=sys,
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto"},
        )
        budget.charge(turns=1)

        submit_input = _find_submit_matches(response)
        if submit_input is not None:
            try:
                payload = SubmitMatchesPayload.model_validate(submit_input)
            except ValidationError as exc:
                raise PricingShapeError(f"submit_matches payload failed validation: {exc}") from exc
            submitted_ids = {r.item_id for r in payload.results}
            partial_ids = [iid for iid in items_by_id if iid not in submitted_ids]
            return PricingLoopOutcome(
                submit=payload,
                steps=step + 1,
                candidate_rows_by_item=candidate_rows_by_item,
                queried_item_ids=queried_item_ids,
                query_history=query_history,
                partial_item_ids=partial_ids,
            )

        # No submit_matches yet — find query_catalog calls and handle them.
        tool_uses = _tool_use_blocks(response)
        query_calls = [b for b in tool_uses if getattr(b, "name", None) == "query_catalog"]
        if not query_calls:
            raise PricingShapeError(
                "model response had no query_catalog or submit_matches tool_use"
            )

        messages.append(_response_to_assistant_message(response))
        tool_results: list[dict[str, Any]] = []
        for block in query_calls:
            tool_input = dict(getattr(block, "input", {}) or {})
            payload, is_error, rows = _run_query_catalog(
                tool_input,
                items_by_id=items_by_id,
                bundle=input_.bundle,
                catalog_db_path=input_.catalog_db_path,
                branch_id=input_.branch_id,
            )
            iid = tool_input.get("item_id")
            if isinstance(iid, str) and iid in candidate_rows_by_item:
                # The attempt counts even if the query errored or
                # returned zero rows — "the model queried for this
                # item" is the distinction verify.py needs.
                queried_item_ids.add(iid)
                for row in rows:
                    sku = row.get("sku")
                    if sku and sku not in seen_skus_by_item[iid]:
                        seen_skus_by_item[iid].add(sku)
                        candidate_rows_by_item[iid].append(row)
            query_history.append(
                {
                    "tool": "query_catalog",
                    "item_id": iid,
                    "input": tool_input,
                    "rows": len(rows),
                    "is_error": is_error,
                }
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "id", ""),
                    "content": json.dumps(payload),
                    "is_error": is_error,
                }
            )
        messages.append({"role": "user", "content": tool_results})

    # max_steps reached without submit_matches. Return a partial
    # outcome: no SubmitMatchesPayload, every item marked partial.
    return PricingLoopOutcome(
        submit=None,
        steps=input_.max_steps + 1,
        candidate_rows_by_item=candidate_rows_by_item,
        queried_item_ids=queried_item_ids,
        query_history=query_history,
        partial_item_ids=list(items_by_id),
    )


# Keep for future static analysis.
_ = Callable
