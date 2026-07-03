"""DB-backed test for the Phase 2 pain-point store against real Postgres.

Skipped automatically when DATABASE_URL isn't set.
"""

import os

import pytest

from app import db

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)


@pytest.fixture
async def pool():
    await db.init_pool()
    yield
    await db.close_pool()


async def test_pain_point_store_roundtrip(pool):
    store = db.DbPainPointStore()
    run_id = await db.create_run(market="test market for store")

    before = set(await store.get_existing_pain_points())

    text = f"unique pain point {run_id}"
    await store.insert_pain_point(run_id, text, "supporting evidence", "https://example.org/x")
    await store.set_run_research(run_id, novelty=83, region="Sub-Saharan Africa")

    after = set(await store.get_existing_pain_points())
    assert text in after
    assert text not in before

    async with db._get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT novelty, region FROM runs WHERE id = $1", run_id)
        assert row["novelty"] == 83
        assert row["region"] == "Sub-Saharan Africa"

        # Clean up the rows this test created.
        await conn.execute("DELETE FROM pain_points WHERE run_id = $1", run_id)
        await conn.execute("DELETE FROM runs WHERE id = $1", run_id)
