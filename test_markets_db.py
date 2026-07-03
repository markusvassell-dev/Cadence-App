"""DB-backed tests for the operator-managed markets. Skipped without DATABASE_URL."""

import os

import pytest

from app import db

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)


@pytest.fixture
async def clean_pool():
    await db.init_pool()
    async with db._get_pool().acquire() as conn:
        await conn.execute("DELETE FROM markets")
    yield
    async with db._get_pool().acquire() as conn:
        await conn.execute("DELETE FROM markets")
    await db.close_pool()


def _actives(markets):
    return [m for m in markets if m["is_active"]]


async def test_seed_then_add_activate_remove(clean_pool):
    # seed is a no-op setter of one active market; a second call is a no-op when non-empty
    await db.ensure_seed_market("Default focus")
    await db.ensure_seed_market("Should not be added")
    markets = await db.list_markets()
    assert [m["label"] for m in markets] == ["Default focus"]
    assert await db.get_active_market() == "Default focus"

    # add makes the new one active (exactly one active — the partial-index invariant)
    added = await db.add_market("Maternal health · South Asia")
    assert added["is_active"] is True
    markets = await db.list_markets()
    assert len(_actives(markets)) == 1
    assert await db.get_active_market() == "Maternal health · South Asia"

    # activate the other one -> still exactly one active
    default_id = next(m["id"] for m in markets if m["label"] == "Default focus")
    assert await db.set_active_market(default_id) is True
    assert len(_actives(await db.list_markets())) == 1
    assert await db.get_active_market() == "Default focus"

    # set_active on a missing id returns False
    assert await db.set_active_market(999999999) is False


async def test_remove_promotes_and_refuses_last(clean_pool):
    await db.add_market("First")
    await db.add_market("Second")  # now active
    markets = await db.list_markets()
    active_id = next(m["id"] for m in markets if m["is_active"])

    # deleting the active market promotes the most-recent remaining one
    assert await db.remove_market(active_id) == "ok"
    assert await db.get_active_market() is not None
    assert len(_actives(await db.list_markets())) == 1

    # refuses to delete the only remaining market
    last_id = (await db.list_markets())[0]["id"]
    assert await db.remove_market(last_id) == "last"

    # removing a non-existent market
    assert await db.remove_market(999999999) == "not_found"
