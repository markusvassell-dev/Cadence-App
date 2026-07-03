"""Human approval gate: release + per-piece approval endpoints (addendum).

When settings.human_approval_gate is true, run_pipeline() stops after the pre-publish
audit and leaves the run at status='review' with its content_registry rows 'pending'.
These endpoints let a person approve the pieces and release the run to distribution.
Registered in app/main.py."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from .. import db
from ..distribution import DistributionService
from .hooks import get_distribution_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/runs/{run_id}/content")
async def run_content(run_id: str) -> dict:
    """The pieces produced by a run and their approval status (drives the review UI)."""
    run = await db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"run": run, "pieces": await db.list_run_content(run_id)}


@router.post("/content/{content_id}/approve")
async def approve_piece(content_id: int) -> dict:
    """Approve a single piece: status 'pending' -> 'approved', locked_at = now().
    Idempotent-ish — approving an already-approved piece is a no-op success."""
    result = await db.approve_content(content_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="content not found")
    return {"ok": True, "content_id": content_id, "status": "approved"}


@router.post("/runs/{run_id}/publish")
async def publish_run(
    run_id: str,
    force: bool = False,
    distribution_service: DistributionService = Depends(get_distribution_service),
) -> dict:
    """Release a held run to distribution. Requires every piece approved unless
    force=true. Re-runs the pre-publish audit, posts, then marks the run published.
    This is the gate opening — the only path from 'review' to 'published' when the
    approval gate is on."""
    run = await db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run["status"] == "published":
        return {"ok": True, "run_id": run_id, "status": "published", "note": "already published"}

    pending = await db.count_unapproved_content(run_id)
    if pending and not force:
        raise HTTPException(
            status_code=409,
            detail=f"{pending} piece(s) still awaiting approval; approve them or pass force=true",
        )

    generated = await db.get_run_generated(run_id)  # {content: {...}, content_ids: {...}}
    if not generated["content_ids"]:
        raise HTTPException(status_code=409, detail="run has no generated content to publish")

    # Final registry integrity check, same as the automated path.
    audit = await distribution_service.audit(run_id, generated["content_ids"])
    if not audit.ok:
        raise HTTPException(status_code=409, detail={"audit_failed": audit.collisions})

    results = await distribution_service.publish(run_id, generated, dry_run=False)
    await db.mark_run_content_published(run_id)
    await db.update_run_status(run_id, "published")
    logger.info("run %s released by approval gate: %s", run_id, results)
    return {"ok": True, "run_id": run_id, "status": "published", "distribution": results}
