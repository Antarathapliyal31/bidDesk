"""Orchestrator tool handlers (SPEC §7.2).

Each tool the orchestrator can call has a handler here. Handlers:
  - take a typed input model from `contracts/orchestrator.py`,
  - write only through `repo.py` (never raw SQL),
  - validate every claim (item id existence, quote verification,
    link freshness) before persistence,
  - return a compact dict suitable for `tool_result` content —
    never the raw item/price blob, which would blow the context.

The tools share an `OrchestratorContext` carrying the run id,
the DB handle, the event bus, the semantic bundle, the catalog
path, a live items-by-id cache, and the trackers (fired gap-rule
ids, rejected claims) that `bid/assemble.py` later consumes.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.agents.analyst.agent import AnalystRequest
from app.agents.pricing.agent import build_pricing_spec
from app.catalog.semantic_layer import SemanticBundle
from app.contracts import AgentName, ItemView
from app.contracts.orchestrator import (
    AddRequirementEntry,
    AddRequirementsInput,
    AskDocumentAnalystInput,
    CheckGapRulesInput,
    ClassifyItemsInput,
    FlagConflictInput,
    PriceItemsInput,
)
from app.contracts.pricing_agent import (
    PricingItemRequest,
    PricingRequest,
    RequirementSpec,
)
from app.db import repo
from app.db.init import Database
from app.knowledge.matcher import (
    GapRule,
    evaluate_item_rules,
    fires_for_bid,
    matches_item,
)
from app.runtime.delegate import AgentContext, delegate, register_agent
from app.runtime.events import EventBus

# ---------------------------------------------------------------------------
# Context carried through the loop
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorContext:
    """Shared state for one orchestrator run. The loop builds this
    once; every tool handler reads/mutates it under the event-bus
    + repo locks. Not frozen — tools append to the trackers."""

    run_id: str
    branch_id: str
    document_tag: str
    db: Database
    bus: EventBus
    budget: Any  # Budget instance
    bundle: SemanticBundle
    catalog_db_path: Path
    gap_rules: list[GapRule]
    # live item cache (populated after the analyst completes)
    items_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Trackers used by assembly
    fired_question_rule_ids: set[str] = field(default_factory=set)
    fired_requirement_rule_ids: set[str] = field(default_factory=set)
    rejected_claims_buffer: list[dict[str, Any]] = field(default_factory=list)
    # Priced items — tracked to drive the pricing-completion pass.
    priced_item_ids: set[str] = field(default_factory=set)

    @property
    def agent_ctx(self) -> AgentContext:
        return AgentContext(run_id=self.run_id, bus=self.bus, budget=self.budget)


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _short(obj: Any, limit: int = 300) -> str:
    """Compact JSON preview for tool.called / tool.result events."""

    try:
        s = json.dumps(obj, default=str, separators=(",", ":"))
    except TypeError:
        s = str(obj)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# classify_items
# ---------------------------------------------------------------------------


async def handle_classify_items(
    payload: ClassifyItemsInput, *, ctx: OrchestratorContext
) -> dict[str, Any]:
    """Validate each entry's item_id, insert into `item_triage`
    atomically, return per-category counts."""

    entries: list[tuple[str, str, str]] = []
    rejected: list[dict[str, Any]] = []
    for e in payload.entries:
        if e.item_id not in ctx.items_by_id:
            rejected.append({"item_id": e.item_id, "reason": "unknown_item_id"})
            continue
        entries.append((e.item_id, e.category, e.reason))
    if entries:
        await repo.insert_item_triage_bulk(
            ctx.db, run_id=ctx.run_id, entries=entries, created_at=_now_iso()
        )
    counts: dict[str, int] = {}
    for _, cat, _reason in entries:
        counts[cat] = counts.get(cat, 0) + 1
    for r in rejected:
        ctx.rejected_claims_buffer.append(
            {"kind": "classify_items", "payload": r, "reason": r["reason"]}
        )
    return {"ok": len(entries), "rejected": len(rejected), "counts": counts}


# ---------------------------------------------------------------------------
# check_gap_rules
# ---------------------------------------------------------------------------


_PRODUCT_CATEGORY_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    # Priority: longer / more-specific patterns first so "fascia
    # trim" beats the generic lumber nominal-size catch.
    ("trim", ("fascia", "trim", "casing", "baseboard")),
    ("roofing", ("shingle", "underlayment", "ridge vent", "roofing", "cedar shake")),
    ("housewrap", ("housewrap", "house wrap", "wrb", "weather-resistive barrier")),
    ("insulation", ("insulation", "r-13", "r-19", "r-30", "batt", "blown-in")),
    ("drywall", ("drywall", "gypsum", "sheetrock")),
    ("sheathing", ("osb", "cdx", "plywood", "sheathing")),
    ("fasteners", ("nail", "screw", "staple", "fastener")),
    ("connectors", ("hanger", "hurricane", "strap", "bracket", "anchor", "tie")),
    # Dimensional lumber catch-all: 2xN, 4x4, 6x6 patterns + the
    # common framing nouns.
    (
        "dimensional_lumber",
        (
            "2x4",
            "2x6",
            "2x8",
            "2x10",
            "2x12",
            "4x4",
            "6x6",
            "stud",
            "joist",
            "rafter",
            "beam",
            "post",
            "header",
            "sill",
            "plate",
            "ledger",
            "ridge",
        ),
    ),
]

# Spec regex library. The matcher reads structural specs from
# `item.nominal_size` / `item.length_ft` / `item.thickness_in` /
# `item.r_value` / `item.fire_rating` to decide `applies_to` +
# `triggered_when` — so these need to be inferable from the raw
# description when the analyst hasn't parsed them. Semantic
# specs (`treatment`, `moisture_resistant`, `grade`, `species`)
# are deliberately NOT inferred: leaving them null is what makes
# requirement-scope rules fire with `missing_condition.not_in`
# / `not_equals` / `is_null`. Code-side gap rules then stamp
# those values into `item_requirements`.

_NOMINAL_SIZE_RE = re.compile(r"\b(2x\d+|4x4|6x6)\b", re.IGNORECASE)
# Matches "x 16'", "x 16 ft", "x16ft" etc. after a nominal size.
_LENGTH_FT_RE = re.compile(
    r"\d+x\d+\s*x\s*(\d+)\s*(?:'|ft\b)",
    re.IGNORECASE,
)
# Fractional inch: "7/16", "1/2", "5/8", "3/4", "23/32", "3/8".
# Only matches when `in`, `inch`, or `drywall/sheathing` is
# nearby; keep the pattern itself loose and let category gating
# in `_infer_specs` decide whether to attach it.
_THICKNESS_IN_RE = re.compile(r"\b(\d+/\d+)(?:\s*in(?:ch)?)?\b")
_R_VALUE_RE = re.compile(r"\br[-\s]?(\d{1,3})\b", re.IGNORECASE)


def _infer_specs(item: dict[str, Any]) -> dict[str, Any]:
    """Infer product category + structural specs from the raw
    description + source_quote so the gap-rule matcher's
    `applies_to` + `triggered_when` clauses can fire. SPEC §7.4
    describes this as the analyst's job; we backfill in the
    orchestrator because the analyst doesn't tag specs yet.

    Keys returned:
      - category: see `_PRODUCT_CATEGORY_KEYWORDS`.
      - nominal_size: the first `2xN` / `4x4` / `6x6` token (upper-
        cased to match the YAML enum).
      - length_ft: from `… x 16'` / `… x 16 ft` patterns.
      - thickness_in: from `7/16` / `1/2` / `5/8` / `3/4` /
        `23/32` / `3/8` when the item is drywall / sheathing.
      - r_value: from `R-19` / `R19` patterns.
      - fire_rating: `type_x` when "Type X" appears in text.

    `treatment`, `moisture_resistant`, `grade`, and `species` are
    intentionally LEFT NULL — the requirement-scope gap rules
    (pt_sill_plates_on_concrete, pt_posts_in_ground,
    wet_area_drywall_mr, etc.) fire on exactly those missing
    specs and stamp them into `item_requirements`.
    """

    haystack = " ".join(
        (
            (item.get("description") or "").lower(),
            (item.get("source_quote") or "").lower(),
        )
    )

    category: str | None = None
    for cat, keywords in _PRODUCT_CATEGORY_KEYWORDS:
        if any(kw in haystack for kw in keywords):
            category = cat
            break

    nominal_size: str | None = None
    m = _NOMINAL_SIZE_RE.search(haystack)
    if m:
        # Normalise to the lower-case `2x6` / `4x4` form the
        # products.yaml enum uses.
        nominal_size = m.group(1).lower().replace("x", "x")

    length_ft: int | None = None
    m = _LENGTH_FT_RE.search(haystack)
    if m:
        try:
            length_ft = int(m.group(1))
        except ValueError:
            length_ft = None

    thickness_in: str | None = None
    if category in ("drywall", "sheathing"):
        m = _THICKNESS_IN_RE.search(haystack)
        if m:
            thickness_in = m.group(1)

    r_value: int | None = None
    m = _R_VALUE_RE.search(haystack)
    if m:
        try:
            r_value = int(m.group(1))
        except ValueError:
            r_value = None

    fire_rating: str | None = None
    if "type x" in haystack or "typex" in haystack:
        fire_rating = "type_x"

    return {
        "category": category,
        "nominal_size": nominal_size,
        "length_ft": length_ft,
        "thickness_in": thickness_in,
        "r_value": r_value,
        "fire_rating": fire_rating,
    }


def _item_dict_for_matcher(item: dict[str, Any], triage: dict[str, Any] | None) -> dict[str, Any]:
    """Shape an item for `matcher.matches_item`. The matcher reads
    `category`, parsed `specs`, and `source_quote` (SPEC §7.4).

    Structural specs are inferred via `_infer_specs`. Semantic
    specs (`treatment`, `moisture_resistant`, `grade`, `species`)
    stay null in this view so requirement-scope rules fire; code
    applies those requirements into `item_requirements`, and
    `_merge_requirements_into_item_view` overlays them before the
    POST-PRICING matcher pass.

    `triage` is accepted for SPEC §7.4 symmetry but unused.
    """

    _ = triage
    inferred = _infer_specs(item)
    specs = item.get("specs") or {}
    return {
        "category": specs.get("category") or inferred["category"],
        "nominal_size": specs.get("nominal_size") or inferred["nominal_size"],
        "length_ft": specs.get("length_ft") or inferred["length_ft"],
        "thickness_in": specs.get("thickness_in") or inferred["thickness_in"],
        "grade": specs.get("grade"),
        "species": specs.get("species"),
        "treatment": specs.get("treatment"),
        "fire_rating": specs.get("fire_rating") or inferred["fire_rating"],
        "moisture_resistant": specs.get("moisture_resistant"),
        "r_value": specs.get("r_value") or inferred["r_value"],
        "source_quote": item.get("source_quote"),
        "description": item.get("description"),
    }


def _merge_requirements_into_item_view(
    item_view: dict[str, Any], requirement_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Overlay recorded requirements onto a matcher-view item dict
    so POST-pricing rules see code-stamped specs. A requirement
    with `value_in` picks the first value (same convention as the
    matcher's own `evaluate_item_rules`)."""

    merged = dict(item_view)
    for req in requirement_rows:
        field = req.get("field")
        if not field:
            continue
        parsed = json.loads(req["value_json"])
        value = parsed[0] if isinstance(parsed, list) else parsed
        if value is None:
            continue
        merged[field] = value
    return merged


