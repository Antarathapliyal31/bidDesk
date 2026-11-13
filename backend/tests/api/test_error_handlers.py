"""Exception-handler tests. Two paths:

- `ApiError` subclass → canonical envelope at its http_status.
- Anything else → generic HTTP 500 with NO traceback / exception
  string / file paths leaked (SPEC §13).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.errors import install_exception_handlers
from app.contracts.errors import ApiError, ErrorCode, TooLargeError


def _build_test_app() -> FastAPI:
    """Minimal app with routes that intentionally raise. Keeps
    the error-handling tests independent of real Phase 2 routes."""

    from app.contracts.edits import EditRequest

    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/raise/api_error")
    def raise_api_error() -> None:
        raise ApiError(ErrorCode.invalid_branch, "No such branch 'BR-ZZ'")

    @app.get("/raise/too_large")
    def raise_too_large() -> None:
        raise TooLargeError(size_mb=42.3, limit_mb=25.0)

    @app.get("/raise/unhandled")
    def raise_unhandled() -> None:
        raise RuntimeError("secret traceback with path /etc/shadow")

    # Route that exercises the real `EditRequest` so a malformed
    # body goes through FastAPI's RequestValidationError handler.
    # We parse the JSON manually with the shared `TypeAdapter` and
    # re-raise as `RequestValidationError` — this mirrors how
    # FastAPI validates on PATCH /api/runs/{id}/bid, without the
    # discriminated-union ForwardRef resolution issue the
    # `response_model` / `Body(...)` paths hit under the strict
    # EditRequest + Annotated discriminator setup.
    from fastapi.exceptions import RequestValidationError
    from pydantic import TypeAdapter, ValidationError

    _edit_adapter: TypeAdapter[EditRequest] = TypeAdapter(EditRequest)

    @app.post("/edit-echo")
    async def edit_echo(body: dict) -> dict[str, int]:
        try:
            parsed = _edit_adapter.validate_python(body)
        except ValidationError as exc:
            # Re-raise as a FastAPI RequestValidationError so our
            # handler in app.api.errors picks it up — same end-to-
            # end path as a real route.
            raise RequestValidationError(exc.errors()) from exc
        return {"ops": len(parsed.ops)}

    return app


@pytest.fixture
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import app.config as cfg

    monkeypatch.setattr(cfg, "_settings", None)
    yield
    monkeypatch.setattr(cfg, "_settings", None)


@pytest_asyncio.fixture
async def test_client(_env: None, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    app = _build_test_app()
    # `raise_app_exceptions=False` lets Starlette render the 500
    # instead of re-raising to the test — mirrors real HTTP
    # behavior.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


class TestApiErrorHandler:
    async def test_simple_code_renders_envelope(self, test_client: AsyncClient) -> None:
        response = await test_client.get("/raise/api_error")
        assert response.status_code == 422
        assert response.json() == {
            "error": {
                "code": "invalid_branch",
                "message": "No such branch 'BR-ZZ'",
            }
        }

    async def test_typed_subclass_carries_details(self, test_client: AsyncClient) -> None:
        response = await test_client.get("/raise/too_large")
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "too_large"
        # Message is the default ceilToTenthMb-formatted string.
        assert "42.3 MB" in body["error"]["message"]
        assert "25.0 MB" in body["error"]["message"]
        assert body["error"]["details"]["size_mb"] == pytest.approx(42.3)
        assert body["error"]["details"]["limit_mb"] == pytest.approx(25.0)


class TestGeneric500:
    async def test_unhandled_exception_becomes_internal_error_envelope(
        self, test_client: AsyncClient
    ) -> None:
        response = await test_client.get("/raise/unhandled")
        assert response.status_code == 500
        body = response.json()
        assert body == {
            "error": {
                "code": "internal_error",
                "message": "Something went wrong on our end.",
            }
        }

    async def test_no_internals_leaked(self, test_client: AsyncClient) -> None:
        response = await test_client.get("/raise/unhandled")
        text = response.text
        # The exception message, file paths, and traceback markers
        # must NOT appear in the response body.
        assert "secret traceback" not in text
        assert "/etc/shadow" not in text
        assert "RuntimeError" not in text
        assert "Traceback" not in text
        assert 'File "' not in text


class TestRequestValidationEnvelope:
    """FastAPI's own `RequestValidationError` must be rendered in
    the SPEC §13 envelope — not the raw `detail` array — so the
    frontend's single parser works on every 4xx (DECISIONS F13)."""

    async def test_unknown_op_discriminator_returns_invalid_input_envelope(
        self, test_client: AsyncClient
    ) -> None:
        response = await test_client.post(
            "/edit-echo",
            json={
                "draft_revision": 1,
                "ops": [{"op": "set_quantity", "line_id": "l1", "sale_qty": "5"}],
            },
        )
        assert response.status_code == 422
        body = response.json()
        # Envelope shape, not FastAPI's raw `detail`.
        assert set(body.keys()) == {"error"}
        err = body["error"]
        assert err["code"] == "invalid_input"
        assert isinstance(err["message"], str) and err["message"]
        # Per-op breakdown lands under details.ops[*].{index, error}.
        ops = err["details"]["ops"]
        assert len(ops) == 1
        assert ops[0]["index"] == 0
        assert "set_quantity" in ops[0]["error"] or "tag" in ops[0]["error"]

    async def test_missing_required_field_is_also_envelope(
        self, test_client: AsyncClient
    ) -> None:
        # `set_unit_price` without override_reason — real cause of
        # the 422 the UI also hits on an override without reason.
        response = await test_client.post(
            "/edit-echo",
            json={
                "draft_revision": 1,
                "ops": [
                    {
                        "op": "set_unit_price",
                        "line_id": "l1",
                        "unit_price_cents": 2222,
                    }
                ],
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "invalid_input"
        ops = body["error"]["details"]["ops"]
        assert ops[0]["index"] == 0
        assert "override_reason" in ops[0]["error"]

    async def test_non_op_body_error_lands_under_null_index(
        self, test_client: AsyncClient
    ) -> None:
        # `draft_revision` missing entirely → not tied to an op.
        response = await test_client.post(
            "/edit-echo",
            json={"ops": []},
        )
        assert response.status_code == 422
        ops = response.json()["error"]["details"]["ops"]
        assert any(entry["index"] is None for entry in ops)
