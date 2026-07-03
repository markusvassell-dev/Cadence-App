"""DB-backed test for GET /lead-magnet/{slug}. Uses standalone connections to
seed/clean so it doesn't contend with the app's connection pool."""

import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)

_RUN_ID = "RUN-EPTEST1"
_SLUG = "endpoint-test-slug"
_HTML = "<!doctype html><html><body><form action='/leads'>x</form></body></html>"


async def _seed():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute(
        "INSERT INTO runs (id, market, status) VALUES ($1, $2, 'running') ON CONFLICT (id) DO NOTHING",
        _RUN_ID,
        "endpoint test",
    )
    await conn.execute(
        "INSERT INTO lead_magnets (run_id, slug, headline, body_html) VALUES ($1, $2, $3, $4)",
        _RUN_ID,
        _SLUG,
        "Headline",
        _HTML,
    )
    await conn.close()


async def _cleanup():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute("DELETE FROM lead_magnets WHERE slug = $1", _SLUG)
    await conn.execute("DELETE FROM runs WHERE id = $1", _RUN_ID)
    await conn.close()


def test_lead_magnet_endpoint_serves_html_and_404s():
    asyncio.run(_seed())
    try:
        with TestClient(app) as client:
            ok = client.get(f"/lead-magnet/{_SLUG}")
            missing = client.get("/lead-magnet/no-such-slug")
        assert ok.status_code == 200
        assert "<form" in ok.text
        assert missing.status_code == 404
    finally:
        asyncio.run(_cleanup())
