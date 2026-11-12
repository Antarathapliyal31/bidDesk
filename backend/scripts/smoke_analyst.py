"""Real-API smoke test for the Document Analyst.

Not a pytest test — intentionally outside tests/ because it hits
the live Anthropic API. Run manually with the venv:

    cd backend && .venv/bin/python scripts/smoke_analyst.py [--list]

Reports metrics only: item count vs expected, verified count,
injection leak, tokens, wall time. With `--list`, also prints
each extracted item (kind / page / quote prefix) so a diff
against `samples/expected/*.json` is visible.

**Credentials policy.** The key is read ONLY from `ANTHROPIC_API_KEY`
in `backend/.env` via `app.config.get_settings()`. This script
NEVER:
  - runs `ant auth` / reads any keychain or OAuth cache
  - accepts credentials from CLI flags
  - prints or logs the key (not even a prefix)
If `.env` isn't set, the AnthropicLLM constructor raises a clear
error and the script exits.
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

# Isolate from any ambient ANTHROPIC_API_KEY so the only source
# is .env. The env var is deleted before any import that might
# cache it.
os.environ.pop("ANTHROPIC_API_KEY", None)

from app.agents.analyst.agent import register_default_analyst
from app.agents.orchestrator.loop import RunPlan, run_orchestrator
from app.config import get_settings
from app.contracts import InputKind
from app.db import repo
from app.db.init import open_app_db
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import AnthropicLLM

_SAMPLES_ROOT = Path(__file__).resolve().parents[2] / "samples"
_FIXED_NOW = "2026-11-11T12:00:00+00:00"


def _expected(name: str) -> dict[str, Any]:
    return json.loads((_SAMPLES_ROOT / "expected" / name).read_text(encoding="utf-8"))


async def _run_one(sample_name: str, expected_json: str, list_items: bool) -> dict[str, Any]:
    expected = _expected(expected_json)
    pdf = _SAMPLES_ROOT / sample_name

    start = time.monotonic()
    with tempfile.TemporaryDirectory() as td:
        app_db = Path(td) / "app.db"
        async with open_app_db(app_db, now_iso=_FIXED_NOW) as (db, _):
            bus = EventBus(db)
            budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
            budget.start()
            await repo.insert_run(
                db,
                run_id="run_smoke",
                branch_id="BR-NJ-01",
                input_kind=InputKind.pdf,
                input_name=sample_name,
                created_at=_FIXED_NOW,
            )
            ctx = AgentContext(run_id="run_smoke", bus=bus, budget=budget)

            unregister_all()
            register_default_analyst(AnthropicLLM(db=db))

            try:
                outcome = await run_orchestrator(
                    RunPlan(
                        kind="pdf",
                        pdf_path=str(pdf),
                        pasted_text=None,
                        branch_id="BR-NJ-01",
                        document_tag=f"document_smoke_{sample_name}",
                    ),
                    ctx=ctx,
                )
            except Exception as exc:
                elapsed = time.monotonic() - start
                return {
                    "sample": sample_name,
                    "error": f"{exc.__class__.__name__}: {exc}",
                    "wall_time_s": round(elapsed, 2),
                }

            # Read token counters persisted to runs row.
            cursor = await db.read_conn.execute(
                "SELECT tokens_in, tokens_out FROM runs WHERE id = 'run_smoke'"
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()
            tokens_in = int(row[0]) if row else 0
            tokens_out = int(row[1]) if row else 0

    elapsed = time.monotonic() - start

    bad_strings: list[str] = expected.get("must_not_include", [])
    leaks: list[tuple[str, str]] = []
    for item in outcome.items:
        for bad in bad_strings:
            if bad in item.description or bad in (item.source.quote or ""):
                leaks.append((item.id, bad))

    expected_total = len(expected["items"]) + len(expected.get("exclusions", []))

    result = {
        "sample": sample_name,
        "expected_items": expected_total,
        "got_items": len(outcome.items),
        "verified": sum(1 for i in outcome.items if i.source.verified),
        "unverified": sum(1 for i in outcome.items if not i.source.verified),
        "injection_leaks": leaks,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "wall_time_s": round(elapsed, 2),
    }

    if list_items:
        result["items"] = [
            {
                "id": i.id,
                "kind": i.kind.value,
                "page": i.source.page,
                "description": i.description,
                "quantity_text": i.quantity_text,
                "unit_raw": i.unit_raw,
                "quote": (i.source.quote or "")[:100],
                "verified": i.source.verified,
                "notes": list(i.notes),
            }
            for i in outcome.items
        ]

    return result


def _ensure_env_only_key() -> None:
    """Verify that the key is available via `.env` + `get_settings()`
    and that no CLI/keychain/OAuth path is in use. If no key, bail
    with a clear message (no printing of the key itself)."""

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


async def main() -> None:
    parser = argparse.ArgumentParser(description="Real-API smoke for Document Analyst")
    parser.add_argument(
        "--list",
        action="store_true",
        help="print each extracted item (kind/page/quote prefix)",
    )
    args = parser.parse_args()

    _ensure_env_only_key()

    print("=" * 60)
    print("Real-API smoke — Document Analyst against sample PDFs")
    print("=" * 60)
    for sample, expected in (
        ("rfp_clean.pdf", "rfp_clean.json"),
        ("rfp_messy.pdf", "rfp_messy.json"),
    ):
        print(f"\n--- {sample} ---")
        result = await _run_one(sample, expected, list_items=args.list)
        items = result.pop("items", None)
        for k, v in result.items():
            print(f"  {k}: {v}")
        if items is not None:
            print("  items:")
            for it in items:
                print(
                    f"    {it['id']:7s} {it['kind']:9s} p{it['page']:<2d} "
                    f"{it['description'][:50]!r:52s} "
                    f"qty={it['quantity_text']!s:>6s} "
                    f"unit={it['unit_raw']!s:>7s} "
                    f"verified={it['verified']}"
                )


if __name__ == "__main__":
    asyncio.run(main())
