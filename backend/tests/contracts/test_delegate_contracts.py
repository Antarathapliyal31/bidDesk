"""Tests for the AgentTask / AgentResult / AgentError / ValidationFeedback
contracts. Two disciplines:
  - AgentTask / AgentResult reject malformed input (in-bound contract).
  - AgentError / ValidationFeedback coerce or truncate rather than
    raising (error-path contracts — a crash while recording a failure
    would hide the failure)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from app.contracts import (
    AgentError,
    AgentName,
    AgentResult,
    AgentTask,
    ErrorKind,
    ValidationFeedback,
)

# ---------------------------------------------------------------------------
# Tiny payload type used by AgentTask[In] and AgentResult[Out] below
# ---------------------------------------------------------------------------


class _SampleIn(BaseModel):
    query: str


class _SampleOut(BaseModel):
    answer: str


def _ok_kwargs(output: _SampleOut | None, errors: list[AgentError]) -> dict:
    return {
        "task_id": "t-1",
        "agent": AgentName.pricing_agent,
        "output": output,
        "errors": errors,
        "attempts": 1,
        "duration_ms": 42,
    }


# ---------------------------------------------------------------------------
# AgentTask
# ---------------------------------------------------------------------------


class TestAgentTask:
    def test_minimum_task_valid(self) -> None:
        task = AgentTask[_SampleIn](
            task_id="t-1",
            run_id="r-1",
            agent=AgentName.pricing_agent,
            input=_SampleIn(query="2x6"),
            deadline_s=30.0,
        )
        assert task.attempt == 1
        assert task.input.query == "2x6"

    def test_deadline_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            AgentTask[_SampleIn](
                task_id="t-1",
                run_id="r-1",
                agent=AgentName.pricing_agent,
                input=_SampleIn(query="x"),
                deadline_s=0.0,
            )

    def test_attempt_ge_1(self) -> None:
        with pytest.raises(ValidationError):
            AgentTask[_SampleIn](
                task_id="t-1",
                run_id="r-1",
                agent=AgentName.pricing_agent,
                input=_SampleIn(query="x"),
                deadline_s=30.0,
                attempt=0,
            )

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AgentTask[_SampleIn].model_validate(
                {
                    "task_id": "t-1",
                    "run_id": "r-1",
                    "agent": "pricing_agent",
                    "input": {"query": "x"},
                    "deadline_s": 30.0,
                    "stray": "nope",
                }
            )


# ---------------------------------------------------------------------------
# AgentError — lenient (truncate / coerce, never raise for size)
# ---------------------------------------------------------------------------


class TestAgentError:
    def test_minimum_error(self) -> None:
        err = AgentError(kind=ErrorKind.timeout, message="deadline exceeded")
        assert err.ref is None
        assert err.code is None
        assert err.detail is None

    def test_long_message_truncates(self) -> None:
        err = AgentError(kind=ErrorKind.transport, message="x" * 900)
        assert len(err.message) == 500
        assert err.message.endswith("…")
        assert err.message.startswith("x" * 499)

    def test_short_message_unchanged(self) -> None:
        err = AgentError(kind=ErrorKind.transport, message="short")
        assert err.message == "short"

    def test_oversized_detail_becomes_marker(self) -> None:
        big = {"blob": "x" * 3000}
        assert len(json.dumps(big).encode("utf-8")) > 2048
        err = AgentError(kind=ErrorKind.validation, message="big", detail=big)
        assert err.detail is not None
        assert err.detail["truncated"] is True
        assert err.detail["bytes"] > 2048

    def test_non_json_detail_coerced_via_default_str(self) -> None:
        # A Decimal is not JSON-serializable by default. The coercion
        # layer stringifies it so the error path doesn't crash.
        err = AgentError(
            kind=ErrorKind.validation,
            message="bad",
            detail={"qty": Decimal("3.14")},
        )
        assert err.detail == {"qty": "3.14"}

    def test_non_json_opaque_object_also_coerced(self) -> None:
        class Opaque:
            def __str__(self) -> str:
                return "<opaque>"

        err = AgentError(
            kind=ErrorKind.validation,
            message="bad",
            detail={"obj": Opaque()},  # type: ignore[dict-item]
        )
        assert err.detail == {"obj": "<opaque>"}

    def test_ref_optional_for_per_item_failure(self) -> None:
        err = AgentError(
            kind=ErrorKind.validation,
            message="row dropped",
            ref="item_7",
            code="pydantic",
            detail={"loc": ["sale_qty"]},
        )
        assert err.ref == "item_7"


# ---------------------------------------------------------------------------
# ValidationFeedback
# ---------------------------------------------------------------------------


class TestValidationFeedback:
    def test_one_error_minimum(self) -> None:
        fb = ValidationFeedback(errors=["sale_qty: not a Decimal"], attempt=1)
        assert len(fb.errors) == 1

    def test_min_one_error_required(self) -> None:
        with pytest.raises(ValidationError):
            ValidationFeedback(errors=[], attempt=1)

    def test_11_errors_truncates_not_raises(self) -> None:
        errors = [f"e{i}" for i in range(11)]
        fb = ValidationFeedback(errors=errors, attempt=1)
        assert len(fb.errors) == 10
        assert fb.errors[0] == "e0"
        assert fb.errors[-1] == "e9"

    def test_long_string_truncates_to_300(self) -> None:
        fb = ValidationFeedback(errors=["x" * 500], attempt=2)
        assert len(fb.errors[0]) == 300
        assert fb.errors[0] == "x" * 300

    def test_non_list_still_validated(self) -> None:
        with pytest.raises(ValidationError):
            ValidationFeedback(errors="not a list", attempt=1)  # type: ignore[arg-type]

    def test_attempt_ge_1(self) -> None:
        with pytest.raises(ValidationError):
            ValidationFeedback(errors=["e"], attempt=0)


# ---------------------------------------------------------------------------
# AgentResult status-shape invariants
# ---------------------------------------------------------------------------


class TestAgentResultStatus:
    def test_ok_requires_output_and_no_errors(self) -> None:
        AgentResult[_SampleOut](status="ok", **_ok_kwargs(_SampleOut(answer="ok"), []))

    def test_ok_rejects_missing_output(self) -> None:
        with pytest.raises(ValidationError, match="status='ok' requires output"):
            AgentResult[_SampleOut](status="ok", **_ok_kwargs(None, []))

    def test_ok_rejects_non_empty_errors(self) -> None:
        err = AgentError(kind=ErrorKind.transport, message="x")
        with pytest.raises(ValidationError, match="status='ok' must have empty errors"):
            AgentResult[_SampleOut](status="ok", **_ok_kwargs(_SampleOut(answer="ok"), [err]))

    def test_partial_requires_output_and_errors(self) -> None:
        err = AgentError(kind=ErrorKind.validation, message="row dropped", ref="item_3")
        AgentResult[_SampleOut](status="partial", **_ok_kwargs(_SampleOut(answer="some"), [err]))

    def test_partial_rejects_missing_output(self) -> None:
        err = AgentError(kind=ErrorKind.validation, message="row dropped", ref="item_3")
        with pytest.raises(ValidationError, match="status='partial' requires output"):
            AgentResult[_SampleOut](status="partial", **_ok_kwargs(None, [err]))

    def test_partial_requires_at_least_one_error(self) -> None:
        with pytest.raises(ValidationError, match="status='partial' requires at least one error"):
            AgentResult[_SampleOut](status="partial", **_ok_kwargs(_SampleOut(answer="x"), []))

    def test_partial_requires_ref_on_every_error(self) -> None:
        with_ref = AgentError(kind=ErrorKind.validation, message="r1", ref="item_1")
        without_ref = AgentError(kind=ErrorKind.validation, message="r2")
        with pytest.raises(
            ValidationError,
            match=r"status='partial' requires ref on every error; errors\[1\] has ref=None",
        ):
            AgentResult[_SampleOut](
                status="partial",
                **_ok_kwargs(_SampleOut(answer="x"), [with_ref, without_ref]),
            )

    def test_failed_requires_output_none(self) -> None:
        err = AgentError(kind=ErrorKind.transport, message="HTTP 503")
        AgentResult[_SampleOut](status="failed", **_ok_kwargs(None, [err]))

    def test_failed_rejects_output_set(self) -> None:
        err = AgentError(kind=ErrorKind.transport, message="HTTP 503")
        with pytest.raises(ValidationError, match="status='failed' must have output=None"):
            AgentResult[_SampleOut](status="failed", **_ok_kwargs(_SampleOut(answer="x"), [err]))

    def test_failed_requires_errors(self) -> None:
        with pytest.raises(ValidationError, match="status='failed' requires at least one error"):
            AgentResult[_SampleOut](status="failed", **_ok_kwargs(None, []))

    def test_timeout_requires_timeout_error_kind(self) -> None:
        err = AgentError(kind=ErrorKind.timeout, message="deadline exceeded")
        AgentResult[_SampleOut](status="timeout", **_ok_kwargs(None, [err]))

    def test_timeout_rejects_output_set(self) -> None:
        err = AgentError(kind=ErrorKind.timeout, message="deadline exceeded")
        with pytest.raises(ValidationError, match="status='timeout' must have output=None"):
            AgentResult[_SampleOut](status="timeout", **_ok_kwargs(_SampleOut(answer="x"), [err]))

    def test_timeout_empty_errors_distinct_message(self) -> None:
        # Distinct from the "no timeout-kind error" message so each is
        # addressable from logs / tests.
        with pytest.raises(
            ValidationError,
            match=r"status='timeout' requires a non-empty errors list",
        ):
            AgentResult[_SampleOut](status="timeout", **_ok_kwargs(None, []))

    def test_timeout_rejects_only_non_timeout_errors(self) -> None:
        err = AgentError(kind=ErrorKind.transport, message="HTTP 503")
        with pytest.raises(
            ValidationError,
            match=r"status='timeout' requires at least one error with kind='timeout'",
        ):
            AgentResult[_SampleOut](status="timeout", **_ok_kwargs(None, [err]))


# ---------------------------------------------------------------------------
# AgentResult — JSON round-trip, wrong-shape output, extra fields
# ---------------------------------------------------------------------------


class TestAgentResultShape:
    def test_json_roundtrip_ok(self) -> None:
        original = AgentResult[_SampleOut](
            status="ok",
            **_ok_kwargs(_SampleOut(answer="forty"), []),
        )
        payload = original.model_dump(mode="json")
        revived = AgentResult[_SampleOut].model_validate(payload)
        assert revived == original

    def test_json_roundtrip_partial_with_errors(self) -> None:
        err = AgentError(
            kind=ErrorKind.validation,
            message="row dropped",
            ref="item_3",
            code="pydantic",
            detail={"loc": ["sale_qty"]},
        )
        original = AgentResult[_SampleOut](
            status="partial",
            **_ok_kwargs(_SampleOut(answer="some"), [err]),
        )
        payload = original.model_dump(mode="json")
        revived = AgentResult[_SampleOut].model_validate(payload)
        assert revived == original

    def test_wrong_output_shape_raises(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult[_SampleOut].model_validate(
                {
                    "task_id": "t",
                    "agent": "pricing_agent",
                    "status": "ok",
                    "output": {"wrong_field": "nope"},
                    "errors": [],
                    "attempts": 1,
                    "duration_ms": 1,
                }
            )

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult[_SampleOut].model_validate(
                {
                    "task_id": "t",
                    "agent": "pricing_agent",
                    "status": "ok",
                    "output": {"answer": "x"},
                    "errors": [],
                    "attempts": 1,
                    "duration_ms": 1,
                    "stray": "nope",
                }
            )
