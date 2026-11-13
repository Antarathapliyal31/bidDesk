"""Settings loaded from .env.

ANTHROPIC_API_KEY is optional at import time so tests and dev commands
can run without a real key; code that actually talks to the Anthropic
API must call require_anthropic_key() and surface a clear error. The
key is wrapped in SecretStr so a stray repr/log never leaks it.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_ENV_FILE = _REPO_ROOT / ".env"


LlmMode = Literal["real", "fake"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    llm_mode: LlmMode = Field(default="real", alias="LLM_MODE")

    # Defaults match the IDs resolved from /v1/models on 2026-11-09;
    # override in .env to tune without code changes.
    orchestrator_model: str = Field(default="claude-sonnet-5-5", alias="ORCHESTRATOR_MODEL")
    analyst_model: str = Field(default="claude-sonnet-5-5", alias="ANALYST_MODEL")
    analyst_text_only_model: str = Field(
        default="claude-haiku-5-5", alias="ANALYST_TEXT_ONLY_MODEL"
    )
    pricing_model: str = Field(default="claude-haiku-5-5", alias="PRICING_MODEL")

    # All limits are strictly positive — zero or negative would silently break
    # the ingest/batch logic that depends on them.
    max_upload_mb: int = Field(default=20, gt=0, alias="MAX_UPLOAD_MB")
    max_pages: int = Field(default=60, gt=0, alias="MAX_PAGES")
    max_text_chars: int = Field(default=50_000, gt=0, alias="MAX_TEXT_CHARS")
    parse_timeout_s: int = Field(default=20, gt=0, alias="PARSE_TIMEOUT_S")
    single_call_max_pages: int = Field(default=30, gt=0, alias="SINGLE_CALL_MAX_PAGES")

    # Vision cap (DECISIONS #26). Pages beyond the cap are not
    # transcribed; they land in `unreadable_pages`. Default 10 is a
    # compromise between recall on long scanned RFPs and the
    # analyst's overall deadline.
    max_vision_pages: int = Field(default=10, gt=0, alias="MAX_VISION_PAGES")
    # Bumped from 90s on 2026-11-12 so 3x Sonnet image calls +
    # extraction fit inside one delegate span (DECISIONS #26).
    analyst_timeout_s: int = Field(default=150, gt=0, alias="ANALYST_TIMEOUT_S")
    pricing_batch_timeout_s: int = Field(default=45, gt=0, alias="PRICING_BATCH_TIMEOUT_S")
    run_timeout_s: int = Field(default=240, gt=0, alias="RUN_TIMEOUT_S")
    max_orchestrator_turns: int = Field(default=12, gt=0, alias="MAX_ORCHESTRATOR_TURNS")
    run_token_budget: int = Field(default=400_000, gt=0, alias="RUN_TOKEN_BUDGET")

    # Safer default: off. .env.example sets it to true so local dev gets the
    # demo path by default, but any environment without the var (e.g. prod)
    # starts with faults disabled.
    demo_faults_enabled: bool = Field(default=False, alias="DEMO_FAULTS_ENABLED")

    # DB paths default to backend/data/* and remain there regardless of cwd.
    # A relative path in .env is resolved against backend/ so `data/app.db`
    # keeps meaning "the one inside the backend package".
    app_db_path: Path = Field(default=Path("data/app.db"), alias="APP_DB_PATH")
    catalog_db_path: Path = Field(default=Path("data/catalog.db"), alias="CATALOG_DB_PATH")

    @field_validator("app_db_path", "catalog_db_path")
    @classmethod
    def _anchor_to_backend(cls, v: Path) -> Path:
        return v if v.is_absolute() else _BACKEND_ROOT / v


def get_settings() -> Settings:
    """Load settings. Cached per process via module-level singleton pattern."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def require_anthropic_key() -> str:
    """Return the Anthropic API key or raise with a clear message.

    Call this at the point of the first real API call, not at import.
    """
    s = get_settings()
    if s.anthropic_api_key is None or not s.anthropic_api_key.get_secret_value():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env or set LLM_MODE=fake "
            "for tests and the offline e2e."
        )
    return s.anthropic_api_key.get_secret_value()


_settings: Settings | None = None
