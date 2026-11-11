"""Sample generator tests.

Covers:
  * Deterministic text extraction for text PDFs (rfp_clean, rfp_messy).
  * Deterministic page-image hashes for rfp_scanned (vision PDF).
  * Byte-identical list_paste.txt across runs.
  * Committed samples match a fresh build (`make samples` reproducibility).
  * No wallclock / no random in make_samples.py.
  * Expected JSON shape per file.
  * Every quote on a quote_verifiable sample appears in pdfplumber's
    extracted text for that page (whitespace-normalised).
  * must_not_include strings absent from items / exclusions / labor
    (description OR quote).
  * Every expected_sku resolves to an active row in the seeded catalog.
  * Scenario SKUs (full-cover transfer, partial, zero-stock) each
    appear in at least one sample.
  * Expected rule IDs exist in gap_rules.yaml.

TODO (Phase 3 eval):
  Once the analyst / orchestrator pipeline exists, run it against each
  `samples/*.pdf` and compare its output to `samples/expected/*.json`:
  rules fired (requirement + question ids), SKUs, must_not_include
  checked on the real extracted descriptions / quotes. The committed
  expected JSONs are the golden-set input for that eval.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pdfplumber
import pypdfium2 as pdfium
import pytest

from app.catalog import seed
from app.catalog.semantic_layer import load_bundle
from app.knowledge.matcher import load_rules

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SAMPLES_DIR = _REPO_ROOT / "samples"
_EXPECTED_DIR = _SAMPLES_DIR / "expected"
_MAKE_SAMPLES_PY = _SAMPLES_DIR / "make_samples.py"
_BUNDLE_DIR = _REPO_ROOT / "backend" / "app" / "catalog" / "semantic"
_GAP_RULES_YAML = _REPO_ROOT / "backend" / "app" / "knowledge" / "gap_rules.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def samples_one(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("samples_one")
    subprocess.run([sys.executable, str(_MAKE_SAMPLES_PY), "--out-dir", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def samples_two(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("samples_two")
    subprocess.run([sys.executable, str(_MAKE_SAMPLES_PY), "--out-dir", str(out)], check=True)
    return out


@pytest.fixture(scope="module")
def seeded_catalog(tmp_path_factory: pytest.TempPathFactory) -> Path:
    db = tmp_path_factory.mktemp("catalog") / "catalog.db"
    return seed.main(db)


@pytest.fixture(scope="module")
def all_rule_ids(seeded_catalog: Path) -> set[str]:
    """Rule IDs declared in gap_rules.yaml, loaded through the real
    semantic bundle (so category/field cross-validation runs too)."""
    bundle = load_bundle(_BUNDLE_DIR, seeded_catalog)
    rules = load_rules(_GAP_RULES_YAML, products_entity=bundle.entities["products"])
    return {r.id for r in rules}


def _ro_conn(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _load_expected(name: str) -> dict[str, Any]:
    return json.loads((_EXPECTED_DIR / f"{name}.json").read_text(encoding="utf-8"))


_EXPECTED_NAMES = ("rfp_clean", "rfp_messy", "rfp_scanned", "list_paste")


def _pdfplumber_pages(path: Path) -> list[str]:
    with pdfplumber.open(path) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def _page_image_hashes(path: Path, dpi: int = 150) -> list[str]:
    pdf = pdfium.PdfDocument(path)
    try:
        return [
            hashlib.sha256(page.render(scale=dpi / 72).to_pil().tobytes()).hexdigest()
            for page in pdf
        ]
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# No wallclock / no random (static scan)
# ---------------------------------------------------------------------------


class TestNoWallclock:
    def test_no_wallclock_calls(self) -> None:
        source = _MAKE_SAMPLES_PY.read_text(encoding="utf-8")
        forbidden = [
            "datetime.now",
            "datetime.today",
            "datetime.utcnow",
            "time.time(",
            "time.monotonic(",
        ]
        hits = [t for t in forbidden if t in source]
        assert not hits, f"make_samples.py must not touch wallclock; found: {hits}"

    def test_no_random_import(self) -> None:
        source = _MAKE_SAMPLES_PY.read_text(encoding="utf-8")
        assert not re.search(r"^\s*import\s+random\b", source, re.M)
        assert not re.search(r"^\s*from\s+random\s+import", source, re.M)


# ---------------------------------------------------------------------------
# Determinism across two runs
# ---------------------------------------------------------------------------


class TestDeterminism:
    @pytest.mark.parametrize("name", ["rfp_clean.pdf", "rfp_messy.pdf"])
    def test_text_pdfs_identical_per_page(
        self, samples_one: Path, samples_two: Path, name: str
    ) -> None:
        one = _pdfplumber_pages(samples_one / name)
        two = _pdfplumber_pages(samples_two / name)
        assert one == two, f"{name} differs across runs — reportlab invariant mode broken?"

    def test_scanned_pdf_identical_page_images(self, samples_one: Path, samples_two: Path) -> None:
        one = _page_image_hashes(samples_one / "rfp_scanned.pdf")
        two = _page_image_hashes(samples_two / "rfp_scanned.pdf")
        assert one == two, "rfp_scanned page images differ — pypdfium2 render drift?"

    def test_list_paste_byte_identical(self, samples_one: Path, samples_two: Path) -> None:
        assert (samples_one / "list_paste.txt").read_bytes() == (
            samples_two / "list_paste.txt"
        ).read_bytes()


# ---------------------------------------------------------------------------
# Committed samples match a fresh build
# ---------------------------------------------------------------------------


class TestCommittedSamplesMatchFreshBuild:
    @pytest.mark.parametrize("name", ["rfp_clean.pdf", "rfp_messy.pdf"])
    def test_committed_text_pdf_matches_build(self, samples_one: Path, name: str) -> None:
        committed = _pdfplumber_pages(_SAMPLES_DIR / name)
        fresh = _pdfplumber_pages(samples_one / name)
        assert committed == fresh, (
            f"committed samples/{name} drifted from a fresh build — run `make samples`."
        )

    def test_committed_scanned_matches_build(self, samples_one: Path) -> None:
        committed = _page_image_hashes(_SAMPLES_DIR / "rfp_scanned.pdf")
        fresh = _page_image_hashes(samples_one / "rfp_scanned.pdf")
        assert committed == fresh, (
            "committed samples/rfp_scanned.pdf drifted from a fresh build — run `make samples`."
        )

    def test_committed_list_paste_matches_build(self, samples_one: Path) -> None:
        committed = (_SAMPLES_DIR / "list_paste.txt").read_bytes()
        fresh = (samples_one / "list_paste.txt").read_bytes()
        assert committed == fresh, (
            "committed samples/list_paste.txt drifted from a fresh build — run `make samples`."
        )


# ---------------------------------------------------------------------------
# Expected-JSON schema
# ---------------------------------------------------------------------------


_REQUIRED_TOP_LEVEL = {
    "page_count",
    "page_routes",
    "must_not_include",
    "quote_verifiable",
    "items",
    "exclusions",
    "labor",
    "conflicts",
    "expected_requirement_rule_ids",
    "expected_question_rule_ids",
}


class TestExpectedJsonSchema:
    @pytest.mark.parametrize("name", _EXPECTED_NAMES)
    def test_top_level_shape(self, name: str) -> None:
        data = _load_expected(name)
        missing = _REQUIRED_TOP_LEVEL - set(data)
        assert not missing, f"{name}: missing top-level keys: {missing}"
        assert isinstance(data["items"], list) and data["items"], (
            f"{name}: items list must be non-empty"
        )
        for i, it in enumerate(data["items"]):
            for k in ("description", "source_page", "quote"):
                assert k in it, f"{name}: item[{i}] missing {k!r}"
            assert isinstance(it["source_page"], int)
            assert 1 <= it["source_page"] <= data["page_count"]
            assert "expected_sku" in it

    @pytest.mark.parametrize("name", ["rfp_clean", "rfp_messy"])
    def test_page_count_matches_pdf(self, name: str) -> None:
        data = _load_expected(name)
        with pdfplumber.open(_SAMPLES_DIR / data["pdf"]) as pdf:
            assert len(pdf.pages) == data["page_count"]


# ---------------------------------------------------------------------------
# Quote verification (skipped for vision-routed samples)
# ---------------------------------------------------------------------------


def _normalize_ws(s: str) -> str:
    return " ".join(s.split()).lower()


class TestQuoteVerifiable:
    @pytest.mark.parametrize("name", _EXPECTED_NAMES)
    def test_every_quote_in_page_text(self, name: str) -> None:
        data = _load_expected(name)
        if not data.get("quote_verifiable", True):
            pytest.skip(f"{name}: quote_verifiable=false (vision-routed)")

        if name == "list_paste":
            page_text = {1: (_SAMPLES_DIR / "list_paste.txt").read_text(encoding="utf-8")}
        else:
            with pdfplumber.open(_SAMPLES_DIR / data["pdf"]) as pdf:
                page_text = {i + 1: (page.extract_text() or "") for i, page in enumerate(pdf.pages)}

        # Every expected item's quote must appear in its page's text
        # (whitespace-normalised, lowercased). Covers the mid-phrase
        # line wrap on page 2 of rfp_messy — see SPEC §6.4.1
        # de-hyphenation / whitespace collapse.
        failures: list[str] = []
        for it in data["items"]:
            quote_n = _normalize_ws(it["quote"])
            haystack = _normalize_ws(page_text[it["source_page"]])
            if quote_n not in haystack:
                failures.append(
                    f"item {it['line_no']}: quote not found on page {it['source_page']}\n"
                    f"  quote (norm): {quote_n!r}\n"
                    f"  page   (norm): {haystack[:200]!r}..."
                )
        assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# must_not_include absent from any item/exclusion/labor description or quote
# ---------------------------------------------------------------------------


class TestMustNotInclude:
    @pytest.mark.parametrize("name", _EXPECTED_NAMES)
    def test_banned_strings_absent(self, name: str) -> None:
        data = _load_expected(name)
        banned = data.get("must_not_include", [])
        if not banned:
            pytest.skip(f"{name}: no must_not_include entries")
        buckets = [
            ("items", data["items"]),
            ("exclusions", data["exclusions"]),
            ("labor", data["labor"]),
        ]
        failures: list[str] = []
        for bucket_name, entries in buckets:
            for entry in entries:
                for field in ("description", "quote"):
                    text = (entry.get(field) or "").lower()
                    for ban in banned:
                        if ban.lower() in text:
                            failures.append(
                                f"{bucket_name} entry contains banned {ban!r} in "
                                f"{field}: {entry.get(field)!r}"
                            )
        assert not failures, "\n".join(failures)


# ---------------------------------------------------------------------------
# Every expected_sku resolves to an active catalog row
# ---------------------------------------------------------------------------


class TestExpectedSkusInCatalog:
    @pytest.mark.parametrize("name", _EXPECTED_NAMES)
    def test_every_expected_sku_is_active(self, name: str, seeded_catalog: Path) -> None:
        data = _load_expected(name)
        skus = {it["expected_sku"] for it in data["items"] if it.get("expected_sku")}
        if not skus:
            pytest.skip(f"{name}: no non-null expected_skus")
        conn = _ro_conn(seeded_catalog)
        try:
            cur = conn.execute("SELECT sku FROM products WHERE active = 1")
            active = {row[0] for row in cur.fetchall()}
        finally:
            conn.close()
        missing = skus - active
        assert not missing, (
            f"{name}: these expected SKUs are not active in catalog: {sorted(missing)}"
        )


# ---------------------------------------------------------------------------
# Stock-scenario SKUs appear across the sample set
# ---------------------------------------------------------------------------


class TestScenarioSkusAppear:
    def test_full_cover_partial_and_zero_stock_present(self) -> None:
        wanted = {
            "LUM-2X6-16-SYP-2-PTAG",  # full-cover transfer
            "LUM-2X10-18-SPF-2",  # partial "N of M"
            "ROOF-UNDERLAY-ROLL",  # zero-stock everywhere
        }
        seen: set[str] = set()
        for name in _EXPECTED_NAMES:
            for it in _load_expected(name)["items"]:
                if it.get("expected_sku") in wanted:
                    seen.add(it["expected_sku"])
        missing = wanted - seen
        assert not missing, f"scenario SKUs missing from all samples: {sorted(missing)}"


# ---------------------------------------------------------------------------
# Expected rule IDs exist in gap_rules.yaml
# ---------------------------------------------------------------------------


class TestExpectedRuleIdsExist:
    @pytest.mark.parametrize("name", _EXPECTED_NAMES)
    def test_every_rule_id_is_real(self, name: str, all_rule_ids: set[str]) -> None:
        data = _load_expected(name)
        used = set(data["expected_requirement_rule_ids"]) | set(data["expected_question_rule_ids"])
        for it in data["items"]:
            used |= set(it.get("triggered_requirement_rule_ids", []))
            used |= set(it.get("triggered_question_rule_ids", []))
        unknown = used - all_rule_ids
        assert not unknown, f"{name}: unknown rule ids referenced: {sorted(unknown)}"


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


class TestCli:
    def test_cli_runs_cleanly(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(_MAKE_SAMPLES_PY), "--out-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        for name in ("rfp_clean.pdf", "rfp_messy.pdf", "rfp_scanned.pdf", "list_paste.txt"):
            assert (tmp_path / name).exists(), f"CLI didn't produce {name}"
            assert f"wrote {tmp_path / name}" in result.stdout
