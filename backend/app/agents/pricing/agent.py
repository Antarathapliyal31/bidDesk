"""Pricing Agent handler + `AgentSpec` registration.

Ties extraction (tool loop) + verify together. The orchestrator
(Phase 5) will call `delegate("pricing_agent", PricingRequest)`;
Phase 4 wires registration + an integration test invoking the
same path.

`tool_choice={"type":"auto"}` + ONE submit_matches tool, same as
analyst (DECISIONS #21). `CandidateSetViolation` and
`PricingShapeError` are both converted to Pydantic
`ValidationError` so `delegate()`'s single validation-retry
fires with structured feedback — the model gets to self-correct
within the batch.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.agents.pricing.extraction import (
    PricingLoopInput,
    PricingShapeError,
    run_pricing_loop,
)
from app.agents.pricing.verify import (
    CandidateSetViolation,
    MissingQueryViolation,
    _parse_qty_decimal,
    needs_length_but_missing,
    skipped_length_missing_result,
    skipped_no_quantity_result,
    verify_batch,
)
from app.catalog.semantic_layer import SemanticBundle
from app.contracts import AgentName
from app.contracts.pricing_agent import (
    ItemError,
    PricingItemRequest,
    PricingLineResult,
    PricingRequest,
    PricingResult,
)
from app.runtime.delegate import AgentContext, AgentSpec, register_agent


def _shape_error_to_validation_error(exc: Exception, where: str) -> ValidationError:
    """Convert a PricingShapeError / CandidateSetViolation into a
    Pydantic `ValidationError` so delegate()'s retry path fires
    with ValidationFeedback carrying the reason verbatim."""

    return ValidationError.from_exception_data(
        title="submit_matches",
        line_errors=[
            InitErrorDetails(
                type=PydanticCustomError(where, str(exc)),
                loc=("response",),
                input=None,
            )
        ],
    )


@dataclass
class _PricingHandler:
    """Carries the injected LLM + semantic bundle + catalog path
    between handler invocations. Tests pass a FakeLLM here."""

    llm: Any
    bundle: SemanticBundle
    catalog_db_path: Path
    max_steps: int = 6

    async def __call__(
        self,
        request: PricingRequest,
        ctx: AgentContext,
        feedback: Any,  # ValidationFeedback | None
    ) -> dict[str, Any]:
        # `feedback` is None on the first attempt and non-None on
        # the delegate-level retry. We use it as both (a) the
        # "last chance" signal (verify.py soft-fails per-item on
        # retry instead of raising) AND (b) the text-feedback
        # source we inject into the user prompt so the model
        # sees the exact rejection reason (e.g. "item_id=X: sku
        # not returned by query_catalog").
        is_retry = feedback is not None
        feedback_errors: list[str] | None = None
        if feedback is not None:
            errs = getattr(feedback, "errors", None)
            if errs:
                feedback_errors = [str(e) for e in errs]

        # Partition items: no parsed quantity → skipped, never
        # reach the model. SPEC §8.3 treats these as flagged lines
        # (the estimator fills the quantity in). Keeps the model
        # from inventing a quantity to "complete" the match.
        price_items, skipped_results = _partition_pricable(request.items)

        if not price_items:
            # Everything skipped — don't even start the loop.
            return _build_result(
                verify_results=skipped_results,
                candidate_set_failures=[],
                partial_item_ids=[],
                skipped_results=[],
            )

        try:
            outcome = await run_pricing_loop(
                self.llm,
                input_=PricingLoopInput(
                    run_id=ctx.run_id,
                    branch_id=request.branch_id,
                    items=price_items,
                    document_tag=f"document_{ctx.run_id}",
                    bundle=self.bundle,
                    catalog_db_path=self.catalog_db_path,
                    max_steps=self.max_steps,
                    feedback=feedback_errors,
                ),
                bus=ctx.bus,
                budget=ctx.budget,
            )
        except PricingShapeError as exc:
            raise _shape_error_to_validation_error(exc, "pricing_shape") from exc

        try:
            verify_result = verify_batch(
                outcome,
                request_items=price_items,
                bundle=self.bundle,
                catalog_db_path=self.catalog_db_path,
                branch_id=request.branch_id,
                is_retry=is_retry,
            )
        except MissingQueryViolation as exc:
            # First-attempt protocol violation: delegate retries
            # with feedback that names the offending items.
            raise _shape_error_to_validation_error(exc, "missing_query") from exc
        except CandidateSetViolation as exc:
            # First-attempt rejection: delegate retries with feedback.
            raise _shape_error_to_validation_error(exc, "candidate_set") from exc

        return _build_result(
            verify_results=verify_result.results,
            candidate_set_failures=verify_result.candidate_set_failures,
            partial_item_ids=outcome.partial_item_ids,
            skipped_results=skipped_results,
        )


def _partition_pricable(
    items: list[PricingItemRequest],
) -> tuple[list[PricingItemRequest], list[PricingLineResult]]:
    """Split a batch into (pricable, skipped). An item is skipped
    when code can tell the model has nothing to work with:
      - No parsable quantity → `quantity_missing`.
      - Piece-counted lumber (e.g. "2x10 deck ledger — 1 ea")
        with no stated length anywhere in the description or
        requirements → `length_missing`.
    Skipped items never reach the model; they surface as a
    question for the estimator (SPEC §8.3 refinement)."""

    pricable: list[PricingItemRequest] = []
    skipped: list[PricingLineResult] = []
    for req in items:
        qty = _parse_qty_decimal(req.item.quantity_text)
        if qty is None or qty <= 0:
            skipped.append(skipped_no_quantity_result(req.item.id))
            continue
        if needs_length_but_missing(req.item, req.requirements):
            skipped.append(skipped_length_missing_result(req.item.id))
            continue
        pricable.append(req)
    return pricable, skipped


def _build_result(
    *,
    verify_results: list[PricingLineResult],
    candidate_set_failures: list[tuple[str, str]],
    partial_item_ids: list[str],
    skipped_results: list[PricingLineResult],
) -> dict[str, Any]:
    """Compose the PricingResult envelope from verify output +
    code-skipped items. A single `partial` status covers both
    kinds of per-item failures (candidate_set + max_steps);
    quantity-missing skips do NOT flip status to partial because
    SPEC §8.3 treats them as deliberate flags, not failures."""

    all_results = [*verify_results, *skipped_results]
    item_errors = _build_item_errors(
        verify_results,
        candidate_set_failures=candidate_set_failures,
        partial_item_ids=partial_item_ids,
    )
    status = "partial" if item_errors else "ok"
    return PricingResult(
        status=status,
        results=all_results,
        item_errors=item_errors,
    ).model_dump(mode="python")


def _build_item_errors(
    results: list[PricingLineResult],
    *,
    candidate_set_failures: list[tuple[str, str]],
    partial_item_ids: list[str],
) -> list[ItemError]:
    """Translate per-item failure signals into `ItemError`s. One
    entry per failed item; `ref` is the item_id so downstream
    consumers can treat these uniformly with `AgentError.ref`."""

    errors: list[ItemError] = []
    seen: set[str] = set()
    cs_map = {iid: sku for iid, sku in candidate_set_failures}
    for iid in partial_item_ids:
        if iid in seen:
            continue
        seen.add(iid)
        errors.append(
            ItemError(
                ref=iid,
                kind="max_steps_exceeded",
                message=(
                    f"pricing loop hit max_steps before submitting a match for item_id={iid!r}"
                ),
            )
        )
    for iid, sku in cs_map.items():
        if iid in seen:
            continue
        seen.add(iid)
        errors.append(
            ItemError(
                ref=iid,
                kind="candidate_set",
                message=(
                    f"submitted sku={sku!r} was not returned by query_catalog "
                    f"for item_id={iid!r} (even after retry with feedback)"
                ),
            )
        )
    # Hallucinated SKU is now a per-item FAILURE (not a no_match —
    # per the SPEC-refinement: `no_match` is reserved for
    # "the agent searched and found nothing suitable" with
    # submitted sku=None).
    for r in results:
        if r.item_id in seen:
            continue
        if r.status != "failed":
            continue
        if "candidate_set" in r.verify_notes or "max_steps_exceeded" in r.verify_notes:
            continue
        if "hallucinated_sku" in r.verify_notes:
            errors.append(
                ItemError(
                    ref=r.item_id,
                    kind="hallucinated_sku",
                    message=(
                        f"submitted sku={r.priced.sku!r} is not in the catalog "
                        f"(item_id={r.item_id!r})"
                    ),
                )
            )
            seen.add(r.item_id)
            continue
        errors.append(
            ItemError(
                ref=r.item_id,
                kind="missing_proposal",
                message=(f"no submit_matches entry for item_id={r.item_id!r}"),
            )
        )
        seen.add(r.item_id)
    return errors


def build_pricing_spec(
    llm: Any,
    *,
    bundle: SemanticBundle,
    catalog_db_path: Path,
    timeout_s: float = 45.0,  # PRICING_BATCH_TIMEOUT_S default (SPEC §8.2)
    max_validation_retries: int = 1,
    max_steps: int = 6,
) -> AgentSpec[PricingRequest, PricingResult]:
    """Build the pricing `AgentSpec`. Register with
    `register_agent(build_pricing_spec(...))`."""

    handler = _PricingHandler(
        llm=llm,
        bundle=bundle,
        catalog_db_path=catalog_db_path,
        max_steps=max_steps,
    )

    async def _handler(req: PricingRequest, ctx: AgentContext, feedback: Any) -> dict[str, Any]:
        return await handler(req, ctx, feedback)

    return AgentSpec(
        name=AgentName.pricing_agent,
        input_model=PricingRequest,
        output_model=PricingResult,
        handler=_handler,
        timeout_s=timeout_s,
        max_validation_retries=max_validation_retries,
    )


def register_default_pricing(
    llm: Any,
    *,
    bundle: SemanticBundle,
    catalog_db_path: Path,
    max_steps: int = 6,
) -> None:
    """Shorthand for tests / lifespan."""

    register_agent(
        build_pricing_spec(
            llm,
            bundle=bundle,
            catalog_db_path=catalog_db_path,
            max_steps=max_steps,
        )
    )


# Keep imports pinned for static analysis.
_ = Callable, Awaitable
