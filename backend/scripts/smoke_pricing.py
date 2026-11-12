"""Real-API smoke test for the Pricing Agent.

Not a pytest test — hits the live Anthropic API. Run manually:

    cd backend && .venv/bin/python scripts/smoke_pricing.py [--list]

Feeds `samples/list_paste.txt` to the pricing agent via
`delegate(pricing_agent, ...)` and reports: matched / substitute /
no_match / failed per line, expected-SKU accuracy, tokens, wall
time. With `--list`, prints each PricingLineResult detail too.

**Credentials policy** — identical to `smoke_analyst.py`:
  - key read ONLY from `ANTHROPIC_API_KEY` in `backend/.env`
  - no keychain / OAuth cache / CLI flag
  - never printed or logged

Pricing scope:
  - One batch of up to 8 items (SPEC §8.2 limit). The 10-line
    `list_paste` is split 8/2.
  - Each item carries no orchestrator-attached requirements —
    those come from the orchestrator (Phase 5); the pricing agent
    still chooses on its own.
  - A fresh in-memory `app.db` is created per invocation; the
    seeded `backend/data/catalog.db` is required (`make seed` if
    missing).
"""

from __future__ import annotations

import argparse
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

from app.agents.pricing.agent import register_default_pricing
from app.catalog.semantic_layer import load_bundle
from app.catalog.units import normalize_unit
from app.config import get_settings
from app.contracts import InputKind, ItemView
from app.contracts.common import AgentName, ItemKind, SourceRef
from app.contracts.pricing_agent import PricingItemRequest, PricingRequest
from app.db import repo
from app.db.init import open_app_db
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, delegate, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import AnthropicLLM

_BACKEND = Path(__file__).resolve().parents[1]
_SAMPLES_ROOT = _BACKEND.parent / "samples"
_BUNDLE_DIR = _BACKEND / "app" / "catalog" / "semantic"
_CATALOG_DB = _BACKEND / "data" / "catalog.db"
_FIXED_NOW = "2026-11-11T12:00:00+00:00"


_SAMPLES: dict[str, str] = {
    "list_paste": "list_paste.json",
    "rfp_messy": "rfp_messy.json",
}


def _expected(sample: str = "list_paste") -> dict[str, Any]:
    name = _SAMPLES.get(sample)
    if name is None:
        raise ValueError(f"unknown sample {sample!r}; known: {sorted(_SAMPLES)}")
    return json.loads((_SAMPLES_ROOT / "expected" / name).read_text())


def _priceable_items(sample: str) -> list[dict[str, Any]]:
    """Items we can price today. For rfp_messy that means
    `expected_sku != null` OR items with `expected_sku == null`
    that are still priceable (cedar shakes → no_match, deck
    ledger 2x10 → no_match)."""

    data = _expected(sample)
    return list(data["items"])


def _mk_item(entry: dict[str, Any]) -> ItemView:
    """Build an ItemView matching what the analyst would produce.

    Specifically, we mirror the analyst's unit-normalization pass
    (`app.agents.analyst.verify._normalize_unit`-style logic) so
    the pint fallback fires in the smoke the same way it would on
    a real run. Without this, items with `unit_raw="yards"` would
    never carry the `unit_pint_convertible` note and the pricing
    agent would see them as `unit_conversion_unavailable`.
    """

    notes: list[str] = []
    if entry["unit_raw"]:
        normalized = normalize_unit(entry["unit_raw"])
        if normalized.unit is None:
            if normalized.pint_convertible:
                notes.append("unit_pint_convertible")
            else:
                notes.append("unknown_unit")
    return ItemView(
        id=f"it_{entry['line_no']}",
        kind=ItemKind.material,
        description=entry["description"],
        quantity_text=entry["quantity_text"],
        unit_raw=entry["unit_raw"],
        source=SourceRef(
            page=entry["source_page"],
            quote=entry["quote"],
            read_via="text",
            verified=True,
        ),
        notes=notes,
    )


