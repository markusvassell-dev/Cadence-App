"""DB-backed POST/GET /leads tests against real Postgres. The ActiveCampaign sync
uses the default keyless stub; email drafting uses a fake LLM but persists to the
real DB via DbCampaignStore. Standalone connections seed/clean so they don't
contend with the app pool. Skipped when DATABASE_URL isn't set."""

import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app import db
from app.email_sequence import EmailSequenceService
from app.main import app
from app.routers.leads import get_email_service

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed test",
)

_RUN_ID = "RUN-LEADTEST"
_SLUG = "lead-test-slug"
_EMAILS = ["lead-test-json@example.com", "lead-test-form@example.com"]

_SEQUENCE = {
    "emails": [
        {"position": 1, "goal": "deliver", "timing": "immediately", "subject": "S1", "body": "Hi {{name}}"},
        {"position": 2, "goal": "educate", "timing": "day_3", "subject": "S2", "body": "B2"},
        {"position": 3, "goal": "soft_pitch", "timing": "day_7", "subject": "S3", "body": "B3"},
    ]
}


class FakeEmailLLM:
    async def complete_json(self, *, system, user, model, temperature, max_tokens):
        return _SEQUENCE


def _fake_email_service():
    return EmailSequenceService(
        llm_client=FakeEmailLLM(),
        store=db.DbCampaignStore(),
        model="claude-sonnet-4-6",
        temperature=0.7,
        max_tokens=4000,
        sender_name="The Cadence Team",
    )


async def _seed():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    await conn.execute(
        "INSERT INTO runs (id, market, status) VALUES ($1, 'm', 'review') ON CONFLICT (id) DO NOTHING",
        _RUN_ID,
    )
    await conn.execute(
        "INSERT INTO pain_points (run_id, text, source_insight) VALUES ($1, $2, $3)",
        _RUN_ID,
        "cold chain failures spoil probiotics",
        "evidence",
    )
    await conn.execute(
        "INSERT INTO lead_magnets (run_id, slug, headline, body_html) VALUES ($1, $2, $3, $4)",
        _RUN_ID,
        _SLUG,
        "Protect your cold chain",
        "<html>x</html>",
    )
    await conn.close()


async def _cleanup():
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    # campaign_emails cascade from campaigns; delete in FK-safe order.
    await conn.execute(
        "DELETE FROM campaigns WHERE lead_id IN (SELECT id FROM leads WHERE email = ANY($1::text[]))",
        _EMAILS,
    )
    await conn.execute("DELETE FROM leads WHERE email = ANY($1::text[])", _EMAILS)
    await conn.execute("DELETE FROM lead_magnets WHERE run_id = $1", _RUN_ID)
    await conn.execute("DELETE FROM pain_points WHERE run_id = $1", _RUN_ID)
    await conn.execute("DELETE FROM runs WHERE id = $1", _RUN_ID)
    await conn.close()


async def _fetch_lead(email):
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        lead = await conn.fetchrow("SELECT * FROM leads WHERE email = $1", email)
        n_emails = await conn.fetchval(
            "SELECT count(*) FROM campaign_emails ce JOIN campaigns c ON c.id = ce.campaign_id WHERE c.lead_id = $1",
            lead["id"],
        )
        return lead, n_emails
    finally:
        await conn.close()


def test_leads_endpoint_full_flow():
    import asyncio

    asyncio.run(_seed())
    app.dependency_overrides[get_email_service] = _fake_email_service
    try:
        with TestClient(app) as client:
            # 1. JSON submission -> JSON response, synced via stub, sequence drafted
            r = client.post("/leads", json={"name": "Ada Lovelace", "email": _EMAILS[0], "lead_source": _RUN_ID, "slug": _SLUG})
            assert r.status_code == 200
            body = r.json()
            assert body["sync_status"] == "synced"
            assert body["ac_contact_id"].startswith("stub-")
            assert body["campaign_id"] is not None

            # 2. Form submission (the real landing-page path) -> HTML success page
            form = client.post(
                "/leads",
                data={"name": "Grace Hopper", "email": _EMAILS[1], "lead_source": _RUN_ID, "slug": _SLUG},
            )
            assert form.status_code == 200
            assert "text/html" in form.headers["content-type"]
            assert "Thanks" in form.text

            # 3. Validation failure -> 422, no lead created
            bad = client.post("/leads", json={"name": "", "email": "nope"})
            assert bad.status_code == 422

            # 4. GET /leads lists the captured leads
            listing = client.get("/leads").json()["leads"]
            assert _EMAILS[0] in [lead["email"] for lead in listing]

        lead_json, n_emails_json = asyncio.run(_fetch_lead(_EMAILS[0]))
        assert lead_json["sync_status"] == "synced"
        assert lead_json["run_id"] == _RUN_ID
        assert lead_json["pain_point"] == "cold chain failures spoil probiotics"
        assert n_emails_json == 3  # the 3-email nurture sequence persisted
    finally:
        app.dependency_overrides.clear()
        import asyncio as _aio

        _aio.run(_cleanup())
