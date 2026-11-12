"""Real-API smoke for the orchestrator (Phase 5a).

Runs `rfp_messy.pdf` through the full pipeline (Document Analyst
+ Pricing Agent + Orchestrator loop + bid assembly) against the
live Anthropic API. Report-only — no assertions.

Credentials policy (same as smoke_analyst / smoke_pricing):
  - key read ONLY from `ANTHROPIC_API_KEY` in `backend/.env`
  - no keychain / OAuth cache / CLI flag
  - never printed or logged

Prints:
  - outcome.status, outcome.outcome, outcome.generated_by,
    outcome.partial_reasons
  - turns used (consumed from the budget) vs 16 cap
  - tokens (runs.tokens_in / tokens_out)
  - wall time
  - items priced vs expected_sku hits
  - requirement rule ids fired vs
    samples/expected/rfp_messy.json::expected_requirement_rule_ids
  - question rule ids fired vs
    samples/expected/rfp_messy.json::expected_question_rule_ids
  - conflicts found + comparison vs expected
  - bid totals.open_flags
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Isolate from any ambient ANTHROPIC_API_KEY — the only source is .env.
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.agents.analyst.agent import register_default_analyst
from app.agents.orchestrator.loop import MAX_ORCHESTRATOR_TURNS, RunPlan, run_orchestrator
from app.agents.orchestrator.tools import register_pricing_for_run
from app.catalog.semantic_layer import load_bundle
from app.config import get_settings
from app.contracts import InputKind
from app.contracts.bid import BranchInfo
from app.db import repo
from app.db.init import open_app_db
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import AnthropicLLM

_BACKEND = Path(__file__).resolve().parents[1]
_SAMPLES_ROOT = _BACKEND.parent / "samples"
_BUNDLE_DIR = _BACKEND / "app" / "catalog" / "semantic"
_CATALOG_DB = _BACKEND / "data" / "catalog.db"
_FIXED_NOW = "2026-11-12T12:00:00+00:00"


def _expected_messy() -> dict[str, Any]:
    return json.loads((_SAMPLES_ROOT / "expected" / "rfp_messy.json").read_text())


def _ensure_env_only_key() -> None:
    settings = get_settings()
    key = settings.anthropic_api_key
    if key is None or not key.get_secret_value():
        print(
            "ANTHROPIC_API_KEY is not set in backend/.env.\n"
            "Put your key in backend/.env (ANTHROPIC_API_KEY=sk-ant-...) and re-run.\n"
            "This script does not read keychains or OAuth caches.",
            file=sys.stderr,
        )
        sys.exit(2)


def _ensure_catalog() -> None:
    if not _CATALOG_DB.exists():
        print(
            f"Catalog not built at {_CATALOG_DB}.\nRun `make seed` first.",
            file=sys.stderr,
        )
        sys.exit(2)


async def _collect_fired_rule_ids(db, run_id: str) -> tuple[set[str], set[str]]:
    """Reconstruct fired rule ids from the DB. Requirement rules
    land in `item_requirements.reason` as 'gap_rule {id}: title';
    question rules are visible on contractor_questions in the bid
    (basis.ref_id). This is a best-effort surface for the report —
    the orchestrator's in-memory trackers are richer."""

    req_rule_ids: set[str] = set()
    cur = await db.read_conn.execute(
        "SELECT reason FROM item_requirements WHERE run_id = ?", (run_id,)
    )
    try:
        rows = await cur.fetchall()
    finally:
        await cur.close()
    for r in rows:
        reason = str(r[0] or "")
        if reason.startswith("gap_rule "):
            # "gap_rule pt_sill_plates_on_concrete: Title"
            rest = reason[len("gap_rule ") :]
            rule_id = rest.split(":", 1)[0].strip()
            if rule_id:
                req_rule_ids.add(rule_id)

    # Question rule ids come from the bid's contractor_questions.
    question_rule_ids: set[str] = set()
    cur = await db.read_conn.execute(
        "SELECT data_json FROM bids WHERE run_id = ? AND kind = 'agent'", (run_id,)
    )
    try:
        row = await cur.fetchone()
    finally:
        await cur.close()
    if row:
        bid = json.loads(row[0])
        for q in bid.get("contractor_questions", []) or []:
            basis = q.get("basis") or {}
            if basis.get("kind") == "gap_rule":
                question_rule_ids.add(str(basis.get("ref_id") or ""))
    return req_rule_ids, question_rule_ids


async def _collect_bid_summary(db, run_id: str) -> dict[str, Any]:
    """Pull the agent bid's top-level stats for the report."""

    cur = await db.read_conn.execute(
        "SELECT data_json FROM bids WHERE run_id = ? AND kind = 'agent'", (run_id,)
    )
    try:
        row = await cur.fetchone()
    finally:
        await cur.close()
    if not row:
        return {}
    bid = json.loads(row[0])
    totals = bid.get("totals") or {}
    return {
        "lines": len(bid.get("lines") or []),
        "exclusions": len(bid.get("exclusions") or []),
        "labor": len(bid.get("labor") or []),
        "unreadable": len(bid.get("unreadable") or []),
        "conflicts": [c.get("type") for c in (bid.get("conflicts") or [])],
        "assumptions": len(bid.get("assumptions") or []),
        "contractor_questions": len(bid.get("contractor_questions") or []),
        "open_flags": totals.get("open_flags"),
        "priced_lines": totals.get("priced_lines"),
        "subtotal_cents": totals.get("subtotal_cents"),
        "total_cents": totals.get("total_cents"),
        "summary": bid.get("summary"),
    }


