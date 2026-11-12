"""Shared pricing-agent fixtures.

Keeps the seeded catalog.db module-scoped — rebuilding it per
test adds ~100ms a shot.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.catalog.seed import main as seed_main
from app.catalog.semantic_layer import load_bundle

_BUNDLE_DIR = Path(__file__).resolve().parents[3] / "app" / "catalog" / "semantic"


@pytest.fixture(scope="module")
def catalog_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Fully seeded catalog.db for pricing tests."""

    tmp = tmp_path_factory.mktemp("pricing_catalog")
    return seed_main(tmp / "catalog.db")


@pytest.fixture(scope="module")
def bundle(catalog_db: Path):
    return load_bundle(_BUNDLE_DIR, catalog_db)
