"""DB-backed tests for the /markets endpoints. Skipped without DATABASE_URL."""

import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)


async def _reset():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("DELETE FROM markets")
    await conn.close()


def test_markets_crud_and_conflict():
    import asyncio

    asyncio.run(_reset())
    try:
        with TestClient(app) as client:  # lifespan seeds one active market
            listed = client.get("/markets").json()["markets"]
            assert len(listed) == 1 and listed[0]["is_active"]

            # add -> becomes active
            created = client.post("/markets", json={"label": "Maternal health · South Asia"})
            assert created.status_code == 200 and created.json()["is_active"] is True

            markets = client.get("/markets").json()["markets"]
            assert len(markets) == 2
            assert sum(1 for m in markets if m["is_active"]) == 1

            # activate the seeded one
            seeded = next(m for m in markets if not m["is_active"])
            act = client.post(f"/markets/{seeded['id']}/activate")
            assert act.status_code == 200
            assert client.get("/markets").json()["markets"]
            actives = [m for m in client.get("/markets").json()["markets"] if m["is_active"]]
            assert len(actives) == 1 and actives[0]["id"] == seeded["id"]

            # blank label -> 422
            assert client.post("/markets", json={"label": "   "}).status_code == 422

            # delete one is fine; deleting the last one 409s
            other = next(m for m in markets if m["id"] != seeded["id"])
            assert client.delete(f"/markets/{other['id']}").status_code == 200
            last = client.get("/markets").json()["markets"][0]
            assert client.delete(f"/markets/{last['id']}").status_code == 409

            # deleting a missing market -> 404
            assert client.delete("/markets/999999999").status_code == 404
    finally:
        import asyncio as _aio

        _aio.run(_reset())
