"""DB-backed audit + distribution_log test against real Postgres. Skipped when
DATABASE_URL isn't set."""

import os

import pytest

from app import db
from app.content import content_hash
from app.distribution import DistributionService
from app.distributors import StubDistributor
from app.embeddings import HashingEmbedder

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)

EMBEDDER = HashingEmbedder()


@pytest.fixture
async def pool():
    await db.init_pool()
    yield
    await db.close_pool()


async def test_audit_and_dry_run_log(pool):
    run_id = await db.create_run(market="distribution test")
    text = "Cold chain storage failures spoil probiotics in rural clinics across the region"
    h = content_hash(text)
    store = db.DbDistributionStore()
    service = DistributionService(distributor=StubDistributor(), store=store)

    try:
        content_id = await db.insert_content(run_id, "blog", h, text[:280], text, EMBEDDER.embed(text))

        # audit passes for the real, uniquely-hashed piece...
        ok = await service.audit(run_id, {"blog": content_id})
        assert ok.ok is True

        # ...and fails for a content id that isn't in the registry
        missing = await service.audit(run_id, {"blog": 999999999})
        assert missing.ok is False

        # dry run logs a 'dry_run' result and posts nothing
        generated = {"content": {"blog": text}, "content_ids": {"blog": content_id}}
        results = await service.publish(run_id, generated, dry_run=True)
        assert results["blog"] == "dry_run"

        async with db._get_pool().acquire() as conn:
            row = await conn.fetchrow(
                "SELECT result, content_id FROM distribution_log WHERE run_id = $1 AND platform = 'blog'",
                run_id,
            )
            assert row["result"] == "dry_run"
            assert row["content_id"] == content_id
    finally:
        async with db._get_pool().acquire() as conn:
            await conn.execute("DELETE FROM distribution_log WHERE run_id = $1", run_id)
            await conn.execute("DELETE FROM content_registry WHERE run_id = $1", run_id)
            await conn.execute("DELETE FROM runs WHERE id = $1", run_id)