async def apply_rule_questions(ctx: OrchestratorContext) -> list[dict[str, Any]]:
    """POST-pricing deterministic gap-rule pass.

    Reads items + item_requirements, overlays requirements onto
    each item's matcher view (so bid-scope rules that need
    `treatment` etc. see them), and evaluates:

      - item-scope question rules (one per matching item);
      - bid-scope rules (action='question') once per rule.

    Returns a list of `{text, basis}` dicts ready for assembly to
    drop straight into `contractor_questions`. Also updates
    `ctx.fired_question_rule_ids` so model-authored
    `basis.kind='gap_rule'` references pointing at THESE rules
    survive assembly's link validation.
    """

    requirement_rows = await repo.list_item_requirements(ctx.db, run_id=ctx.run_id)
    reqs_by_item: dict[str, list[dict[str, Any]]] = {}
    for r in requirement_rows:
        reqs_by_item.setdefault(r["item_id"], []).append(r)
    triage_rows = await repo.list_item_triage(ctx.db, run_id=ctx.run_id)
    triage_by_item = {t["item_id"]: t for t in triage_rows}

    code_questions: list[dict[str, Any]] = []
    seen_item_question_keys: set[tuple[str, str]] = set()
    seen_bid_rule_ids: set[str] = set()

    # Build the merged view per item for bid-scope matching.
    merged_views: list[dict[str, Any]] = []
    for iid, item in ctx.items_by_id.items():
        view = _item_dict_for_matcher(item, triage_by_item.get(iid))
        view = _merge_requirements_into_item_view(view, reqs_by_item.get(iid, []))
        merged_views.append(view)
        # Item-scope QUESTION rules fire per item.
        context_text = item.get("source_quote") or ""
        for rule in ctx.gap_rules:
            if rule.scope != "item" or rule.action != "question":
                continue
            if matches_item(rule, view, context_text):
                key = (rule.id, iid)
                if key in seen_item_question_keys:
                    continue
                seen_item_question_keys.add(key)
                text = _fill_question_template(rule.question_template, rule.title, view)
                code_questions.append(
                    {
                        "text": text,
                        "basis": {"kind": "gap_rule", "ref_id": rule.id},
                    }
                )
                ctx.fired_question_rule_ids.add(rule.id)

    # Bid-scope rules (question action) — matched against the
    # merged view set so absent_match can see pricing output too.
    for rule in ctx.gap_rules:
        if rule.scope != "bid" or rule.action != "question":
            continue
        if rule.id in seen_bid_rule_ids:
            continue
        if fires_for_bid(rule, merged_views):
            seen_bid_rule_ids.add(rule.id)
            text = (rule.question_template or rule.title or "").strip()
            code_questions.append(
                {
                    "text": text or f"Review required: {rule.id}",
                    "basis": {"kind": "gap_rule", "ref_id": rule.id},
                }
            )
            ctx.fired_question_rule_ids.add(rule.id)

    return code_questions


