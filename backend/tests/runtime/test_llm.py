"""`FakeLLM` tests + `AnthropicLLM` key-check smoke.

The real `AnthropicLLM.call()` lands in Phase 3 — here we just
verify the constructor fails loudly without a key, so Phase 2
tests can't accidentally wire the real client.
"""

from __future__ import annotations

import os

import pytest

from app.runtime.llm import AnthropicLLM, FakeLLM, ScriptedResponse


class TestFakeLLM:
    async def test_plays_responses_in_order(self) -> None:
        fake = FakeLLM(
            responses=[
                ScriptedResponse(content={"n": 1}, tokens_in=10, tokens_out=5),
                ScriptedResponse(content={"n": 2}, tokens_in=20, tokens_out=8),
            ]
        )
        r1 = await fake.call("first")
        r2 = await fake.call("second")
        assert r1.content == {"n": 1}
        assert r2.content == {"n": 2}
        assert fake.calls_made == 2

    async def test_exhausted_raises_assertion(self) -> None:
        fake = FakeLLM(responses=[ScriptedResponse(content={"x": 1})])
        await fake.call("first")
        with pytest.raises(AssertionError, match="exhausted"):
            await fake.call("second")

    async def test_tokens_passthrough(self) -> None:
        fake = FakeLLM(responses=[ScriptedResponse(content={"x": 1}, tokens_in=100, tokens_out=50)])
        r = await fake.call("x")
        assert r.tokens_in == 100
        assert r.tokens_out == 50


