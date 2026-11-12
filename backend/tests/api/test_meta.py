"""`GET /api/meta` tests."""

from __future__ import annotations

from httpx import AsyncClient


class TestMeta:
    async def test_shape(self, client: AsyncClient) -> None:
        response = await client.get("/api/meta")
        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {
            "demo_faults_enabled",
            "faults",
            "models",
            "limits",
        }
        assert body["demo_faults_enabled"] is False
        assert isinstance(body["faults"], list)
        assert all(set(f.keys()) == {"key", "title", "description"} for f in body["faults"])
        assert set(body["models"].keys()) == {
            "orchestrator",
            "analyst",
            "analyst_text_only",
            "pricing",
        }
        assert set(body["limits"].keys()) == {
            "max_upload_mb",
            "max_pages",
            "max_text_chars",
        }

    async def test_faults_includes_known_keys(self, client: AsyncClient) -> None:
        response = await client.get("/api/meta")
        body = response.json()
        keys = {f["key"] for f in body["faults"]}
        # Keys SPEC §10.6 names as the demo repertoire.
        assert keys >= {
            "pricing_timeout_once",
            "pricing_timeout_always",
            "analyst_malformed_once",
            "pricing_hallucinated_sku",
            "orchestrator_no_submit",
        }
