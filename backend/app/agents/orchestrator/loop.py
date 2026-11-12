"""Orchestrator loop (SPEC §7.1) — hand-written, explainable.

One-pass version per Antara's Phase-5a brief:
  1. Analyst runs once (same as Phase 3), produces the item list.
  2. CODE applies requirement-scope gap rules immediately
     (SPEC §7.4 refinement) — the model can't skip them.
  3. Model loop with the full tool set (classify / check_gap_rules
     / add_requirements / price_items / flag_conflict / submit_bid)
     until `submit_bid` lands or a budget trips.
  4. Pricing completion pass: any material item triaged `price`
     (or never triaged) without a `price_results` row is priced by
     code via delegate("pricing_agent") if ≥ 30 s remain on the
     deadline; otherwise it becomes a flagged line
     `not_priced:<budget reason>` in assembly.
  5. Assembly: happy path if `submit_bid` passed §9.2 validation,
     otherwise fallback with `generated_by='fallback'`.

Guardrails live in this file:
  - duplicate-tool guard (SPEC §7.3) via canonical JSON of the
    semantically relevant input;
  - turn / token / deadline budgets → BudgetExhausted → fallback;
  - shape-error retry via the same user-prompt feedback shape as
    analyst/pricing (one chance);
  - max_steps cap sized for a normal rfp_messy run (16 turns).

Every tool call is wrapped in a `trace_span`; every result is
summarised in a `tool.called` / `tool.result` event pair.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.agents.analyst.agent import AnalystRequest
from app.agents.orchestrator.prompts import (
    ALL_TOOLS,
    system_prompt,
    user_prompt,
)
from app.agents.orchestrator.tools import (
    OrchestratorContext,
    apply_rule_questions,
    apply_rule_requirements,
    ask_document_analyst,  # code-only kickoff call, not a model tool
    handle_add_requirements,
    handle_check_gap_rules,
    handle_classify_items,
    handle_flag_conflict,
    handle_price_items,
    preview_input,
    preview_result,
)
from app.bid.assemble import (
    AssemblyInput,
    SummaryValidationError,
    build_bid,
    new_bid_id,
    validate_summary,
)
from app.catalog.semantic_layer import SemanticBundle
from app.contracts import AgentName, ItemView
from app.contracts.bid import BranchInfo, ProjectInfo
from app.contracts.common import BidKind, GeneratedBy
from app.contracts.orchestrator import (
    AddRequirementsInput,
    CheckGapRulesInput,
    ClassifyItemsInput,
    FlagConflictInput,
    PriceItemsInput,
    SubmitBidInput,
)
from app.contracts.pricing_agent import PricingItemRequest, PricingRequest
from app.db import repo
from app.db.init import Database
from app.knowledge.matcher import load_rules
from app.runtime.budgets import BudgetExhausted
from app.runtime.delegate import AgentContext, delegate
from app.runtime.events import EventBus
from app.runtime.spotlight import spotlight
from app.runtime.trace import trace_span

MAX_ORCHESTRATOR_TURNS = 16  # Antara: sized for rfp_messy comfort
COMPLETION_PASS_DEADLINE_FLOOR_S = 30.0
PRICING_BATCH_SIZE = 8
SHAPE_ERROR_RETRIES = 1


# ---------------------------------------------------------------------------
# Plan + outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunPlan:
    kind: str  # "pdf" | "text"
    pdf_path: str | None
    pasted_text: str | None
    branch_id: str
    document_tag: str


@dataclass
class OrchestratorOutcome:
    run_id: str
    status: str  # "completed" | "partial" | "failed"
    outcome: str  # "bid_ready" | "no_materials" | "budget_exhausted" | ...
    outcome_detail: str | None
    generated_by: GeneratedBy
    agent_bid_id: str | None
    draft_bid_id: str | None
    partial_reasons: list[str] = field(default_factory=list)
    rejected_claims: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


_TOOL_HANDLERS = {
    "classify_items": (ClassifyItemsInput, handle_classify_items),
    "check_gap_rules": (CheckGapRulesInput, handle_check_gap_rules),
    "add_requirements": (AddRequirementsInput, handle_add_requirements),
    "price_items": (PriceItemsInput, handle_price_items),
    "flag_conflict": (FlagConflictInput, handle_flag_conflict),
}


def _canonical_json(obj: Any) -> str:
    """Sorted-key compact JSON — stable hashable key for the
    duplicate-tool guard."""

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _dedupe_key(tool_name: str, input_dict: dict[str, Any]) -> str:
    """Canonicalise a tool's input for the duplicate guard. For
    most tools we just canonical-JSON the input. `price_items`
    sorts item_ids so the same batch in a different order dedupes."""

    if tool_name == "price_items":
        ids = sorted(input_dict.get("item_ids") or [])
        return _canonical_json({"tool": tool_name, "item_ids": ids})
    if tool_name == "check_gap_rules":
        ids = sorted(input_dict.get("item_ids") or [])
        return _canonical_json({"tool": tool_name, "item_ids": ids})
    if tool_name == "classify_items":
        pairs = sorted(
            (e.get("item_id"), e.get("category")) for e in input_dict.get("entries") or []
        )
        return _canonical_json({"tool": tool_name, "pairs": pairs})
    if tool_name == "add_requirements":
        pairs = sorted(
            (e.get("item_id"), e.get("field"), e.get("value"), tuple(e.get("value_in") or []))
            for e in input_dict.get("entries") or []
        )
        return _canonical_json({"tool": tool_name, "pairs": pairs})
    return _canonical_json({"tool": tool_name, "input": input_dict})


def _tool_use_blocks(response: Any) -> list[Any]:
    content = getattr(response, "content", None)
    if isinstance(content, list):
        return [b for b in content if getattr(b, "type", None) == "tool_use"]
    return []


def _assistant_message_from(response: Any) -> dict[str, Any]:
    """Round-trip the model's response into an assistant turn we
    can feed back as messages[-1]. Preserves tool_use + text
    blocks verbatim."""

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


def _text_from(response: Any) -> str:
    content = getattr(response, "content", None)
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for b in content:
        if getattr(b, "type", None) == "text":
            chunks.append(getattr(b, "text", "") or "")
    return "\n".join(c for c in chunks if c)


async def _emit_tool_pair(
    bus: EventBus,
    *,
    run_id: str,
    tool: str,
    args_preview: str,
    result_preview: str,
    ok: bool,
) -> None:
    await bus.emit(
        type="tool.called",
        run_id=run_id,
        agent=AgentName.orchestrator,
        payload={"tool": tool, "args_preview": args_preview},
    )
    await bus.emit(
        type="tool.result",
        run_id=run_id,
        agent=AgentName.orchestrator,
        payload={"tool": tool, "ok": ok, "result_preview": result_preview},
    )


async def _emit_agent_activity(bus: EventBus, run_id: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    await bus.emit(
        type="agent.activity",
        run_id=run_id,
        agent=AgentName.orchestrator,
        payload={"text": text[:200]},
    )


# ---------------------------------------------------------------------------
# Completion pass
# ---------------------------------------------------------------------------


async def _run_completion_pass(
    ctx: OrchestratorContext,
) -> tuple[int, list[str]]:
    """Any `price`-triaged or untriaged material item without a
    price_results row gets priced via delegate (same pricing
    agent). Returns `(priced_count, partial_reasons)` — on budget
    exhaustion, remaining items are left unpriced and their ids
    are added to partial_reasons via a single bullet."""

    triage_rows = await repo.list_item_triage(ctx.db, run_id=ctx.run_id)
    triage_by_item = {t["item_id"]: t for t in triage_rows}

    outstanding: list[str] = []
    for iid, item in ctx.items_by_id.items():
        if item.get("kind") != "material":
            continue
        tri = triage_by_item.get(iid)
        cat = (tri or {}).get("category")
        if cat not in (None, "price"):
            continue
        if iid in ctx.priced_item_ids:
            continue
        outstanding.append(iid)

    if not outstanding:
        return 0, []

    # Budget guard: SPEC §7.3 floor of 30 s remaining.
    remaining_s = float(ctx.budget.remaining()["deadline_s"])
    if remaining_s < COMPLETION_PASS_DEADLINE_FLOOR_S:
        return 0, [
            f"pricing completion pass skipped ({len(outstanding)} items): "
            f"only {remaining_s:.0f}s left on run deadline"
        ]

    try:
        res = await handle_price_items(
            PriceItemsInput(item_ids=outstanding),
            ctx=ctx,
        )
    except BudgetExhausted as exc:
        return 0, [f"pricing completion pass: budget_exhausted({exc.which})"]
    return int(res.get("ok", 0)), []


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


async def run_orchestrator(
    plan: RunPlan,
    *,
    ctx: AgentContext,
    db: Database,
    bundle: SemanticBundle,
    catalog_db_path: Path,
    llm: Any,
    branch: BranchInfo,
) -> OrchestratorOutcome:
    """One end-to-end pass. Writes `agent_bid` + `draft_bid` via
    `bid/assemble.py` and returns a summary the API layer uses to
    set the run's final status + outcome."""

    bus = ctx.bus
    run_id = ctx.run_id

    # 1. Analyst pass (unchanged from Phase 3).
    analyst_result = await ask_document_analyst(
        AnalystRequest(
            kind=plan.kind,
            pdf_path=plan.pdf_path,
            pasted_text=plan.pasted_text,
            document_tag=plan.document_tag,
        ),
        ctx=ctx,
    )
    items_views = [ItemView.model_validate(it) for it in analyst_result["items"]]
    project = ProjectInfo(
        name=analyst_result.get("project_name"),
        due_date_text=analyst_result.get("due_date_text"),
    )

    # Persist items. They come in verified by the analyst; here
    # we just store them so the loop + assembly can read them back.
    now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    for iv in items_views:
        await repo.insert_item(
            db,
            run_id=run_id,
            item_id=iv.id,
            kind=iv.kind,
            description=iv.description,
            quantity_text=iv.quantity_text,
            unit_raw=iv.unit_raw,
            source_page=iv.source.page,
            source_quote=iv.source.quote,
            read_via=iv.source.read_via,
            verified=iv.source.verified,
            verification_notes=list(iv.notes),
            created_at=now_iso,
        )

    items_rows = await repo.list_items(db, run_id=run_id)
    items_by_id = {r["id"]: r for r in items_rows}

    # Build the orchestrator context now that items exist.
    orch_ctx = OrchestratorContext(
        run_id=run_id,
        branch_id=plan.branch_id,
        document_tag=plan.document_tag,
        db=db,
        bus=bus,
        budget=ctx.budget,
        bundle=bundle,
        catalog_db_path=catalog_db_path,
        gap_rules=load_rules(products_entity=bundle.entities["products"]),
        items_by_id=items_by_id,
    )

    # 2. CODE applies requirement-scope gap rules BEFORE the loop.
    async with trace_span(
        bus,
        run_id=run_id,
        name="apply_rule_requirements",
        label="code-side gap-rule requirements",
        agent=AgentName.orchestrator,
    ):
        await apply_rule_requirements(orch_ctx)

    # 3. Model loop.
    partial_reasons: list[str] = []
    submit_bid_input: SubmitBidInput | None = None
    messages = _build_initial_messages(orch_ctx, items_views, feedback=None)
    seen_dedupe_keys: set[str] = set()
    shape_error_retries_left = SHAPE_ERROR_RETRIES
    last_submit_error: str | None = None

    budget_tripped = False
    for _turn in range(MAX_ORCHESTRATOR_TURNS + 1):
        if budget_tripped:
            break
        try:
            orch_ctx.budget.assert_alive()
        except BudgetExhausted as exc:
            partial_reasons.append(f"budget_exhausted({exc.which})")
            await bus.emit(
                type="budget.exhausted",
                run_id=run_id,
                agent=AgentName.orchestrator,
                payload={"budget": exc.which, "detail": exc.detail},
            )
            break

        try:
            response = await llm.call(
                "",  # ignored when messages= is set
                bus=bus,
                run_id=run_id,
                budget=orch_ctx.budget,
                system=system_prompt(),
                messages=messages,
                tools=ALL_TOOLS,
                tool_choice={"type": "auto"},
            )
        except BudgetExhausted as exc:
            partial_reasons.append(f"budget_exhausted({exc.which})")
            await bus.emit(
                type="budget.exhausted",
                run_id=run_id,
                agent=AgentName.orchestrator,
                payload={"budget": exc.which, "detail": exc.detail},
            )
            break

        try:
            orch_ctx.budget.charge(turns=1)
        except BudgetExhausted as exc:
            # charge() raises when the turn counter overflows
            # exactly here — treat the same as any other
            # mid-loop exhaustion: record + emit + break.
            partial_reasons.append(f"budget_exhausted({exc.which})")
            await bus.emit(
                type="budget.exhausted",
                run_id=run_id,
                agent=AgentName.orchestrator,
                payload={"budget": exc.which, "detail": exc.detail},
            )
            break
        await _emit_agent_activity(bus, run_id, _text_from(response))

        tool_uses = _tool_use_blocks(response)
        if not tool_uses:
            # Nudge once; if the model still doesn't act, fallback.
            if "no-tool" in (last_submit_error or ""):
                partial_reasons.append("no tool_use after nudge")
                break
            last_submit_error = "no-tool"
            messages.append(_assistant_message_from(response))
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Please continue with the tool calls, finishing with `submit_bid`."
                    ),
                }
            )
            continue

        # Append the assistant turn once.
        messages.append(_assistant_message_from(response))
        tool_results: list[dict[str, Any]] = []

        # Process each tool_use in order. submit_bid ends the loop.
        submit_landed_this_turn = False
        for block in tool_uses:
            tool_name = str(getattr(block, "name", "")) or ""
            tool_use_id = str(getattr(block, "id", "")) or ""
            raw_input = dict(getattr(block, "input", {}) or {})

            if tool_name == "submit_bid":
                try:
                    submit_bid_input = SubmitBidInput.model_validate(raw_input)
                    validate_summary(submit_bid_input.summary)
                except (ValidationError, SummaryValidationError) as exc:
                    last_submit_error = str(exc)
                    if shape_error_retries_left > 0:
                        shape_error_retries_left -= 1
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": _preview_err("submit_bid", exc),
                                "is_error": True,
                            }
                        )
                        await _emit_tool_pair(
                            bus,
                            run_id=run_id,
                            tool="submit_bid",
                            args_preview=preview_input("submit_bid", raw_input),
                            result_preview=_short(f"validation: {exc}", 300),
                            ok=False,
                        )
                        continue
                    partial_reasons.append(f"submit_bid rejected twice: {last_submit_error}")
                    submit_bid_input = None
                    submit_landed_this_turn = True
                    break
                await _emit_tool_pair(
                    bus,
                    run_id=run_id,
                    tool="submit_bid",
                    args_preview=preview_input("submit_bid", submit_bid_input),
                    result_preview="ok",
                    ok=True,
                )
                submit_landed_this_turn = True
                break

            if tool_name not in _TOOL_HANDLERS:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": f"unknown tool: {tool_name!r}",
                        "is_error": True,
                    }
                )
                continue

            model_cls, handler = _TOOL_HANDLERS[tool_name]
            try:
                payload = model_cls.model_validate(raw_input)
            except ValidationError as exc:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": _preview_err(tool_name, exc),
                        "is_error": True,
                    }
                )
                await _emit_tool_pair(
                    bus,
                    run_id=run_id,
                    tool=tool_name,
                    args_preview=preview_input(tool_name, raw_input),
                    result_preview=_short(f"validation: {exc}", 300),
                    ok=False,
                )
                continue

            key = _dedupe_key(tool_name, raw_input)
            if key in seen_dedupe_keys:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": "already done: identical call earlier this run",
                        "is_error": False,
                    }
                )
                await _emit_tool_pair(
                    bus,
                    run_id=run_id,
                    tool=tool_name,
                    args_preview=preview_input(tool_name, raw_input),
                    result_preview="duplicate (skipped)",
                    ok=True,
                )
                continue
            seen_dedupe_keys.add(key)

            async with trace_span(
                bus,
                run_id=run_id,
                name=tool_name,
                label=f"tool_use/{tool_name}",
                agent=AgentName.orchestrator,
            ):
                try:
                    result = await handler(payload, ctx=orch_ctx)
                    is_error = False
                except BudgetExhausted as exc:
                    # Pricing agent (or any sub-agent) tripped the
                    # shared budget mid-handler. Record + stop the
                    # loop cleanly so assembly can run with a
                    # fallback summary.
                    partial_reasons.append(f"budget_exhausted({exc.which})")
                    await bus.emit(
                        type="budget.exhausted",
                        run_id=run_id,
                        agent=AgentName.orchestrator,
                        payload={"budget": exc.which, "detail": exc.detail},
                    )
                    budget_tripped = True
                    result = {"ok": False, "error": f"budget_exhausted({exc.which})"}
                    is_error = True
                except Exception as exc:
                    result = {"ok": False, "error": str(exc)}
                    is_error = True

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(result),
                    "is_error": is_error,
                }
            )
            await _emit_tool_pair(
                bus,
                run_id=run_id,
                tool=tool_name,
                args_preview=preview_input(tool_name, payload),
                result_preview=preview_result(tool_name, result),
                ok=not is_error,
            )

        if submit_landed_this_turn and submit_bid_input is not None:
            break
        if submit_landed_this_turn:
            # submit_bid rejected twice — give up and fallback.
            break

        if tool_results:
            messages.append({"role": "user", "content": tool_results})

    else:
        # for-else: ran out of turns without breaking.
        partial_reasons.append(f"turn_limit reached ({MAX_ORCHESTRATOR_TURNS})")

    code_authored_questions: list[dict[str, Any]] = []
    # 4. Pricing completion pass. A BudgetExhausted here is a
    # signal that the run already blew its ceiling; fall through
    # to fallback assembly with that reason recorded.
    async with trace_span(
        bus,
        run_id=run_id,
        name="pricing_completion_pass",
        label="price items the model skipped",
        agent=AgentName.orchestrator,
    ):
        try:
            _, cp_partial = await _run_completion_pass(orch_ctx)
            partial_reasons.extend(cp_partial)
        except BudgetExhausted as exc:
            partial_reasons.append(f"pricing completion pass: budget_exhausted({exc.which})")
            await bus.emit(
                type="budget.exhausted",
                run_id=run_id,
                agent=AgentName.orchestrator,
                payload={"budget": exc.which, "detail": exc.detail},
            )

    # 4a. Code-side gap-rule QUESTION pass (item + bid scope).
    # Runs AFTER the completion pass so bid-scope `absent_match`
    # patterns see the final catalog assignments (and so
    # `apply_rule_questions` reads a consistent requirements set).
    async with trace_span(
        bus,
        run_id=run_id,
        name="apply_rule_questions",
        label="code-side gap-rule questions",
        agent=AgentName.orchestrator,
    ):
        try:
            code_authored_questions = await apply_rule_questions(orch_ctx)
        except BudgetExhausted as exc:
            partial_reasons.append(f"apply_rule_questions: budget_exhausted({exc.which})")

    # 5. Assemble.
    generated_by = GeneratedBy.orchestrator if submit_bid_input else GeneratedBy.fallback

    assembly_input = await _build_assembly_input(
        orch_ctx,
        branch=branch,
        project=project,
        submit=submit_bid_input,
        generated_by=generated_by,
        partial_reasons=partial_reasons,
        code_authored_questions=code_authored_questions,
    )
    result = build_bid(assembly_input)

    # Write any accumulated rejected claims (model + assembly).
    all_rejected = [*orch_ctx.rejected_claims_buffer, *result.rejected_claims]
    for rc in all_rejected:
        await repo.insert_rejected_claim(
            db,
            run_id=run_id,
            kind=str(rc.get("kind") or "unknown"),
            payload=rc.get("payload") or {},
            reason=str(rc.get("reason") or ""),
            created_at=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        )

    agent_bid_id: str | None = None
    draft_bid_id: str | None = None
    if result.agent_bid is not None and result.draft_bid is not None:
        agent_bid_id = new_bid_id(BidKind.agent, 0, run_id=run_id)
        draft_bid_id = new_bid_id(BidKind.draft, 1, run_id=run_id)
        created_at = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        async with db.write() as conn:
            await repo._insert_bid(
                conn,
                bid_id=agent_bid_id,
                run_id=run_id,
                kind=BidKind.agent,
                revision=0,
                data=result.agent_bid.model_dump(mode="json"),
                created_at=created_at,
            )
            await repo._insert_bid(
                conn,
                bid_id=draft_bid_id,
                run_id=run_id,
                kind=BidKind.draft,
                revision=1,
                data=result.draft_bid.model_dump(mode="json"),
                created_at=created_at,
            )
        await bus.emit(
            type="bid.ready",
            run_id=run_id,
            agent=AgentName.orchestrator,
            payload={
                "revision": 1,
                "generated_by": generated_by.value,
                "open_flags": result.agent_bid.totals.open_flags,
            },
        )

    status = "partial" if partial_reasons or generated_by == GeneratedBy.fallback else "completed"
    return OrchestratorOutcome(
        run_id=run_id,
        status=status,
        outcome=result.outcome,
        outcome_detail=result.outcome_detail,
        generated_by=generated_by,
        agent_bid_id=agent_bid_id,
        draft_bid_id=draft_bid_id,
        partial_reasons=partial_reasons,
        rejected_claims=all_rejected,
    )


