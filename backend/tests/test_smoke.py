"""Phase 0 smoke: the app factory boots and health returns ok.

Also exercises the config.py contracts: Settings loads without
ANTHROPIC_API_KEY, the key is wrapped in SecretStr so it doesn't leak via
repr, LLM_MODE only accepts real/fake, zero limits are rejected, DB paths
anchor to backend/, and model names have defaults.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import config
from app.main import create_app


def test_health_endpoint_returns_ok() -> None:
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_require_anthropic_key_raises_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        config.require_anthropic_key()


def test_api_key_is_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    """SecretStr means the key stays out of logs / stack traces / error dumps."""
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-not-real-test-value-xyz")
    s = config.Settings(_env_file=None)
    text = repr(s) + " " + str(s)
    assert "sk-ant-not-real-test-value-xyz" not in text
    # But we can still get it explicitly.
    assert s.anthropic_api_key is not None
    assert s.anthropic_api_key.get_secret_value() == "sk-ant-not-real-test-value-xyz"


def test_llm_mode_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv("LLM_MODE", "pretend")
    with pytest.raises(ValidationError, match="LLM_MODE"):
        config.Settings(_env_file=None)


@pytest.mark.parametrize(
    "var", ["MAX_UPLOAD_MB", "MAX_PAGES", "RUN_TIMEOUT_S", "MAX_ORCHESTRATOR_TURNS"]
)
def test_positive_limits_rejected_when_zero(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv(var, "0")
    with pytest.raises(ValidationError, match=var):
        config.Settings(_env_file=None)


def test_db_paths_anchored_to_backend_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    """Relative DB paths in .env must land under backend/data/ regardless of cwd."""
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.setenv("APP_DB_PATH", "data/app.db")
    monkeypatch.setenv("CATALOG_DB_PATH", "data/catalog.db")
    s = config.Settings(_env_file=None)
    assert s.app_db_path.is_absolute()
    assert s.app_db_path.parts[-3:] == ("backend", "data", "app.db")
    assert s.catalog_db_path.parts[-3:] == ("backend", "data", "catalog.db")


def test_db_paths_respect_absolute_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Absolute paths in .env are passed through unchanged."""
    monkeypatch.setattr(config, "_settings", None)
    absolute = tmp_path / "custom.db"
    monkeypatch.setenv("APP_DB_PATH", str(absolute))
    s = config.Settings(_env_file=None)
    assert s.app_db_path == absolute


def test_demo_faults_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Demo faults must default off so prod (no .env) can't inject failures."""
    monkeypatch.setattr(config, "_settings", None)
    monkeypatch.delenv("DEMO_FAULTS_ENABLED", raising=False)
    s = config.Settings(_env_file=None)
    assert s.demo_faults_enabled is False


def test_model_defaults_match_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaults match the IDs resolved from /v1/models (SPEC §2, DECISIONS #4)."""
    monkeypatch.setattr(config, "_settings", None)
    for var in ("ORCHESTRATOR_MODEL", "ANALYST_MODEL", "ANALYST_TEXT_ONLY_MODEL", "PRICING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    s = config.Settings(_env_file=None)
    assert s.orchestrator_model == "claude-sonnet-5-5"
    assert s.analyst_model == "claude-sonnet-5-5"
    assert s.analyst_text_only_model == "claude-haiku-5-5"
    assert s.pricing_model == "claude-haiku-5-5"