def _fill_question_template(template: str | None, title: str, item: dict[str, Any]) -> str:
    """Minimal placeholder expansion. Today the templates in
    gap_rules.yaml are self-contained — if they ever carry
    `{nominal_size}` / `{description}` tokens, best-effort fill
    from the merged item view."""

    base = (template or title or "").strip()
    if not base:
        return "Review required"
    # Avoid KeyError when the template references a field we
    # don't have; fall back to the raw template.
    try:
        return base.format(**item)
    except (KeyError, IndexError):
        return base


def _apply_item_rules(
    ctx: OrchestratorContext,
    item: dict[str, Any],
    triage: dict[str, Any] | None,
) -> tuple[list[GapRule], list[GapRule]]:
    """Return `(fired_requirements, fired_questions)` for one
    item. Pure call — persistence happens one layer up."""

    item_dict = _item_dict_for_matcher(item, triage)
    context_text = item.get("source_quote") or ""
    return evaluate_item_rules(ctx.gap_rules, item_dict, context_text)


async def handle_check_gap_rules(
    payload: CheckGapRulesInput, *, ctx: OrchestratorContext
) -> dict[str, Any]:
    """Return a compact summary of applicable rules keyed by
    item_id. Also runs bid-scope rules against the currently-
    triaged items so the model sees the missing-companion
    prompts once pricing has happened."""

    triage_rows = await repo.list_item_triage(ctx.db, run_id=ctx.run_id)
    triage_by_item = {t["item_id"]: t for t in triage_rows}

    target_ids: list[str] = payload.item_ids or list(ctx.items_by_id.keys())

    item_rules_payload: list[dict[str, Any]] = []
    for iid in target_ids:
        if iid not in ctx.items_by_id:
            continue
        reqs, questions = _apply_item_rules(ctx, ctx.items_by_id[iid], triage_by_item.get(iid))
        if not (reqs or questions):
            continue
        item_rules_payload.append(
            {
                "item_id": iid,
                "requirements": [
                    {
                        "id": r.id,
                        "title": r.title,
                        "field": r.requirement.field if r.requirement else None,
                        "value": r.requirement.value if r.requirement else None,
                        "value_in": r.requirement.value_in if r.requirement else None,
                        "code_ref": r.code_ref,
                        "basis": r.basis,
                    }
                    for r in reqs
                ],
                "questions": [
                    {
                        "id": q.id,
                        "title": q.title,
                        "question_template": q.question_template,
                        "code_ref": q.code_ref,
                        "basis": q.basis,
                    }
                    for q in questions
                ],
            }
        )

    # Bid-scope rules — matched against the triaged items visible so
    # far. (Bid-scope rules that reference pricing fields still match
    # on pre-price attributes for the base test; the `absent_match`
    # pass in assembly re-checks after pricing lands.)
    bid_items = [
        _item_dict_for_matcher(ctx.items_by_id[iid], triage_by_item.get(iid))
        for iid in ctx.items_by_id
    ]
    bid_rules_payload: list[dict[str, Any]] = []
    for rule in ctx.gap_rules:
        if rule.scope != "bid":
            continue
        if fires_for_bid(rule, bid_items):
            bid_rules_payload.append(
                {
                    "id": rule.id,
                    "title": rule.title,
                    "action": rule.action,
                    "question_template": rule.question_template,
                    "code_ref": rule.code_ref,
                    "basis": rule.basis,
                }
            )
            if rule.action == "question":
                ctx.fired_question_rule_ids.add(rule.id)

    # Record every question rule that fired at the item level.
    for entry in item_rules_payload:
        for q in entry["questions"]:
            ctx.fired_question_rule_ids.add(q["id"])

    return {
        "ok": True,
        "item_rules": item_rules_payload,
        "bid_rules": bid_rules_payload,
    }