async def _run_batch(
    sample: str,
    batch_no: int,
    entries: list[dict[str, Any]],
    *,
    bundle,
    list_items: bool,
) -> dict[str, Any]:
    run_id = f"run_smoke_px_{sample}_{batch_no}"
    start = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        app_db = Path(td) / "app.db"
        async with open_app_db(app_db, now_iso=_FIXED_NOW) as (db, _):
            bus = EventBus(db)
            budget = Budget(turns=40, tokens=400_000, deadline_s=120.0)
            budget.start()
            await repo.insert_run(
                db,
                run_id=run_id,
                branch_id="BR-NJ-01",
                input_kind=InputKind.text,
                input_name=f"{sample}.txt",
                created_at=_FIXED_NOW,
            )
            ctx = AgentContext(run_id=run_id, bus=bus, budget=budget)

            unregister_all()
            register_default_pricing(
                AnthropicLLM(db=db),
                bundle=bundle,
                catalog_db_path=_CATALOG_DB,
                max_steps=10,
            )

            request = PricingRequest(
                branch_id="BR-NJ-01",
                items=[PricingItemRequest(item=_mk_item(e), requirements=[]) for e in entries],
            )

            try:
                agent_result = await delegate(AgentName.pricing_agent, request, ctx=ctx)
            except Exception as exc:
                return {
                    "sample": sample,
                    "batch": batch_no,
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "wall_time_s": round(time.monotonic() - start, 2),
                }

            cursor = await db.read_conn.execute(
                "SELECT tokens_in, tokens_out FROM runs WHERE id = ?",
                [run_id],
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            tokens_in = int(row[0]) if row else 0
            tokens_out = int(row[1]) if row else 0

    elapsed = time.monotonic() - start

    status_str = agent_result.status
    out = agent_result.output
    pr_status = getattr(out, "status", None) if out else None
    results = list(getattr(out, "results", []) if out else [])
    item_errors = list(getattr(out, "item_errors", []) if out else [])

    expected_by_id = {f"it_{e['line_no']}": e["expected_sku"] for e in entries}
    matched = sum(1 for r in results if r.status == "matched")
    substitute = sum(1 for r in results if r.status == "substitute")
    no_match = sum(1 for r in results if r.status == "no_match")
    failed = sum(1 for r in results if r.status == "failed")
    expected_sku_hits = sum(1 for r in results if r.priced.sku == expected_by_id.get(r.item_id))

    report: dict[str, Any] = {
        "sample": sample,
        "batch": batch_no,
        "items_requested": len(entries),
        "delegate_status": status_str,
        "pricing_status": pr_status,
        "matched": matched,
        "substitute": substitute,
        "no_match": no_match,
        "failed": failed,
        "expected_sku_hits": f"{expected_sku_hits}/{len(entries)}",
        "item_errors": [
            {"ref": e.ref, "kind": e.kind, "message": e.message[:80]} for e in item_errors
        ],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "wall_time_s": round(elapsed, 2),
    }

    if list_items:
        report["lines"] = [
            {
                "item_id": r.item_id,
                "status": r.status,
                "sku": r.priced.sku,
                "expected": expected_by_id.get(r.item_id),
                "sale_qty": (str(r.priced.sale_qty) if r.priced.sale_qty is not None else None),
                "sale_unit": r.priced.sale_unit,
                "unit_price_cents": r.priced.unit_price_cents,
                "confidence": r.priced.confidence,
                "verify_notes": list(r.verify_notes),
                "conversion_path": r.priced.conversion_path,
            }
            for r in results
        ]
    return report


def _ensure_env_only_key() -> None:
    """Identical guard to `smoke_analyst.py`."""

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
            f"Catalog not built at {_CATALOG_DB}.\n"
            "Run `make seed` (or `python -m app.catalog.seed`) first.",
            file=sys.stderr,
        )
        sys.exit(2)


