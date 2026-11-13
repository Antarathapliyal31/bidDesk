"""Document Analyst handler + `AgentSpec` registration.

Ties the pieces together: routing → extraction → verify. Returns
a typed `AnalystResult` payload the orchestrator consumes via
`delegate("document_analyst", AnalystRequest)`.

The extraction LLM is injected on the handler (not read from
globals) so tests can pass `FakeLLM` and production can pass
`AnthropicLLM` without touching this file.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.agents.analyst.batched_extraction import extract_items_in_batches
from app.agents.analyst.extraction import (
    ExtractionInput,
    ExtractionShapeError,
    ExtractionTruncated,
    extract_items,
)
from app.agents.analyst.routing import PageExtract, route_pasted_text, route_pdf
from app.agents.analyst.verify import verify_items
from app.agents.analyst.vision import transcribe_vision_pages
from app.config import get_settings
from app.contracts import AgentName, Contract, ItemView
from app.runtime.delegate import AgentContext, AgentSpec, register_agent
from app.runtime.spotlight import new_run_tag


def _shape_error_to_validation_error(exc: ExtractionShapeError) -> ValidationError:
    """Convert an `ExtractionShapeError` (model didn't call the
    forced tool, or tool payload failed Pydantic) into a Pydantic
    `ValidationError` so `delegate()`'s retry path fires with
    `ValidationFeedback`. The feedback carries the shape message
    verbatim — the model gets told exactly what to fix."""

    return ValidationError.from_exception_data(
        title="report_extraction",
        line_errors=[
            InitErrorDetails(
                type=PydanticCustomError("extraction_shape", str(exc)),
                loc=("response",),
                input=None,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Analyst request / result contracts (orchestrator → analyst)
# ---------------------------------------------------------------------------


class AnalystRequest(Contract):
    """Orchestrator's input to the analyst. Either a PDF on disk
    or pasted text — never both (XOR, per SPEC §6.1)."""

    kind: Literal["pdf", "text"]
    pdf_path: str | None = None  # absolute or anchored path
    pasted_text: str | None = None
    document_tag: str = Field(default_factory=new_run_tag)


class AnalystResult(Contract):
    """Analyst's output — verified items + pages + document facts."""

    items: list[ItemView]
    page_routes: list[dict[str, Any]]  # [{page_no, route, text_source, table_issue}]
    unreadable_pages: list[int]
    project_name: str | None
    due_date_text: str | None


# ---------------------------------------------------------------------------
# Handler — type matches AgentSpec.handler
# ---------------------------------------------------------------------------


