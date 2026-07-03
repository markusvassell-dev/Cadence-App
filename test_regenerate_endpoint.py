"""DB-backed test for POST /content/{id}/regenerate. Skipped without DATABASE_URL."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app import db
from app.content import ContentService, content_hash
from app.embeddings import HashingEmbedder
from app.main import app
from app.routers.hooks import get_content_service

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)

EMBEDDER = HashingEmbedder()
_NEW_BLOG = "Financial literacy gaps leave smallholder farmers unable to reach microcredit programs and grow."


class FakeBlogLLM:
    def __init__(self):
        self.prompts = []

    async def complete_json(self, *, system, user, model, temperature, max_tokens):
        self.prompts.append(user)
        return {
            "meta_title": "T", "meta_description": "D", "headers": ["H2 a"],
            "body_markdown": _NEW_BLOG, "internal_link_suggestions": [], "cta": "c",
            "word_count": len(_NEW_BLOG.split()),
        }


def _fake_content_service():
    return ContentService(
        llm_client=FakeBlogLLM(), embedder=EMBEDDER, store=db.DbContentStore(),
        model="claude-sonnet-4-6", temperature=0.7, max_tokens=4000,
        sim_threshold=0.60, max_retries=4,
    )


async def _seed():
    await db.init_pool()
    try:
        run_id = await db.create_run(market="regen test")
        await db.update_run_status(run_id, "review")
        async with db._get_pool().acquire() as conn:
            await conn.execute(
                "INSERT INTO pain_points (run_id, text, source_insight) VALUES ($1, $2, $3)",
                run_id, "cold chain gaps", "evidence",
            )
            await conn.execute(
                "INSERT INTO lead_magnets (run_id, slug, headline, body_html) VALUES ($1, $2, $3, $4)",
                run_id, f"g-{run_id}", "Protect your cold chain", "<html>x</html>",
            )
        old = "Cold chain failures spoil probiotics across rural clinics almost every single week here."
        old_id = await db.insert_content(run_id, "blog", content_hash(old), old[:280], old, EMBEDDER.embed(old))
        return run_id, old_id
    finally:
        await db.close_pool()


async def _row_status(cid):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        return await conn.fetchval("SELECT status FROM content_registry WHERE id = $1", cid)
    finally:
        await conn.close()


async def _cleanup(run_id):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("DELETE FROM blog_meta WHERE content_id IN (SELECT id FROM content_registry WHERE run_id = $1)", run_id)
    await conn.execute("DELETE FROM content_registry WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM lead_magnets WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM pain_points WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM runs WHERE id = $1", run_id)
    await conn.close()


def test_regenerate_supersedes_old_and_creates_new():
    run_id, old_id = asyncio.run(_seed())
    app.dependency_overrides[get_content_service] = _fake_content_service
    try:
        with TestClient(app) as client:
            resp = client.post(f"/content/{old_id}/regenerate", json={"reason": "Too generic"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["old_id"] == old_id
            new_id = body["new_id"]
            assert isinstance(new_id, int) and new_id != old_id

        assert asyncio.run(_row_status(old_id)) == "superseded"  # old piece retired
        assert asyncio.run(_row_status(new_id)) == "pending"     # new draft awaits approval
    finally:
        app.dependency_overrides.clear()
        asyncio.run(_cleanup(run_id))
