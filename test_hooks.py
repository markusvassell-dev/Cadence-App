import os

import pytest
from fastapi.testclient import TestClient

from app.distribution import AuditResult
from app.main import app
from app.routers.hooks import (
    get_content_service,
    get_distribution_service,
    get_research_service,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"),
    reason="DATABASE_URL not set; skipping DB-backed integration test",
)


class FakeResearchService:
    async def research(self, run_id, market):
        return {
            "pain_point": f"pain point for {market}",
            "source_insight": "evidence",
            "source_url": None,
            "region": None,
            "novelty_self_score": 70,
        }


class FakeContentService:
    async def generate(self, run_id, pain_point):
        return {
            "content": {"blog": "blog", "linkedin": "linkedin", "facebook": "facebook", "instagram": "instagram"},
            "content_ids": {"blog": 1, "linkedin": 2, "facebook": 3, "instagram": 4},
            "lead_magnet": {"slug": "x", "url": "/lead-magnet/x"},
        }


class FakeDistributionService:
    async def audit(self, run_id, content_ids):
        return AuditResult(ok=True, collisions=[])

    async def publish(self, run_id, generated, *, dry_run):
        return {p: ("dry_run" if dry_run else "posted") for p in ("blog", "linkedin", "facebook", "instagram")}


def _override():
    app.dependency_overrides[get_research_service] = lambda: FakeResearchService()
    app.dependency_overrides[get_content_service] = lambda: FakeContentService()
    app.dependency_overrides[get_distribution_service] = lambda: FakeDistributionService()


def test_gated_run_holds_at_review():
    # Default: the human approval gate is ON, so a real run holds at review.
    _override()
    try:
        with TestClient(app) as client:
            response = client.post("/hooks/run", json={"market": "test market"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"].startswith("RUN-")
    assert body["status"] == "review"


def test_gate_off_run_is_published():
    # Per-run override turns the gate off -> auto-publish.
    _override()
    try:
        with TestClient(app) as client:
            response = client.post("/hooks/run", json={"market": "test market", "approval_gate": False})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "published"


def test_dry_run_is_review_only():
    _override()
    try:
        with TestClient(app) as client:
            response = client.post("/hooks/run", json={"market": "test market", "dry_run": True})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["status"] == "review"
