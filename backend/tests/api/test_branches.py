"""`GET /api/branches` tests."""

from __future__ import annotations

from httpx import AsyncClient


class TestBranches:
    async def test_lists_three_biddesk_branches(self, client: AsyncClient) -> None:
        response = await client.get("/api/branches")
        assert response.status_code == 200
        body = response.json()

        # The three seeded branches, sorted by id.
        assert [row["id"] for row in body] == ["BR-NJ-01", "BR-NY-01", "BR-PA-01"]
        assert [row["name"] for row in body] == [
            "BidDesk North Jersey",
            "BidDesk Long Island",
            "BidDesk Lehigh Valley",
        ]

    async def test_tax_rate_ppm_is_integer(self, client: AsyncClient) -> None:
        response = await client.get("/api/branches")
        body = response.json()
        for row in body:
            assert isinstance(row["tax_rate_ppm"], int)
            assert row["tax_rate_ppm"] >= 0
