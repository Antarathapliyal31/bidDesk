"""Extraction-shape retry.

When the model returns a text-only response (no
`report_extraction` tool_use block), `_extract_tool_input` raises
`ExtractionShapeError`; `_AnalystHandler` converts it to a Pydantic
`ValidationError`; `delegate()` catches that and feeds back via
`ValidationFeedback`. The retry attempt sees the feedback and can
(on the FakeLLM path) return a valid payload.

Same rule applies to any forced-tool call added in later phases —
Phase 4's Pricing Agent will reuse this conversion so a text-only
response from the pricing model also triggers one retry with
feedback.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from app.agents.analyst.agent import register_default_analyst
from app.db.init import Database
from app.runtime.budgets import Budget
from app.runtime.delegate import AgentContext, unregister_all
from app.runtime.events import EventBus
from app.runtime.llm import FakeLLM, ScriptedResponse
from tests._seeds import seed_run


class _TextBlock:
    """Minimal duck-type for Anthropic SDK's TextBlock."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _ToolUseBlock:
    """Minimal duck-type for Anthropic SDK's ToolUseBlock."""

    def __init__(self, name: str, input_: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _SdkResponse:
    """Minimal SDK response — a list of content blocks plus an
    optional usage field. Mirrors what `extract_items` reads."""

    def __init__(self, content: list[Any], *, tokens_in: int = 100, tokens_out: int = 50) -> None:
        self.content = content

        class _Usage:
            pass

        self.usage = _Usage()
        self.usage.input_tokens = tokens_in
        self.usage.output_tokens = tokens_out


@pytest_asyncio.fixture
async def ctx(db: Database, bus: EventBus) -> AsyncIterator[AgentContext]:
    await seed_run(db)
    budget = Budget(turns=20, tokens=400_000, deadline_s=240.0)
    budget.start()
    yield AgentContext(run_id="run_abc", bus=bus, budget=budget)
    unregister_all()


class TestExtractionShapeRetry:
    async def test_text_only_first_response_triggers_validation_retry(
        self, ctx: AgentContext, db: Database
    ) -> None:
        """First attempt: SDK-shaped response with ONLY a TextBlock
        (model forgot to call the tool). Second attempt: valid
        tool_use with items. Expected: analyst returns ok with 1
        attempt=2, and one `validation.failed` event was emitted."""

        # pytest's samples_root — we use pasted text to avoid the
        # pdfplumber dependency; the shape is what matters here.
        pasted = "1. Nails ..... 10 boxes"
        first = _SdkResponse(content=[_TextBlock("Sure, I'll help!")])
        second = ScriptedResponse(
            content={
                "items": [
                    {
                        "kind": "material",
                        "description": "Nails",
                        "quantity_text": "10",
                        "unit_raw": "boxes",
                        "source_page": 1,
                        "source_quote": "1. Nails ..... 10 boxes",
                    }
                ],
                "facts": {
                    "project_name": None,
                    "due_date_text": None,
                    "general_notes": [],
                },
                "unreadable_pages": [],
            }
        )
        llm = FakeLLM(responses=[first, second])  # type: ignore[arg-type]
        register_default_analyst(llm)

        from app.agents.analyst.agent import AnalystRequest
        from app.contracts import AgentName
        from app.runtime.delegate import delegate

        result = await delegate(
            AgentName.document_analyst,
            AnalystRequest(kind="text", pasted_text=pasted, document_tag="d_test"),
            ctx=ctx,
        )
        assert result.status == "ok", result.errors
        assert result.attempts == 2

        # A validation.failed event records the first attempt's shape
        # error. The payload mentions the extraction_shape custom error.
        import json as _json

        cursor = await db.read_conn.execute(
            "SELECT payload_json FROM events WHERE type='validation.failed' ORDER BY id"
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        assert len(rows) == 1
        payload = _json.loads(rows[0][0])
        assert payload["will_retry"] is True
        assert any("extraction_shape" in e or "report_extraction" in e for e in payload["errors"])

    async def test_exhausted_shape_retries_returns_failed(self, ctx: AgentContext) -> None:
        """Both attempts return text-only → status=failed."""

        pasted = "1. Nails ..... 10 boxes"
        text_only = _SdkResponse(content=[_TextBlock("I can't help with that.")])
        llm = FakeLLM(responses=[text_only, text_only])  # type: ignore[arg-type]
        register_default_analyst(llm)

        from app.agents.analyst.agent import AnalystRequest
        from app.contracts import AgentName
        from app.runtime.delegate import delegate

        result = await delegate(
            AgentName.document_analyst,
            AnalystRequest(kind="text", pasted_text=pasted, document_tag="d_exhaust"),
            ctx=ctx,
        )
        assert result.status == "failed"
        assert result.attempts == 2
        assert any(e.kind.value == "validation" for e in result.errors)


# Keep pytest happy about the one unused import pattern above.
_ = pytest