class TestAnthropicLLMKeyCheck:
    """Settings is a process-global singleton (`app.config._settings`).
    These tests poke the singleton directly rather than mucking with
    env vars — that's the knob `config.py` gives us."""

    def test_constructor_requires_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.config as cfg

        # Clear the singleton so Settings() re-reads; set the env
        # var to empty (same pattern test_smoke.py uses).
        monkeypatch.setattr(cfg, "_settings", None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
            AnthropicLLM()

    def test_constructor_accepts_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.config as cfg

        monkeypatch.setattr(cfg, "_settings", None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-xxxxxx")
        llm = AnthropicLLM()
        assert llm is not None


# ---------------------------------------------------------------------------
# is_retryable predicate — tight transport gate (SPEC §10.4)
# ---------------------------------------------------------------------------


class TestIsRetryable:
    def test_timeout_errors_retry(self) -> None:

        from app.runtime.llm import is_retryable

        assert is_retryable(TimeoutError()) is True
        assert is_retryable(TimeoutError()) is True

    def test_anthropic_transient_errors_retry(self) -> None:
        import anthropic

        from app.runtime.llm import is_retryable

        # Construct synthetic errors; the real SDK ones carry
        # request/response pairs we don't need for the predicate.
        req = object()
        resp = object()
        # RateLimitError (429)
        rate = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
        rate.body = None
        rate.status_code = 429
        assert is_retryable(rate) is True

        # APIConnectionError
        conn = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
        assert is_retryable(conn) is True

        # APITimeoutError
        at = anthropic.APITimeoutError.__new__(anthropic.APITimeoutError)
        assert is_retryable(at) is True

        _ = (req, resp)  # unused; keeps future static analysis quiet

    def test_5xx_api_status_retries(self) -> None:
        import anthropic

        from app.runtime.llm import is_retryable

        err = anthropic.InternalServerError.__new__(anthropic.InternalServerError)
        err.status_code = 500
        err.body = None
        assert is_retryable(err) is True

        err2 = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
        err2.status_code = 503
        err2.body = None
        assert is_retryable(err2) is True

    def test_overloaded_body_retries(self) -> None:
        import anthropic

        from app.runtime.llm import is_retryable

        # Overloaded can come back as 529 OR as another status with
        # body.error.type == "overloaded_error".
        err = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
        err.status_code = 400
        err.body = {"error": {"type": "overloaded_error", "message": "x"}}
        assert is_retryable(err) is True

    def test_4xx_except_429_and_overloaded_do_not_retry(self) -> None:
        import anthropic

        from app.runtime.llm import is_retryable

        err = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
        err.status_code = 400
        err.body = None
        assert is_retryable(err) is False

        auth = anthropic.AuthenticationError.__new__(anthropic.AuthenticationError)
        auth.status_code = 401
        auth.body = None
        assert is_retryable(auth) is False

    def test_validation_error_never_retries(self) -> None:
        from pydantic import BaseModel, ValidationError

        from app.runtime.llm import is_retryable

        class _M(BaseModel):
            x: int

        try:
            _M.model_validate({"x": "not-an-int"})
        except ValidationError as exc:
            assert is_retryable(exc) is False
        else:
            raise AssertionError("expected ValidationError")

    def test_random_exception_does_not_retry(self) -> None:
        from app.runtime.llm import is_retryable

        assert is_retryable(RuntimeError("random")) is False


# ---------------------------------------------------------------------------
# Token charging: AnthropicLLM.call() → Budget + runs.tokens_in/out
# ---------------------------------------------------------------------------


class TestTokenCharging:
    """We can't call the real Anthropic API in tests; instead we
    construct AnthropicLLM, inject a fake `_client` whose
    `messages.create` returns a mock response carrying a `usage`
    attribute, and watch Budget + runs.tokens_in/out.
    """

    async def test_usage_charges_budget_and_persists(self, db, bus, monkeypatch) -> None:
        from app.contracts import InputKind
        from app.db import repo
        from app.runtime.budgets import Budget
        from app.runtime.llm import AnthropicLLM

        # Seed a run so tokens_in / tokens_out have a row to update.
        await repo.insert_run(
            db,
            run_id="run_abc",
            branch_id="BR-NJ-01",
            input_kind=InputKind.pdf,
            input_name="rfp.pdf",
            created_at="2026-11-11T12:00:00+00:00",
        )
        # Set a real key so the constructor passes.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        import app.config as cfg

        monkeypatch.setattr(cfg, "_settings", None)

        llm = AnthropicLLM(db=db)

        class _Usage:
            input_tokens = 123
            output_tokens = 45

        class _Resp:
            usage = _Usage()

            def __init__(self) -> None:
                self.content: list = []

        class _Messages:
            async def create(self, **_kwargs):
                return _Resp()

        class _Client:
            def __init__(self) -> None:
                self.messages = _Messages()

        # Inject the fake client so we never hit the SDK.
        llm._client = _Client()

        budget = Budget(turns=10, tokens=1000, deadline_s=60.0)
        budget.start()

        await llm.call("hi", bus=bus, run_id="run_abc", budget=budget)

        # In-memory budget was charged.
        assert budget.snapshot()["tokens"] == 123 + 45
        # DB row was updated.
        cursor = await db.read_conn.execute(
            "SELECT tokens_in, tokens_out FROM runs WHERE id = 'run_abc'"
        )
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        assert row is not None
        assert row[0] == 123
        assert row[1] == 45

    async def test_token_limit_stops_the_run(self, db, bus, monkeypatch) -> None:
        """Budget with 100-token limit; a call that reports 200
        tokens must raise BudgetExhausted — the limit is enforced,
        not advisory."""

        from app.contracts import InputKind
        from app.db import repo
        from app.runtime.budgets import Budget, BudgetExhausted
        from app.runtime.llm import AnthropicLLM

        await repo.insert_run(
            db,
            run_id="run_abc",
            branch_id="BR-NJ-01",
            input_kind=InputKind.pdf,
            input_name="rfp.pdf",
            created_at="2026-11-11T12:00:00+00:00",
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        import app.config as cfg

        monkeypatch.setattr(cfg, "_settings", None)

        llm = AnthropicLLM(db=db)

        class _Usage:
            input_tokens = 150
            output_tokens = 100

        class _Resp:
            usage = _Usage()

            def __init__(self) -> None:
                self.content: list = []

        class _Messages:
            async def create(self, **_kwargs):
                return _Resp()

        class _Client:
            def __init__(self) -> None:
                self.messages = _Messages()

        llm._client = _Client()

        budget = Budget(turns=10, tokens=100, deadline_s=60.0)
        budget.start()

        with pytest.raises(BudgetExhausted) as exc:
            await llm.call("hi", bus=bus, run_id="run_abc", budget=budget)
        assert exc.value.which == "tokens"


# Silence the "unused os" warning — kept for future env tweaks.
_ = os
