"""Real-API smoke test for the vision path (Phase 5c).

Not a pytest test — intentionally outside tests/ because it hits
the live Anthropic API with image blocks. Run manually with the
venv:

    cd backend && .venv/bin/python scripts/smoke_vision.py [--list]

What it does, per sample:
  * rfp_scanned.pdf (all 3 pages vision) — transcribes each page,
    extracts items, reports description-level recall against
    `samples/expected/rfp_scanned.json` (SKU-independent — we only
    compare whether the LLM found each description, since the
    quote text can't be byte-matched from an image).
  * rfp_messy.pdf — focuses on page 7 (text+vision). Confirms that
    the 3 expected page-7 items come through as read_via='vision',
    verified=False, with the `read_via_vision` note (DECISIONS
    #26).

**Credentials policy.** The key is read ONLY from
`ANTHROPIC_API_KEY` in `backend/.env` via `app.config.get_settings()`.
This script NEVER:
  - runs `ant auth` / reads any keychain or OAuth cache
  - accepts credentials from CLI flags
  - prints or logs the key (not even a prefix)
If `.env` isn't set, we exit with a clear non-zero status.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

# Isolate from any ambient ANTHROPIC_API_KEY so the only source
# is .env.
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.agents.analyst.agent import (
    AnalystRequest,
    register_default_analyst,
)
from app.config import get_settings
from app.contracts import AgentName, InputKind, ItemView
from app.db import repo
from app.db.init import open_app_db
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, delegate, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import AnthropicLLM

_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"
_FIXED_NOW = "2026-11-12T12:00:00+00:00"

# A description "matches" if its normalized form is a >= 0.75
# SequenceMatcher ratio against an expected description. Vision
# reads rarely produce byte-identical strings — "2x4 x 8' SPF #2"
# often becomes "2x4x8' SPF #2" — but the phrase is close enough
# for a human to confirm.
_MATCH_THRESHOLD = 0.75


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _best_match(target: str, pool: list[str]) -> tuple[str | None, float]:
    best = (None, 0.0)
    for cand in pool:
        ratio = SequenceMatcher(None, _norm(target), _norm(cand)).ratio()
        if ratio > best[1]:
            best = (cand, ratio)
    return best


def _expected(name: str) -> dict[str, Any]:
    return json.loads((_SAMPLES_ROOT / "expected" / name).read_text(encoding="utf-8"))


async def _run_analyst(pdf_name: str, document_tag: str) -> tuple[list[ItemView], dict[str, Any]]:
    pdf = _SAMPLES_ROOT / pdf_name
    with tempfile.TemporaryDirectory() as td:
        app_db = Path(td) / "app.db"
        async with open_app_db(app_db, now_iso=_FIXED_NOW) as (db, _):
            bus = EventBus(db)
            # Vision calls are heavier — bump the per-run deadline
            # to match the new analyst_timeout_s default.
            budget = Budget(turns=40, tokens=600_000, deadline_s=360.0)
            budget.start()
            await repo.insert_run(
                db,
                run_id="run_smoke_vision",
                branch_id="BR-NJ-01",
                input_kind=InputKind.pdf,
                input_name=pdf_name,
                created_at=_FIXED_NOW,
            )
            ctx = AgentContext(run_id="run_smoke_vision", bus=bus, budget=budget)
            unregister_all()
            register_default_analyst(AnthropicLLM(db=db))
            result = await delegate(
                AgentName.document_analyst,
                AnalystRequest(
                    kind="pdf",
                    pdf_path=str(pdf),
                    pasted_text=None,
                    document_tag=document_tag,
                ),
                ctx=ctx,
            )
            if result.status != "ok" or result.output is None:
                raise RuntimeError(
                    f"analyst failed on {pdf_name}: status={result.status}, "
                    f"errors={[e.message for e in result.errors]}"
                )
            cursor = await db.read_conn.execute(
                "SELECT tokens_in, tokens_out FROM runs WHERE id = 'run_smoke_vision'"
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            out = result.output.model_dump(mode="python")
            items = [ItemView.model_validate(i) for i in out["items"]]
            meta: dict[str, Any] = {
                "page_routes": out["page_routes"],
                "unreadable_pages": out["unreadable_pages"],
                "tokens_in": int(row[0]) if row else 0,
                "tokens_out": int(row[1]) if row else 0,
            }
            return items, meta


async def _smoke_rfp_scanned(list_items: bool) -> dict[str, Any]:
    start = time.monotonic()
    items, meta = await _run_analyst("rfp_scanned.pdf", "document_smoke_vision_scanned")
    elapsed = time.monotonic() - start

    expected = _expected("rfp_scanned.json")
    expected_descs: list[str] = [it["description"] for it in expected["items"]]
    got_descs: list[str] = [i.description for i in items]

    hits: list[tuple[str, str, float]] = []
    misses: list[str] = []
    for d in expected_descs:
        match, score = _best_match(d, got_descs)
        if match is not None and score >= _MATCH_THRESHOLD:
            hits.append((d, match, round(score, 2)))
        else:
            misses.append(d)

    vision_items = [i for i in items if i.source.read_via == "vision"]
    invariant_failures = [
        {
            "id": i.id,
            "read_via": i.source.read_via,
            "verified": i.source.verified,
            "has_note": "read_via_vision" in i.notes,
        }
        for i in items
        if i.source.read_via != "vision"
        or i.source.verified is not False
        or "read_via_vision" not in i.notes
    ]

    result = {
        "sample": "rfp_scanned.pdf",
        "page_routes": {str(r["page_no"]): r["route"] for r in meta["page_routes"]},
        "unreadable_pages": meta["unreadable_pages"],
        "expected_items": len(expected_descs),
        "got_items": len(items),
        "vision_items": len(vision_items),
        "recall_hits": len(hits),
        "recall_total": len(expected_descs),
        "recall_pct": (round(100 * len(hits) / len(expected_descs), 1) if expected_descs else 0.0),
        "missed_descriptions": misses,
        "invariant_failures": invariant_failures,
        "tokens_in": meta["tokens_in"],
        "tokens_out": meta["tokens_out"],
        "wall_time_s": round(elapsed, 2),
    }
    if list_items:
        result["items"] = [
            {
                "id": i.id,
                "page": i.source.page,
                "kind": i.kind.value,
                "description": i.description,
                "quantity_text": i.quantity_text,
                "unit_raw": i.unit_raw,
                "read_via": i.source.read_via,
                "verified": i.source.verified,
                "notes": list(i.notes),
            }
            for i in items
        ]
    return result


async def _smoke_rfp_messy_page7(list_items: bool) -> dict[str, Any]:
    start = time.monotonic()
    items, meta = await _run_analyst("rfp_messy.pdf", "document_smoke_vision_messy")
    elapsed = time.monotonic() - start

    page_7_items = [i for i in items if i.source.page == 7]
    expected_page_7 = [it for it in _expected("rfp_messy.json")["items"] if it["source_page"] == 7]
    expected_descs = [it["description"] for it in expected_page_7]
    got_descs = [i.description for i in page_7_items]

    hits = []
    misses = []
    for d in expected_descs:
        match, score = _best_match(d, got_descs)
        if match is not None and score >= _MATCH_THRESHOLD:
            hits.append((d, match, round(score, 2)))
        else:
            misses.append(d)

    invariant_failures = [
        {
            "id": i.id,
            "read_via": i.source.read_via,
            "verified": i.source.verified,
            "has_note": "read_via_vision" in i.notes,
        }
        for i in page_7_items
        if i.source.read_via != "vision"
        or i.source.verified is not False
        or "read_via_vision" not in i.notes
    ]

    result = {
        "sample": "rfp_messy.pdf (page 7 focus)",
        "page_routes": {str(r["page_no"]): r["route"] for r in meta["page_routes"]},
        "page_7_items_got": len(page_7_items),
        "page_7_items_expected": len(expected_page_7),
        "recall_hits": len(hits),
        "recall_total": len(expected_descs),
        "missed_descriptions": misses,
        "invariant_failures": invariant_failures,
        "tokens_in": meta["tokens_in"],
        "tokens_out": meta["tokens_out"],
        "wall_time_s": round(elapsed, 2),
    }
    if list_items:
        result["page_7_items"] = [
            {
                "id": i.id,
                "description": i.description,
                "quantity_text": i.quantity_text,
                "unit_raw": i.unit_raw,
                "read_via": i.source.read_via,
                "verified": i.source.verified,
                "notes": list(i.notes),
            }
            for i in page_7_items
        ]
    return result


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


def _print_block(title: str, result: dict[str, Any]) -> None:
    items = result.pop("items", None)
    page_7_items = result.pop("page_7_items", None)
    print(f"\n--- {title} ---")
    for k, v in result.items():
        print(f"  {k}: {v}")
    if items is not None:
        print("  items:")
        for it in items:
            print(
                f"    {it['id']:7s} p{it['page']:<2d} "
                f"{it['description'][:50]!r:52s} "
                f"qty={it['quantity_text']!s:>6s} "
                f"unit={it['unit_raw']!s:>7s} "
                f"read_via={it['read_via']}"
            )
    if page_7_items is not None:
        print("  page_7_items:")
        for it in page_7_items:
            print(
                f"    {it['id']:7s} "
                f"{it['description'][:50]!r:52s} "
                f"qty={it['quantity_text']!s:>6s} "
                f"unit={it['unit_raw']!s:>7s} "
                f"read_via={it['read_via']}"
            )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Real-API smoke for the vision path")
    parser.add_argument(
        "--list",
        action="store_true",
        help="print each extracted item",
    )
    args = parser.parse_args()
    _ensure_env_only_key()

    print("=" * 64)
    print("Real-API smoke — vision path (Phase 5c, DECISIONS #26)")
    print("=" * 64)

    scanned = await _smoke_rfp_scanned(args.list)
    _print_block("rfp_scanned.pdf (all pages vision)", scanned)

    messy = await _smoke_rfp_messy_page7(args.list)
    _print_block("rfp_messy.pdf (page 7 text+vision)", messy)

    # Non-zero exit if any invariant broke — this script is meant
    # to be run manually and the exit code is the quick signal.
    inv_failures = (scanned.get("invariant_failures") or []) + (
        messy.get("invariant_failures") or []
    )
    if inv_failures:
        print("\nINVARIANT FAILURES:", inv_failures)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