# ---------------------------------------------------------------------------
# ask_document_analyst — Phase 5a stub
# ---------------------------------------------------------------------------


async def handle_ask_document_analyst(
    payload: AskDocumentAnalystInput, *, ctx: OrchestratorContext
) -> dict[str, Any]:
    """Phase-5a stub — the current Analyst spec runs the extractor
    on every call. Phase 5c adds a specialised Q&A mode. For now
    the model receives a short acknowledgement + the item list it
    already saw, so it continues the flow."""

    _ = payload, ctx
    return {
        "ok": True,
        "detail": "Q&A mode not implemented in Phase 5a; rely on extracted items.",
    }


# ---------------------------------------------------------------------------
# add_requirements
# ---------------------------------------------------------------------------


def _requirement_shape_valid(entry: AddRequirementEntry) -> str | None:
    has_value = entry.value is not None
    has_value_in = entry.value_in is not None
    if has_value == has_value_in:
        return "exactly one of 'value' / 'value_in' must be set"
    if has_value_in and not entry.value_in:
        return "'value_in' must be non-empty"
    return None


async def _persist_requirements(
    ctx: OrchestratorContext,
    entries: list[AddRequirementEntry],
    *,
    created_by: str,
) -> dict[str, Any]:
    """Shared insert path. Verifies field + value against the
    semantic layer and writes under one `db.write()` transaction."""

    from app.contracts.bid import RequirementView
    from app.contracts.common import SourceRef as _SourceRef

    ok_count = 0
    rejected: list[dict[str, Any]] = []
    now = _now_iso()
    async with ctx.db.write() as conn:
        for e in entries:
            if e.item_id not in ctx.items_by_id:
                rejected.append({"entry": e.model_dump(), "reason": "unknown_item_id"})
                continue
            shape_err = _requirement_shape_valid(e)
            if shape_err:
                rejected.append({"entry": e.model_dump(), "reason": shape_err})
                continue
            prod = ctx.bundle.entities.get("products")
            if prod is None or e.field not in prod.fields:
                rejected.append({"entry": e.model_dump(), "reason": "unknown_field"})
                continue
            field_def = prod.fields[e.field]
            if not field_def.matchable:
                rejected.append({"entry": e.model_dump(), "reason": "field_not_matchable"})
                continue
            if field_def.values:
                allowed = set(field_def.values)
                candidate_values = [e.value] if e.value is not None else list(e.value_in or [])
                bad = [v for v in candidate_values if v not in allowed]
                if bad:
                    rejected.append(
                        {"entry": e.model_dump(), "reason": f"value_not_allowed: {bad}"}
                    )
                    continue
            source = _SourceRef(
                page=e.source_page,
                quote=e.source_quote,
                read_via="text" if e.source_page else None,
                verified=bool(e.source_page and e.source_quote),
            )
            try:
                req = RequirementView(
                    field=e.field,
                    value=e.value,
                    value_in=list(e.value_in) if e.value_in else None,
                    reason=e.reason,
                    source=source,
                )
            except Exception as exc:
                rejected.append({"entry": e.model_dump(), "reason": f"contract: {exc}"})
                continue
            await repo._insert_item_requirement(
                conn,
                run_id=ctx.run_id,
                item_id=e.item_id,
                requirement=req,
                created_by=created_by,  # type: ignore[arg-type]
                created_at=now,
            )
            ok_count += 1
    for r in rejected:
        ctx.rejected_claims_buffer.append(
            {"kind": "add_requirements", "payload": r["entry"], "reason": r["reason"]}
        )
    return {"ok": ok_count, "rejected": len(rejected)}


