"""DB-backed tests for the Phase 3 content store (pgvector cosine, blog_meta,
lead magnets) against real Postgres. Skipped when DATABASE_URL isn't set."""

import json
import os

import pytest

from app import db
from app.content import content_hash
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


async def test_content_registry_pgvector_roundtrip(pool):
    store = db.DbContentStore()
    run_id = await db.create_run(market="content store test")

    ta = "Cold chain storage failures spoil temperature sensitive probiotics in rural clinics"
    tc = "Financial literacy gaps leave urban smallholder farmers without microcredit access"
    ha = content_hash(ta)

    try:
        content_id = await store.insert_content(
            run_id, "blog", ha, ta[:280], ta, EMBEDDER.embed(ta)
        )
        assert isinstance(content_id, int)

        # exact-hash guard
        assert await store.content_hash_exists("blog", ha) is True
        assert await store.content_hash_exists("blog", content_hash("unrelated text")) is False

        # cosine guard via pgvector: identical ~1.0, distinct topic low,
        # different platform empty -> None
        sim_same = await store.content_max_cosine("blog", EMBEDDER.embed(ta))
        sim_diff = await store.content_max_cosine("blog", EMBEDDER.embed(tc))
        sim_other_platform = await store.content_max_cosine("linkedin", EMBEDDER.embed(ta))

        assert sim_same is not None and sim_same > 0.99
        assert sim_diff is not None and sim_diff < 0.5
        assert sim_other_platform is None

        # blog metadata (JSONB columns)
        await store.insert_blog_meta(
            content_id,
            {
                "meta_title": "T",
                "meta_desc": "D",
                "headers": ["H2 a", "H2 b"],
                "internal_link_suggestions": [{"anchor": "x", "target_topic": "y"}],
                "word_count": 1234,
            },
        )
        async with db._get_pool().acquire() as conn:
            row = await conn.fetchrow("SELECT headers, word_count FROM blog_meta WHERE content_id = $1", content_id)
            assert row["word_count"] == 1234
            # asyncpg returns jsonb as a raw JSON string unless a codec is registered;
            # the app only writes blog_meta, so we decode here to verify storage.
            assert json.loads(row["headers"]) == ["H2 a", "H2 b"]

        # lead magnet roundtrip
        await store.insert_lead_magnet(run_id, f"slug-{run_id}", "Headline", "<html>x</html>")
        assert await db.get_lead_magnet_html(f"slug-{run_id}") == "<html>x</html>"
        assert await db.get_lead_magnet_html("does-not-exist") is None
    finally:
        async with db._get_pool().acquire() as conn:
            await conn.execute("DELETE FROM blog_meta WHERE content_id IN (SELECT id FROM content_registry WHERE run_id = $1)", run_id)
            await conn.execute("DELETE FROM content_registry WHERE run_id = $1", run_id)
            await conn.execute("DELETE FROM lead_magnets WHERE run_id = $1", run_id)
            await conn.execute("DELETE FROM runs WHERE id = $1", run_id)