def _chunks(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=("Real-API smoke for the Pricing Agent against list_paste and rfp_messy.")
    )
    parser.add_argument(
        "--sample",
        choices=["list_paste", "rfp_messy", "all"],
        default="all",
        help="which sample(s) to run (default: all)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print each PricingLineResult (status/sku/qty/unit/price/notes)",
    )
    args = parser.parse_args()

    _ensure_env_only_key()
    _ensure_catalog()
    bundle = load_bundle(_BUNDLE_DIR, _CATALOG_DB)

    samples = ["list_paste", "rfp_messy"] if args.sample == "all" else [args.sample]
    totals: dict[str, dict[str, Any]] = {}
    for sample in samples:
        all_items = _priceable_items(sample)
        batches = _chunks(all_items, 8)

        print("=" * 60)
        print(f"Real-API smoke — Pricing Agent against {sample}")
        print(f"  items: {len(all_items)} across {len(batches)} batch(es) of ≤8")
        print("=" * 60)

        sample_totals = {
            "items": len(all_items),
            "matched": 0,
            "substitute": 0,
            "no_match": 0,
            "failed": 0,
            "expected_sku_hits": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "wall_time_s": 0.0,
            "item_errors": [],
        }
        for i, entries in enumerate(batches, start=1):
            if not entries:
                continue
            print(f"\n--- {sample} batch {i} ({len(entries)} items) ---")
            report = await _run_batch(sample, i, entries, bundle=bundle, list_items=args.list)
            lines = report.pop("lines", None)
            for k, v in report.items():
                print(f"  {k}: {v}")
            if lines is not None:
                print("  lines:")
                for ln in lines:
                    marker = "✓" if ln["sku"] == ln["expected"] else "·"
                    print(
                        f"    {marker} {ln['item_id']:14s} {ln['status']:10s} "
                        f"sku={ln['sku']!s:30s} "
                        f"qty={ln['sale_qty']!s:>8s} {ln['sale_unit']!s:>4s} "
                        f"price={ln['unit_price_cents']}¢ "
                        f"conf={ln['confidence']}"
                    )
                    if ln["verify_notes"]:
                        print(f"      verify_notes: {ln['verify_notes']}")
                    if (
                        ln["conversion_path"]
                        and ln["conversion_path"]
                        != f"{ln['sale_qty']} {ln['sale_unit']} → {ln['sale_qty']} {ln['sale_unit']} (same unit)"
                    ):
                        print(f"      conversion: {ln['conversion_path']}")

            if "error" in report:
                continue
            sample_totals["matched"] += report["matched"]
            sample_totals["substitute"] += report["substitute"]
            sample_totals["no_match"] += report["no_match"]
            sample_totals["failed"] += report["failed"]
            hits_num = int(report["expected_sku_hits"].split("/")[0])
            sample_totals["expected_sku_hits"] += hits_num
            sample_totals["tokens_in"] += report["tokens_in"]
            sample_totals["tokens_out"] += report["tokens_out"]
            sample_totals["wall_time_s"] += report["wall_time_s"]
            sample_totals["item_errors"].extend(report.get("item_errors") or [])

        totals[sample] = sample_totals
        print(f"\n=== {sample} summary ===")
        for k, v in sample_totals.items():
            print(f"  {k}: {v}")

    if len(samples) > 1:
        print("\n" + "=" * 60)
        print("GRAND TOTAL")
        print("=" * 60)
        for sample, t in totals.items():
            print(
                f"  {sample:10s} "
                f"matched={t['matched']:>2d} substitute={t['substitute']:>2d} "
                f"no_match={t['no_match']:>2d} failed={t['failed']:>2d} "
                f"expected_hits={t['expected_sku_hits']}/{t['items']} "
                f"tokens={t['tokens_in']}in/{t['tokens_out']}out "
                f"wall={t['wall_time_s']:.1f}s"
            )


if __name__ == "__main__":
    asyncio.run(main())