async def handle_add_requirements(
    payload: AddRequirementsInput, *, ctx: OrchestratorContext
) -> dict[str, Any]:
    return await _persist_requirements(ctx, payload.entries, created_by="orchestrator")


# ---------------------------------------------------------------------------
# CODE-SIDE: apply fired requirement gap rules before pricing
# ---------------------------------------------------------------------------


async def apply_rule_requirements(ctx: OrchestratorContext) -> dict[str, Any]:
    """Run the matcher's requirement rules over every item; write
    the fired requirements into `item_requirements` directly
    (created_by='orchestrator'). CODE applies these — the model
    can add EXTRA requirements via `add_requirements` but cannot
    skip these (Antara refinement)."""

    triage_rows = await repo.list_item_triage(ctx.db, run_id=ctx.run_id)
    triage_by_item = {t["item_id"]: t for t in triage_rows}

    entries: list[AddRequirementEntry] = []
    for iid, item in ctx.items_by_id.items():
        reqs, _questions = _apply_item_rules(ctx, item, triage_by_item.get(iid))
        for rule in reqs:
            ctx.fired_requirement_rule_ids.add(rule.id)
            req = rule.requirement
            if req is None:
                continue
            value: Any
            value_in: list[Any] | None = None
            if req.value is not None:
                value = req.value
            else:
                value = None
                value_in = list(req.value_in or [])
            entries.append(
                AddRequirementEntry(
                    item_id=iid,
                    field=req.field,
                    value=value,
                    value_in=value_in,
                    reason=f"gap_rule {rule.id}: {rule.title}",
                    source_page=item.get("source_page"),
                    source_quote=item.get("source_quote"),
                )
            )
    if not entries:
        return {"ok": 0, "rejected": 0, "applied_rule_ids": []}
    result = await _persist_requirements(ctx, entries, created_by="orchestrator")
    result["applied_rule_ids"] = sorted(ctx.fired_requirement_rule_ids)
    return result


