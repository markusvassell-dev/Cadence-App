"""DB-backed tests for the approval-gate release flow. Skipped without DATABASE_URL."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app import db
from app.content import content_hash
from app.embeddings import HashingEmbedder
from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)

EMBEDDER = HashingEmbedder()


async def _seed_run():
    """Create a held run with two pending content pieces; return (run_id, ids)."""
    await db.init_pool()
    try:
        run_id = await db.create_run(market="gate test")
        await db.update_run_status(run_id, "review")
        ids = {}
        for platform, text in [
            ("blog", "Cold chain failures spoil probiotics across rural clinics every week."),
            ("linkedin", "Undiagnosed maternal anemia stems from missing point of care diagnostics."),
        ]:
            ids[platform] = await db.insert_content(
                run_id, platform, content_hash(text), text[:280], text, EMBEDDER.embed(text)
            )
        return run_id, ids
    finally:
        await db.close_pool()


async def _cleanup(run_id):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("DELETE FROM distribution_log WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM content_registry WHERE run_id = $1", run_id)
    await conn.execute("DELETE FROM runs WHERE id = $1", run_id)
    await conn.close()


async def _statuses(run_id):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        run = await conn.fetchval("SELECT status FROM runs WHERE id = $1", run_id)
        pieces = await conn.fetch("SELECT status FROM content_registry WHERE run_id = $1", run_id)
        return run, [p["status"] for p in pieces]
    finally:
        await conn.close()


def test_gate_release_requires_approval_then_publishes():
    run_id, ids = asyncio.run(_seed_run())
    try:
        with TestClient(app) as client:
            # review UI: pieces are pending
            content = client.get(f"/runs/{run_id}/content").json()
            assert {p["status"] for p in content["pieces"]} == {"pending"}

            # publishing while pieces are pending is refused
            blocked = client.post(f"/runs/{run_id}/publish")
            assert blocked.status_code == 409

            # approve each piece -> status approved
            for cid in ids.values():
                r = client.post(f"/content/{cid}/approve")
                assert r.status_code == 200 and r.json()["status"] == "approved"

            # now release: audit re-runs, stub distribution posts, run -> published
            released = client.post(f"/runs/{run_id}/publish")
            assert released.status_code == 200
            assert released.json()["status"] == "published"

        run_status, piece_statuses = asyncio.run(_statuses(run_id))
        assert run_status == "published"
        assert set(piece_statuses) == {"published"}
    finally:
        asyncio.run(_cleanup(run_id))


def test_force_publishes_despite_pending():
    run_id, _ = asyncio.run(_seed_run())
    try:
        with TestClient(app) as client:
            forced = client.post(f"/runs/{run_id}/publish", params={"force": "true"})
            assert forced.status_code == 200
            assert forced.json()["status"] == "published"
        run_status, _ = asyncio.run(_statuses(run_id))
        assert run_status == "published"
    finally:
        asyncio.run(_cleanup(run_id))
