"""Shared orchestrator-test fixtures: seeded catalog bundle +
minimal run with items + an `OrchestratorContext`."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from app.agents.orchestrator.tools import OrchestratorContext
from app.catalog.seed import main as seed_main
from app.catalog.semantic_layer import load_bundle
from app.contracts import InputKind, ItemKind
from app.db import repo
from app.db.init import Database
from app.knowledge.matcher import load_rules
from app.runtime.budgets import Budget
from app.runtime.events import EventBus

_BUNDLE_DIR = Path(__file__).resolve().parents[3] / "app" / "catalog" / "semantic"


@pytest.fixture(scope="module")
def catalog_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    tmp = tmp_path_factory.mktemp("orch_catalog")
    return seed_main(tmp / "catalog.db")


@pytest.fixture(scope="module")
def bundle(catalog_db: Path):
    return load_bundle(_BUNDLE_DIR, catalog_db)


@pytest_asyncio.fixture
async def seeded_run_and_items(db: Database) -> str:
    run_id = "run_orch"
    await repo.insert_run(
        db,
        run_id=run_id,
        branch_id="BR-NJ-01",
        input_kind=InputKind.pdf,
        input_name="rfp.pdf",
        created_at="2026-11-12T00:00:00+00:00",
    )
    # Two items: a sill plate (triggers pt_sill_plates_on_concrete)
    # and a 4x4 ground-contact post (triggers pt_posts_in_ground).
    await repo.insert_item(
        db,
        run_id=run_id,
        item_id="it_sill",
        kind=ItemKind.material,
        description="2x6 PT for sill plates on the concrete slab",
        created_at="2026-11-12T00:00:00+00:00",
        quantity_text="12",
        unit_raw="LF",
        source_page=2,
        source_quote="2x6 PT for sill plates on the concrete slab",
        read_via="text",
        verified=True,
        verification_notes=[],
        specs={"nominal_size": "2x6"},
    )
    await repo.insert_item(
        db,
        run_id=run_id,
        item_id="it_post",
        kind=ItemKind.material,
        description="4x4 x 8' SPF #2 (ground contact)",
        created_at="2026-11-12T00:00:00+00:00",
        quantity_text="20",
        unit_raw="pcs",
        source_page=2,
        source_quote="4x4 x 8' SPF #2 (ground contact)",
        read_via="text",
        verified=True,
        verification_notes=[],
        specs={"nominal_size": "4x4", "length_ft": 8},
    )
    return run_id


@pytest_asyncio.fixture
async def orch_ctx(
    db: Database,
    bus: EventBus,
    bundle,
    catalog_db: Path,
    seeded_run_and_items: str,
) -> OrchestratorContext:
    items = await repo.list_items(db, run_id=seeded_run_and_items)
    items_by_id = {r["id"]: r for r in items}
    budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
    budget.start()
    return OrchestratorContext(
        run_id=seeded_run_and_items,
        branch_id="BR-NJ-01",
        document_tag="document_test",
        db=db,
        bus=bus,
        budget=budget,
        bundle=bundle,
        catalog_db_path=catalog_db,
        gap_rules=load_rules(products_entity=bundle.entities["products"]),
        items_by_id=items_by_id,
    )