async def _run_once() -> dict[str, Any]:
    expected = _expected_messy()
    start = time.monotonic()
    bundle = load_bundle(_BUNDLE_DIR, _CATALOG_DB)

    with tempfile.TemporaryDirectory() as td:
        app_db = Path(td) / "app.db"
        async with open_app_db(app_db, now_iso=_FIXED_NOW) as (db, _):
            bus = EventBus(db)
            # Run-level turn budget is intentionally higher than
            # `MAX_ORCHESTRATOR_TURNS` — pricing-agent + analyst
            # loops also charge turns against the same counter, so
            # 16 orchestrator turns can easily see 40+ total.
            budget = Budget(turns=80, tokens=400_000, deadline_s=240.0)
            budget.start()
            run_id = "run_smoke_orch"
            await repo.insert_run(
                db,
                run_id=run_id,
                branch_id="BR-NJ-01",
                input_kind=InputKind.pdf,
                input_name="rfp_messy.pdf",
                created_at=_FIXED_NOW,
            )
            ctx = AgentContext(run_id=run_id, bus=bus, budget=budget)

            unregister_all()
            analyst_llm = AnthropicLLM(db=db)
            pricing_llm = AnthropicLLM(db=db)
            orchestrator_llm = AnthropicLLM(db=db)
            register_default_analyst(analyst_llm)
            register_pricing_for_run(pricing_llm, bundle=bundle, catalog_db_path=_CATALOG_DB)

            branch = BranchInfo(id="BR-NJ-01", name="NJ Branch", tax_rate_ppm=66_250)
            try:
                outcome = await run_orchestrator(
                    RunPlan(
                        kind="pdf",
                        pdf_path=str(_SAMPLES_ROOT / "rfp_messy.pdf"),
                        pasted_text=None,
                        branch_id="BR-NJ-01",
                        document_tag="document_smoke_orch",
                    ),
                    ctx=ctx,
                    db=db,
                    bundle=bundle,
                    catalog_db_path=_CATALOG_DB,
                    llm=orchestrator_llm,
                    branch=branch,
                )
            except Exception as exc:
                elapsed = time.monotonic() - start
                return {
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "wall_time_s": round(elapsed, 2),
                }

            cur = await db.read_conn.execute(
                "SELECT tokens_in, tokens_out FROM runs WHERE id = ?", (run_id,)
            )
            try:
                tok_row = await cur.fetchone()
            finally:
                await cur.close()
            tokens_in = int(tok_row[0]) if tok_row else 0
            tokens_out = int(tok_row[1]) if tok_row else 0

            price_results = await repo.latest_price_result_per_item(db, run_id=run_id)
            req_rule_ids, question_rule_ids = await _collect_fired_rule_ids(db, run_id)
            bid_summary = await _collect_bid_summary(db, run_id)
            consumed_turns = budget.snapshot()["turns"]

    elapsed = time.monotonic() - start

    # Compare against expected.
    expected_by_item = {f"item_{i['line_no']}": i.get("expected_sku") for i in expected["items"]}
    priced_matched = 0
    priced_substitute = 0
    priced_no_match = 0
    priced_needs_review = 0
    priced_failed = 0
    priced_skipped = 0
    sku_hits = 0
    for iid, row in price_results.items():
        status = row.get("status")
        if status == "matched":
            priced_matched += 1
        elif status == "substitute":
            priced_substitute += 1
        elif status == "no_match":
            priced_no_match += 1
        elif status == "needs_review":
            priced_needs_review += 1
        elif status == "failed":
            priced_failed += 1
        elif status == "skipped":
            priced_skipped += 1
        if row.get("sku") == expected_by_item.get(iid):
            sku_hits += 1

    expected_req = set(expected.get("expected_requirement_rule_ids") or [])
    expected_q = set(expected.get("expected_question_rule_ids") or [])

    expected_conflict_types = [c["type"] for c in expected.get("conflicts") or []]
    got_conflict_types = bid_summary.get("conflicts") or []

    return {
        "status": outcome.status,
        "outcome": outcome.outcome,
        "generated_by": outcome.generated_by.value,
        "partial_reasons": outcome.partial_reasons,
        "turns_used": consumed_turns,
        "turn_cap": MAX_ORCHESTRATOR_TURNS,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "wall_time_s": round(elapsed, 2),
        "priced_items": {
            "matched": priced_matched,
            "substitute": priced_substitute,
            "no_match": priced_no_match,
            "needs_review": priced_needs_review,
            "failed": priced_failed,
            "skipped": priced_skipped,
        },
        "expected_sku_hits": f"{sku_hits}/{len(expected_by_item)}",
        "requirement_rules": {
            "expected": sorted(expected_req),
            "fired": sorted(req_rule_ids),
            "matched": sorted(expected_req & req_rule_ids),
            "missed": sorted(expected_req - req_rule_ids),
            "extra": sorted(req_rule_ids - expected_req),
        },
        "question_rules": {
            "expected": sorted(expected_q),
            "fired": sorted(question_rule_ids),
            "matched": sorted(expected_q & question_rule_ids),
            "missed": sorted(expected_q - question_rule_ids),
            "extra": sorted(question_rule_ids - expected_q),
        },
        "conflicts": {
            "expected_types": sorted(set(expected_conflict_types)),
            "got_types": sorted(set(got_conflict_types)),
        },
        "bid": bid_summary,
        "rejected_claims_count": len(outcome.rejected_claims),
    }


async def main() -> None:
    _ensure_env_only_key()
    _ensure_catalog()
    print("=" * 60)
    print("Real-API smoke — Orchestrator against rfp_messy.pdf")
    print("=" * 60)
    report = await _run_once()
    for k, v in report.items():
        if isinstance(v, dict):
            print(f"\n{k}:")
            for kk, vv in v.items():
                print(f"  {kk}: {vv}")
        else:
            print(f"{k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
