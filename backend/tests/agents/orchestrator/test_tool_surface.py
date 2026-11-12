"""Phase-5a tool surface: `ask_document_analyst` is NOT visible
to the model until Phase 5c wires the real Q&A path.

Having a no-op tool in the trace would mislead the demo
("agent asked a question, got nothing back"). The handler + input
model + the code-only kickoff function all stay in-tree — only
the schema entry is removed from `ALL_TOOLS`."""

from __future__ import annotations

from app.agents.orchestrator.loop import _TOOL_HANDLERS
from app.agents.orchestrator.prompts import ALL_TOOLS


def _names(tools: list[dict]) -> set[str]:
    return {str(t["name"]) for t in tools}


class TestOrchestratorToolSurface:
    def test_six_tools_exposed(self) -> None:
        assert _names(ALL_TOOLS) == {
            "classify_items",
            "check_gap_rules",
            "add_requirements",
            "price_items",
            "flag_conflict",
            "submit_bid",
        }

    def test_ask_document_analyst_not_in_schema(self) -> None:
        assert "ask_document_analyst" not in _names(ALL_TOOLS)

    def test_ask_document_analyst_not_in_dispatch_table(self) -> None:
        """Even if the model fabricated the tool name, dispatch
        would fall through to the `unknown tool` error path —
        prove the handler table doesn't carry it."""

        assert "ask_document_analyst" not in _TOOL_HANDLERS

    def test_system_prompt_doesnt_name_the_tool(self) -> None:
        from app.agents.orchestrator.prompts import system_prompt

        assert "ask_document_analyst" not in system_prompt()