# ---------------------------------------------------------------------------
# Helpers (prompt + assembly plumbing)
# ---------------------------------------------------------------------------


def _build_initial_messages(
    orch_ctx: OrchestratorContext,
    items: list[ItemView],
    *,
    feedback: list[str] | None,
) -> list[dict[str, Any]]:
    """Open the loop with ONE spotlighted user message carrying
    the item list + a light instruction. `tool_results` reach the
    model on subsequent turns."""

    lines: list[str] = []
    for i, iv in enumerate(items, start=1):
        kind = iv.kind.value if hasattr(iv.kind, "value") else str(iv.kind)
        lines.append(
            f"{i}. item_id={iv.id} kind={kind} page={iv.source.page}\n"
            f"   description: {iv.description}\n"
            f"   quantity_text={iv.quantity_text!r} unit_raw={iv.unit_raw!r}\n"
            f"   quote: {iv.source.quote!r}\n"
            f"   notes: {list(iv.notes)}"
        )
    items_block = spotlight("\n\n".join(lines) or "(no items)", tag=orch_ctx.document_tag)
    user = user_prompt(items_block, orch_ctx.branch_id, feedback=feedback)
    return [{"role": "user", "content": user}]


async def _build_assembly_input(
    orch_ctx: OrchestratorContext,
    *,
    branch: BranchInfo,
    project: ProjectInfo,
    submit: SubmitBidInput | None,
    generated_by: GeneratedBy,
    partial_reasons: list[str],
    code_authored_questions: list[dict[str, Any]],
) -> AssemblyInput:
    """Snapshot the run's state for `bid/assemble.build_bid`."""

    items = await repo.list_items(orch_ctx.db, run_id=orch_ctx.run_id)
    triage_rows = await repo.list_item_triage(orch_ctx.db, run_id=orch_ctx.run_id)
    triage_by_item = {t["item_id"]: t for t in triage_rows}
    requirement_rows = await repo.list_item_requirements(orch_ctx.db, run_id=orch_ctx.run_id)
    requirements_by_item: dict[str, list[dict[str, Any]]] = {}
    for r in requirement_rows:
        requirements_by_item.setdefault(r["item_id"], []).append(r)
    price_results = await repo.latest_price_result_per_item(orch_ctx.db, run_id=orch_ctx.run_id)
    conflicts = await repo.list_conflicts(orch_ctx.db, run_id=orch_ctx.run_id)

    summary = submit.summary if submit else ""
    assumptions = [a.model_dump(mode="python") for a in submit.assumptions] if submit else []
    questions = [q.model_dump(mode="python") for q in submit.contractor_questions] if submit else []
    # Code-side question rules land straight into
    # contractor_questions. If the model already authored a
    # question for the same gap_rule ref_id, the MODEL'S phrasing
    # wins (it saw the run state); otherwise the code template
    # fills in. ref_ids come from `apply_rule_questions`'s
    # updates to `fired_question_rule_ids`.
    model_question_ref_ids = {
        (q["basis"].get("kind"), q["basis"].get("ref_id"))
        for q in questions
        if isinstance(q.get("basis"), dict)
    }
    for q in code_authored_questions:
        key = (q["basis"]["kind"], q["basis"]["ref_id"])
        if key in model_question_ref_ids:
            continue
        questions.append(q)
        model_question_ref_ids.add(key)

    # Gap-rule ids used by `_valid_questions` include question-scope
    # rules recorded by `check_gap_rules` + flag_conflict +
    # `apply_rule_questions`.
    gap_rule_ids = set(orch_ctx.fired_question_rule_ids)

    return AssemblyInput(
        run_id=orch_ctx.run_id,
        branch=branch,
        project=project,
        items=items,
        triage_by_item=triage_by_item,
        requirements_by_item=requirements_by_item,
        price_results_by_item=price_results,
        conflicts=conflicts,
        summary=summary,
        assumptions=assumptions,
        contractor_questions=questions,
        generated_by=generated_by,
        partial_reasons=list(partial_reasons),
        gap_rule_ids=gap_rule_ids,
    )


def _preview_err(tool: str, exc: Exception) -> str:
    msg = f"{tool} rejected: {exc}"
    return msg if len(msg) <= 300 else msg[:299] + "…"


def _short(obj: Any, limit: int = 300) -> str:
    s = str(obj)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# Static-analysis anchors — keep imports live without noqa noise.
_ = (PricingItemRequest, PricingRequest, delegate, secrets)
