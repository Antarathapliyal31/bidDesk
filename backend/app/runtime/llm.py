"""LLM client wrappers: `AnthropicLLM` and `FakeLLM`.

`AnthropicLLM` constructs `anthropic.AsyncAnthropic(max_retries=0)`
and routes every call through `with_retry` so our retry layer is
the only retry layer (SPEC §10.4 — never double-count). The
retry predicate is tight: 429, 5xx, timeout, overloaded,
connection errors. Any 4xx (except 429) is NOT retried — those
are the model telling us the request is wrong (e.g. ValidationError
from a shape bug), not a transient fault.

`FakeLLM` plays scripted responses in FIFO order; the demo path
never uses it (SPEC §14 + CLAUDE.md §4), and no test in this repo
touches the real Anthropic API (`LLM_MODE=fake` for tests + e2e).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.config import require_anthropic_key
from app.db import repo as _repo
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.events import EventBus
from app.runtime.retry import RetryPolicy, with_retry

# ---------------------------------------------------------------------------
# Retryability predicate
# ---------------------------------------------------------------------------
# `anthropic` is imported lazily inside the predicate so the SDK
# isn't a hard import-time dependency for tests that only use
# FakeLLM. Phase 3 wires the real client; the moment a real agent
# runs with LLM_MODE=real, the import resolves.


def is_retryable(exc: BaseException) -> bool:
    """True for transient-transport failures only.

    Retried: 429 (RateLimitError), 5xx (InternalServerError /
    APIStatusError.status_code>=500), timeouts, connection
    errors, and the `overloaded` shape Anthropic returns under
    heavy load.

    NOT retried: ValidationError (schema mismatch — Pydantic;
    handled one layer up by `delegate` with feedback), 4xx other
    than 429 (bad request / auth — our fault), anything else.
    """

    # asyncio.TimeoutError / builtin TimeoutError
    import asyncio

    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return True

    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — SDK always present in deps
        return False

    if isinstance(exc, anthropic.RateLimitError):
        return True
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APITimeoutError):
        return True
    # Server-side (5xx + "overloaded_error"). anthropic.APIStatusError
    # has `status_code`; "overloaded_error" responses come back as
    # 529 and surface as APIStatusError with that body.
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            return True
        # Belt+braces: Anthropic's overloaded body carries type
        # "overloaded_error" even at non-5xx statuses occasionally.
        body = getattr(exc, "body", None) or {}
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("type") == "overloaded_error":
            return True

    return False


def _retry_reason(exc: BaseException) -> str:
    """One-line reason for `retry.scheduled` events."""

    name = exc.__class__.__name__
    status = getattr(exc, "status_code", None)
    if status is not None:
        return f"{name}({status})"
    return name


# ---------------------------------------------------------------------------
# Scripted response type (used by FakeLLM)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScriptedResponse:
    """One scripted LLM response.

    `content` is either a plain dict (single-turn forced-tool
    shape — what the analyst uses) OR a list of block-like objects
    (`type` / `name` / `input` / `id` / `text`) mimicking the
    Anthropic SDK's `response.content` list. The pricing tool loop
    uses the list shape to feed scripted tool_use blocks across
    multiple turns. `tokens_in` / `tokens_out` feed `Budget.charge`.
    """

    content: dict[str, Any] | list[Any]
    tokens_in: int = 0
    tokens_out: int = 0
    # SDK's `stop_reason` surfaced on the response — set
    # `"max_tokens"` to simulate output-cap truncation in tests
    # (DECISIONS F21). Anthropic SDK responses expose this
    # attribute; the extraction layer reads it via `getattr`.
    stop_reason: str | None = None


# ---------------------------------------------------------------------------
# FakeLLM
# ---------------------------------------------------------------------------


@dataclass
class FakeLLM:
    """Plays scripted responses in FIFO order.

    Tests pass `FakeLLM(responses=[...])`; each `call()` pops and
    returns the next response. An empty queue raises
    `AssertionError` so the test fails on the exact line that ran
    out, not on a silent default.
    """

    responses: Sequence[ScriptedResponse]
    _idx: int = field(default=0, init=False)

    async def call(
        self,
        prompt: str,
        **_kwargs: Any,
    ) -> ScriptedResponse:
        """Matches AnthropicLLM.call() signature so handlers are
        interchangeable. All kwargs (bus, run_id, budget, policy,
        model, max_tokens, tools, tool_choice, ...) are ignored —
        FakeLLM never retries, never charges, and plays whatever
        the test scripted next regardless of prompt shape."""

        _ = (prompt, _kwargs)
        if self._idx >= len(self.responses):
            raise AssertionError(
                f"FakeLLM: exhausted after {self._idx} response(s); "
                "test needs to script another entry"
            )
        resp = self.responses[self._idx]
        self._idx += 1
        return resp

    @property
    def calls_made(self) -> int:
        """How many scripted entries have been consumed so far.
        Useful for `assert fake.calls_made == 2`."""

        return self._idx


# ---------------------------------------------------------------------------
# AnthropicLLM
# ---------------------------------------------------------------------------


def _usage_tokens(response: Any) -> tuple[int, int]:
    """Pull `(input_tokens, output_tokens)` off an SDK response.

    Resilient to both the real SDK's `response.usage.input_tokens`
    shape and a plain-dict `response.usage = {...}` used in tests.
    Zero when usage is missing so callers can charge unconditionally.
    """

    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    tokens_in = getattr(usage, "input_tokens", None)
    tokens_out = getattr(usage, "output_tokens", None)
    if tokens_in is None and isinstance(usage, dict):
        tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("output_tokens")
    return int(tokens_in or 0), int(tokens_out or 0)


class AnthropicLLM:
    """Thin wrapper around `anthropic.AsyncAnthropic(max_retries=0)`.

    `call()` sends one request and routes through `with_retry` so
    our retry layer is the ONLY retry layer. `is_retryable` is the
    tight transport predicate (429 / 5xx / overloaded / timeout /
    connection); validation and other 4xx propagate immediately so
    `delegate` can handle them with structured feedback.

    Charges response usage after every successful call:
      - `budget.charge(tokens_in, tokens_out, turns=0)` — fast
        in-memory accounting so the budget can STOP a run
        mid-flight when the cap is hit (BudgetExhausted).
      - `repo.add_run_tokens(db, run_id, ...)` — persisted to
        `runs.tokens_in` / `.tokens_out` for the history / trace.
      - Only charges after a successful response; failures (incl.
        retried-then-failed) don't accumulate.
    """

    def __init__(self, *, db: Database | None = None) -> None:
        self._api_key = require_anthropic_key()
        # The real client is constructed lazily so test runs that
        # never hit AnthropicLLM don't need the SDK import.
        self._client: Any | None = None
        self._db = db

    def _ensure_client(self) -> Any:
        if self._client is None:
            import anthropic  # type: ignore[import-not-found]

            # max_retries=0 so our retry layer is authoritative.
            self._client = anthropic.AsyncAnthropic(
                api_key=self._api_key,
                max_retries=0,
            )
        return self._client

    async def call(
        self,
        prompt: str,
        *,
        bus: EventBus,
        run_id: str,
        budget: Budget | None = None,
        policy: RetryPolicy | None = None,
        model: str = "claude-sonnet-5-5",
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        system: str | None = None,
        messages: list[dict[str, Any]] | None = None,
    ) -> Any:
        """One LLM call, wrapped with `with_retry`. Returns the raw
        SDK response so callers can walk `content` blocks.

        Pass `messages=` for a multi-turn tool loop (pricing agent).
        Pass bare `prompt` for the single-user-turn analyst shape.
        Explicit either/or — callers that use `messages` MUST pass
        the bus/run_id so retries emit on the right run.
        """

        _policy = policy or RetryPolicy()
        _messages = messages or [{"role": "user", "content": prompt}]

        async def _once() -> Any:
            client = self._ensure_client()
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": _messages,
                "tools": tools or [],
                "tool_choice": tool_choice or {"type": "auto"},
            }
            if system is not None:
                kwargs["system"] = system
            return await client.messages.create(**kwargs)

        response = await with_retry(
            _once,
            policy=_policy,
            bus=bus,
            run_id=run_id,
            is_retryable=is_retryable,
            reason_of=_retry_reason,
            budget=budget,
        )

        # Charge usage AFTER the call returns successfully. The
        # Budget call may raise BudgetExhausted — propagate, since
        # exceeding the budget stops the run.
        tokens_in, tokens_out = _usage_tokens(response)
        if tokens_in or tokens_out:
            if self._db is not None:
                await _repo.add_run_tokens(
                    self._db,
                    run_id=run_id,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )
            if budget is not None:
                # turns=0 so turn counting stays with the orchestrator.
                budget.charge(tokens_in=tokens_in, tokens_out=tokens_out, turns=0)
        return response