@dataclass
class _AnalystHandler:
    """Carries the injected LLM between handler invocations."""

    llm: Any  # FakeLLM in tests, AnthropicLLM in production

    async def __call__(
        self,
        request: AnalystRequest,
        ctx: AgentContext,
        feedback: Any,  # ValidationFeedback | None
    ) -> dict[str, Any]:
        settings = get_settings()
        pages = self._route(request)
        unreadable: list[int] = []

        # Vision + text+vision pages get a per-page transcription
        # call (DECISIONS #26). `transcribe_vision_pages` returns a
        # new pages list with transcriptions dropped into `.text`
        # and a list of page_nos that must go to `unreadable_pages`
        # (beyond the cap or empty transcription). For pasted-text
        # runs there is no PDF to rasterize — skip.
        if request.kind == "pdf" and request.pdf_path is not None:
            pages, vision_unreadable = await transcribe_vision_pages(
                self.llm,
                bus=ctx.bus,
                run_id=ctx.run_id,
                pdf_path=Path(request.pdf_path),
                pages=pages,
                document_tag=request.document_tag,
                max_pages=settings.max_vision_pages,
            )
            unreadable.extend(vision_unreadable)

        # A page contributes to extraction iff it has text to show —
        # that's text / text+vision pages (pdfplumber text) and
        # successfully-transcribed vision pages. Everything else is
        # unreadable.
        readable_pages = [p for p in pages if p.text]
        for p in pages:
            if not p.text and p.page_no not in unreadable:
                unreadable.append(p.page_no)

        if not readable_pages:
            return AnalystResult(
                items=[],
                page_routes=_route_dicts(pages),
                unreadable_pages=sorted(set(unreadable)),
                project_name=None,
                due_date_text=None,
            ).model_dump(mode="python")

        # Long-doc short-circuit (DECISIONS F22): skip the single
        # call entirely when the page count already exceeds the
        # safe-for-one-call threshold. The one-call path would
        # almost certainly hit `max_tokens` + eat a round-trip we
        # can't afford on the analyst deadline. Jump straight to
        # the batched pipeline; the trace says why.
        if len(readable_pages) > settings.single_call_max_pages:
            await ctx.bus.emit(
                type="agent.activity",
                run_id=ctx.run_id,
                agent=AgentName.document_analyst,
                payload={
                    "text": (
                        f"document is {len(readable_pages)} pages "
                        f"(> single_call_max_pages={settings.single_call_max_pages}) — "
                        "running extraction in page batches"
                    )[:200],
                },
            )
            batched = await extract_items_in_batches(
                self.llm,
                input_=ExtractionInput(
                    run_id=ctx.run_id,
                    pages=readable_pages,
                    document_tag=request.document_tag,
                ),
                bus=ctx.bus,
                batch_pages=settings.extraction_batch_pages,
                overlap_pages=settings.extraction_batch_overlap_pages,
                concurrency=settings.extraction_batch_concurrency,
                max_tokens=settings.analyst_max_tokens,
            )
            draft = batched.draft
        else:
            try:
                draft = await extract_items(
                    self.llm,
                    input_=ExtractionInput(
                        run_id=ctx.run_id,
                        pages=readable_pages,
                        document_tag=request.document_tag,
                    ),
                    bus=ctx.bus,
                    max_tokens=settings.analyst_max_tokens,
                )
            except ExtractionTruncated as exc:
                # DECISIONS #20 / F21: single-call hit `max_tokens`
                # (or the forced tool_use came back empty). Fall
                # back to page-batched extraction instead of
                # retrying the same oversized call. Trace message
                # is deliberately explicit so the Live view
                # surfaces the reason.
                await ctx.bus.emit(
                    type="agent.activity",
                    run_id=ctx.run_id,
                    agent=AgentName.document_analyst,
                    payload={
                        "text": (
                            "document too long for one extraction call "
                            f"({exc.reason}) — retrying in page batches"
                        )[:200],
                    },
                )
                batched = await extract_items_in_batches(
                    self.llm,
                    input_=ExtractionInput(
                        run_id=ctx.run_id,
                        pages=readable_pages,
                        document_tag=request.document_tag,
                    ),
                    bus=ctx.bus,
                    batch_pages=settings.extraction_batch_pages,
                    overlap_pages=settings.extraction_batch_overlap_pages,
                    concurrency=settings.extraction_batch_concurrency,
                    max_tokens=settings.analyst_max_tokens,
                )
                draft = batched.draft
            except ExtractionShapeError as exc:
                # Convert to Pydantic ValidationError so delegate's
                # validation-retry path fires with ValidationFeedback
                # instead of returning `status="failed"` on the first
                # malformed call. The feedback carries the reason
                # (e.g. "no report_extraction tool_use block").
                raise _shape_error_to_validation_error(exc) from exc

        items = verify_items(draft.items, pages=pages)
        unreadable.extend(draft.unreadable_pages)
        return AnalystResult(
            items=items,
            page_routes=_route_dicts(pages),
            unreadable_pages=sorted(set(unreadable)),
            project_name=draft.facts.project_name,
            due_date_text=draft.facts.due_date_text,
        ).model_dump(mode="python")

    def _route(self, request: AnalystRequest) -> list[PageExtract]:
        if request.kind == "pdf":
            if request.pdf_path is None:
                raise ValueError("AnalystRequest.kind='pdf' requires pdf_path")
            return route_pdf(Path(request.pdf_path))
        if request.pasted_text is None:
            raise ValueError("AnalystRequest.kind='text' requires pasted_text")
        return [route_pasted_text(request.pasted_text)]


def _route_dicts(pages: list[PageExtract]) -> list[dict[str, Any]]:
    return [
        {
            "page_no": p.page_no,
            "route": p.route,
            "text_source": p.text_source,
            "table_issue": p.table_issue,
        }
        for p in pages
    ]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def build_analyst_spec(
    llm: Any,
    *,
    timeout_s: float = 90.0,
    max_validation_retries: int = 1,
) -> AgentSpec[AnalystRequest, AnalystResult]:
    """Build the `AgentSpec` for the analyst. The caller
    `register_agent(spec)` wires it into the delegate registry."""

    handler = _AnalystHandler(llm=llm)

    async def _handler(req: AnalystRequest, ctx: AgentContext, feedback: Any) -> dict[str, Any]:
        return await handler(req, ctx, feedback)

    return AgentSpec(
        name=AgentName.document_analyst,
        input_model=AnalystRequest,
        output_model=AnalystResult,
        handler=_handler,
        timeout_s=timeout_s,
        max_validation_retries=max_validation_retries,
    )


def register_default_analyst(llm: Any) -> None:
    """Shorthand for tests / lifespan. Equivalent to
    `register_agent(build_analyst_spec(llm))`."""

    register_agent(build_analyst_spec(llm))


# For static-type parity.
_ = Callable, Awaitable