# ---------------------------------------------------------------------------
# price_items — batches ≤ 8, Semaphore(2)
# ---------------------------------------------------------------------------


_PRICING_BATCH_SIZE = 8
_PRICING_CONCURRENCY = 2


def _requirement_specs_for(
    item_id: str, requirement_rows: list[dict[str, Any]]
) -> list[RequirementSpec]:
    out: list[RequirementSpec] = []
    for r in requirement_rows:
        if r["item_id"] != item_id:
            continue
        parsed = json.loads(r["value_json"])
        value = parsed[0] if isinstance(parsed, list) else parsed
        if value is None:
            continue
        out.append(RequirementSpec(field=r["field"], value=value))
    return out


def _item_view_from_row(item: dict[str, Any]) -> ItemView:
    from app.contracts.common import ItemKind, SourceRef

    kind = ItemKind(item["kind"])
    src = SourceRef(
        page=item.get("source_page"),
        quote=item.get("source_quote"),
        read_via=item.get("read_via"),
        verified=bool(item.get("verified", False)),
    )
    return ItemView(
        id=item["id"],
        kind=kind,
        description=item["description"],
        quantity_text=item.get("quantity_text"),
        unit_raw=item.get("unit_raw"),
        source=src,
        notes=list(item.get("notes") or []),
    )


def register_pricing_for_run(
    llm: Any,
    *,
    bundle: SemanticBundle,
    catalog_db_path: Path,
) -> None:
    """Shim used by the loop at setup time — same spec builder as
    Phase 4. Keeps `tools.py` the single place the orchestrator
    reaches into pricing."""

    register_agent(build_pricing_spec(llm, bundle=bundle, catalog_db_path=catalog_db_path))


