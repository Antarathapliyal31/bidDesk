"""Extraction call (SPEC §6.3).

Given a routed set of pages, call the LLM (FakeLLM in tests,
AnthropicLLM in production) with the spotlight-wrapped document
text and the forced `report_extraction` tool. Return the raw
parsed draft dict — verification lives in `verify.py` so this
module stays focused on the LLM boundary.

Vision pages participate when `vision.transcribe_vision_pages`
has populated their `.text` with a transcription. The extraction
call consumes the text/transcription as one spotlight-wrapped
block; `verify.py` is what decides `read_via='vision'` for items
whose source_page was originally routed `vision` or `text+vision`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from app.agents.analyst.prompts import (
    REPORT_EXTRACTION_TOOL,
    system_prompt,
    user_prompt,
)
from app.agents.analyst.routing import PageExtract
from app.runtime.events import EventBus
from app.runtime.spotlight import spotlight

# ---------------------------------------------------------------------------
# Draft item shape (post-model, pre-verification)
# ---------------------------------------------------------------------------


class ItemDraft(BaseModel):
    """What the model returns in `report_extraction.items[*]`.

    This is the shape BEFORE `verify.py` runs. Verification adds
    the `source.verified` decision and `notes`.
    """

    kind: Literal["material", "labor", "exclusion", "note"]
    description: str = Field(min_length=2, max_length=300)
    quantity_text: str | None = None
    unit_raw: str | None = Field(default=None, max_length=20)
    source_page: int = Field(ge=1)
    source_quote: str = Field(min_length=3, max_length=300)


class GeneralNoteDraft(BaseModel):
    page: int = Field(ge=1)
    quote: str = Field(min_length=3, max_length=300)


class DocumentFactsDraft(BaseModel):
    project_name: str | None = None
    due_date_text: str | None = None
    general_notes: list[GeneralNoteDraft] = Field(default_factory=list)


class ExtractionDraft(BaseModel):
    """The whole `report_extraction` payload (pre-verify)."""

    items: list[ItemDraft]
    facts: DocumentFactsDraft = Field(default_factory=DocumentFactsDraft)
    unreadable_pages: list[int] = Field(default_factory=list)


@dataclass(frozen=True)
class ExtractionInput:
    """What `extract_items` receives from the analyst entry point."""

    run_id: str
    pages: list[PageExtract]
    document_tag: str  # from `new_run_tag()`; used to spotlight pages


# ---------------------------------------------------------------------------
# LLM shape (duck-typed — matches both FakeLLM and AnthropicLLM)
# ---------------------------------------------------------------------------


class _LLMCallable(Protocol):
    """Minimal interface the extraction call needs. `FakeLLM.call`
    and `AnthropicLLM.call` both satisfy this at the call sites we
    care about (the `tools=` kwarg is accepted by both)."""

    async def call(self, prompt: str, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def _text_pages_block(pages: list[PageExtract], tag: str) -> str:
    """Join readable pages into a single spotlight-safe block
    tagged with `[[page N]]` markers (SPEC §6.3).

    Readable means `p.text` is non-empty. For text / text+vision
    pages that's pdfplumber output; for transcribed vision pages
    it's the vision transcription (set by
    `vision.transcribe_vision_pages`). The marker also carries the
    read origin (`image`) so the model knows which pages came from
    transcription vs. the text layer — the LINE itself is still
    stamped `read_via='vision'` by `verify.py` based on the page's
    ORIGINAL route, not this marker.
    """

    chunks: list[str] = []
    for p in pages:
        if not p.text:
            # Pages with empty text layer AND no transcription
            # (either not a vision page we handled, or an empty
            # transcription from a scan). Skipped here; the caller
            # surfaces them in `unreadable_pages`.
            continue
        if p.route in ("vision", "text+vision"):
            chunks.append(f"[[page {p.page_no} (image)]]\n{p.text}")
        else:
            chunks.append(f"[[page {p.page_no}]]\n{p.text}")
    return "\n\n".join(chunks)


def build_prompt(input_: ExtractionInput) -> tuple[str, str]:
    """Return (system, user) prompt strings. `user` wraps the
    document text through `spotlight(tag=…)` — typed, escaped,
    wrapped — so bypassing it would require a type error."""

    body = _text_pages_block(input_.pages, input_.document_tag)
    doc = spotlight(body, tag=input_.document_tag)
    return system_prompt(), user_prompt(doc)


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------


class ExtractionShapeError(Exception):
    """Raised when the model's `report_extraction` payload doesn't
    fit `ExtractionDraft`. `delegate()` catches this one layer up
    and shapes it into a validation retry if the retry budget is
    still alive."""


async def extract_items(
    llm: _LLMCallable,
    *,
    input_: ExtractionInput,
    bus: EventBus,
) -> ExtractionDraft:
    """Make ONE extraction call. Returns the parsed draft on
    success; raises `ExtractionShapeError` on malformed output so
    the delegate can re-prompt with feedback.

    `bus` + `run_id` are threaded so `AnthropicLLM.call()` can
    emit retry.scheduled events through its with_retry layer.
    `FakeLLM.call()` ignores them.
    """

    system, user = build_prompt(input_)
    prompt = f"{system}\n\n{user}"  # FakeLLM just needs one string
    # `tool_choice={"type":"tool",...}` is unsupported on some
    # Claude 5.x models (BadRequestError: "tool_choice: type 'tool'
    # and 'any' are not supported for this model"). We rely on the
    # system prompt — "Call `report_extraction` exactly once at
    # the end." — to drive the single tool call. `type: "auto"`
    # with ONE tool offered works everywhere.
    response = await llm.call(
        prompt,
        bus=bus,
        run_id=input_.run_id,
        tools=[REPORT_EXTRACTION_TOOL],
        tool_choice={"type": "auto"},
    )

    tool_input = _extract_tool_input(response)
    try:
        return ExtractionDraft.model_validate(tool_input)
    except ValidationError as exc:
        raise ExtractionShapeError(f"report_extraction payload failed validation: {exc}") from exc


def _extract_tool_input(response: Any) -> dict[str, Any]:
    """Pull `report_extraction.input` out of the SDK response OR
    accept a bare `ScriptedResponse` from FakeLLM.

    FakeLLM shape: `ScriptedResponse(content={"items": [...], ...})`.
    SDK shape: `response.content` is a list of blocks; find the
    `tool_use` block for `report_extraction`.
    """

    # FakeLLM: ScriptedResponse with `content` dict.
    content = getattr(response, "content", None)
    if isinstance(content, dict):
        return content

    # Anthropic SDK: content is a list of blocks.
    if isinstance(content, list):
        for block in content:
            if (
                getattr(block, "type", None) == "tool_use"
                and getattr(block, "name", None) == "report_extraction"
            ):
                return dict(getattr(block, "input", {}) or {})
    raise ExtractionShapeError("no report_extraction tool_use block in response")