async def _price_one_batch(
    *,
    ctx: OrchestratorContext,
    batch: list[PricingItemRequest],
    sem: asyncio.Semaphore,
) -> list[dict[str, Any]]:
    """Delegate one ≤8-item batch to the pricing agent under the
    concurrency semaphore. Writes `price_results` rows + returns
    compact per-item summaries for the model."""

    async with sem:
        request = PricingRequest(branch_id=ctx.branch_id, items=batch)
        agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx.agent_ctx)

    out: list[dict[str, Any]] = []
    now = _now_iso()
    if agent_result.status != "ok" or agent_result.output is None:
        # Handler-level failure — every item in the batch becomes
        # a `failed` row so assembly surfaces them.
        for req in batch:
            row = {
                "status": "failed",
                "verify_notes": ["agent_error"],
                "confidence": "low",
                "confidence_reasons": ["pricing agent failed"],
            }
            await repo.insert_price_result(
                ctx.db,
                run_id=ctx.run_id,
                item_id=req.item.id,
                row=row,
                created_at=now,
            )
            ctx.priced_item_ids.add(req.item.id)
            out.append({"item_id": req.item.id, "status": "failed"})
        return out

    output = agent_result.output
    results = list(output.results)
    item_errors_by_id = {e.ref: e for e in output.item_errors}

    for r in results:
        iid = r.item_id
        row: dict[str, Any] = {
            "status": r.status,
            "sku": r.priced.sku,
            "product_name": r.priced.product_name,
            "sale_unit": r.priced.sale_unit,
            "sale_qty": str(r.priced.sale_qty) if r.priced.sale_qty is not None else None,
            "conversion_path": r.priced.conversion_path,
            "unit_price_cents": r.priced.unit_price_cents,
            "branch_on_hand": r.stock.get("on_hand") if r.stock else None,
            "shortfall": r.priced.shortfall,
            "transfer_options": (r.stock or {}).get("transfer_options") or [],
            "assumptions": [a.model_dump(mode="python") for a in r.assumptions],
            "attribute_mismatches": list(r.attribute_mismatches),
            "verify_notes": list(r.verify_notes),
            "is_substitute": r.is_substitute,
            "substitute_reason": r.substitute_reason,
            "confidence": r.priced.confidence,
            "confidence_reasons": list(r.priced.confidence_reasons),
            "searched": None,
        }
        # Per Antara: each item_error folds into the price row as
        # a `not_priced:<kind>` verify_note so bid assembly surfaces
        # it as an open flag that blocks finalize.
        if iid in item_errors_by_id:
            err = item_errors_by_id[iid]
            row["verify_notes"] = [*row["verify_notes"], f"not_priced:{err.kind}"]
        await repo.insert_price_result(
            ctx.db, run_id=ctx.run_id, item_id=iid, row=row, created_at=now
        )
        ctx.priced_item_ids.add(iid)
        out.append(
            {
                "item_id": iid,
                "status": r.status,
                "sku": r.priced.sku,
                "sale_qty": row["sale_qty"],
                "sale_unit": r.priced.sale_unit,
                "confidence": r.priced.confidence,
                "shortfall": r.priced.shortfall,
            }
        )
    return out


async def handle_price_items(
    payload: PriceItemsInput, *, ctx: OrchestratorContext
) -> dict[str, Any]:
    """Build PricingItemRequests by joining items + requirements
    in code (never the model), batch ≤8 lines, delegate under a
    Semaphore(2). Writes `price_results` for every submitted item."""

    valid_ids: list[str] = []
    rejected: list[dict[str, Any]] = []
    for iid in payload.item_ids:
        if iid not in ctx.items_by_id:
            rejected.append({"item_id": iid, "reason": "unknown_item_id"})
            continue
        valid_ids.append(iid)
    if not valid_ids:
        return {"ok": 0, "rejected": len(rejected), "results": []}

    requirement_rows = await repo.list_item_requirements(ctx.db, run_id=ctx.run_id)

    requests: list[PricingItemRequest] = []
    for iid in valid_ids:
        item = ctx.items_by_id[iid]
        requests.append(
            PricingItemRequest(
                item=_item_view_from_row(item),
                requirements=_requirement_specs_for(iid, requirement_rows),
            )
        )

    batches: list[list[PricingItemRequest]] = [
        requests[i : i + _PRICING_BATCH_SIZE] for i in range(0, len(requests), _PRICING_BATCH_SIZE)
    ]
    sem = asyncio.Semaphore(_PRICING_CONCURRENCY)
    gathered = await asyncio.gather(*(_price_one_batch(ctx=ctx, batch=b, sem=sem) for b in batches))
    results = [row for batch_rows in gathered for row in batch_rows]
    return {
        "ok": len(results),
        "rejected": len(rejected),
        "batches": len(batches),
        "results": results,
    }


# ---------------------------------------------------------------------------
# flag_conflict
# ---------------------------------------------------------------------------


_CONFLICT_PREFIX = "conflict_"


def _page_quote_verified(page: int | None, quote: str | None, read_via: str | None) -> bool:
    return bool(page and (quote or "").strip() and read_via == "text")


async def handle_flag_conflict(
    payload: FlagConflictInput, *, ctx: OrchestratorContext
) -> dict[str, Any]:
    """Persist the conflict after verifying both sides' citations.
    Vision-side contradictions route to contractor_questions
    instead (DECISIONS #16). Unknown item ids or unverifiable
    sides → rejected_claims."""

    side_a = payload.side_a
    side_b = payload.side_b

    vision_or_unverified = False
    for side in (side_a, side_b):
        src = side.source
        if src.read_via == "vision" or not _page_quote_verified(src.page, src.quote, src.read_via):
            vision_or_unverified = True

    if vision_or_unverified:
        question_ref_id = f"flag_{secrets.token_hex(4)}"
        ctx.fired_question_rule_ids.add(question_ref_id)
        ctx.rejected_claims_buffer.append(
            {
                "kind": "flag_conflict_routed_to_question",
                "payload": payload.model_dump(mode="python"),
                "reason": "vision_or_unverified_side",
            }
        )
        return {
            "ok": True,
            "routed": "contractor_question",
            "question_ref_id": question_ref_id,
        }

    for side in (side_a, side_b):
        if side.item_id is not None and side.item_id not in ctx.items_by_id:
            ctx.rejected_claims_buffer.append(
                {
                    "kind": "flag_conflict",
                    "payload": payload.model_dump(mode="python"),
                    "reason": f"unknown_item_id:{side.item_id}",
                }
            )
            return {"ok": False, "rejected": "unknown_item_id"}

    conflict_id = _CONFLICT_PREFIX + secrets.token_hex(4)
    await repo.insert_conflict(
        ctx.db,
        conflict_id=conflict_id,
        run_id=ctx.run_id,
        conflict_type=payload.type.value,
        side_a={
            "item_id": side_a.item_id,
            "source": side_a.source.model_dump(mode="python"),
        },
        side_b={
            "item_id": side_b.item_id,
            "source": side_b.source.model_dump(mode="python"),
        },
        explanation=payload.explanation,
        created_at=_now_iso(),
    )
    return {"ok": True, "conflict_id": conflict_id}


# ---------------------------------------------------------------------------
# Analyst delegate (used once before the model loop starts)
# ---------------------------------------------------------------------------


async def ask_document_analyst(
    request: AnalystRequest,
    *,
    ctx: AgentContext,
) -> dict[str, Any]:
    """Preserved from Phase 3 — runner calls this once at the start
    to produce the item list, then the model loop takes over."""

    result = await delegate(AgentName.document_analyst, request, ctx=ctx)
    if result.status != "ok" or result.output is None:
        raise RuntimeError(
            f"Document Analyst returned status={result.status!r}: "
            f"{[e.message for e in result.errors]}"
        )
    return result.output.model_dump(mode="python")


# ---------------------------------------------------------------------------
# SSE preview helpers (kept tiny per SPEC §10.3 size caps)
# ---------------------------------------------------------------------------


def preview_input(tool: str, inp: Any) -> str:
    _ = tool
    try:
        serialisable = inp.model_dump(mode="json") if hasattr(inp, "model_dump") else inp
    except Exception:
        serialisable = str(inp)
    return _short(serialisable, limit=300)


def preview_result(tool: str, result: dict[str, Any]) -> str:
    _ = tool
    return _short(result, limit=300)


_ = matches_item  # keep import live for future direct-use helpers
