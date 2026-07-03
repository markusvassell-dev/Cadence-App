"""Reject / regenerate reason box (addendum): regenerate ONE piece with reviewer
feedback threaded into the model prompt, then re-run through the uniqueness engine
(and the blog voice check). Registered in app/main.py."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..content import ContentService
from .hooks import get_content_service

logger = logging.getLogger(__name__)
router = APIRouter()


class RegenerateIn(BaseModel):
    reason: str = ""  # reviewer feedback (chips + free text), appended to the prompt


@router.post("/content/{content_id}/regenerate")
async def regenerate_piece(
    content_id: int,
    payload: RegenerateIn,
    content_service: ContentService = Depends(get_content_service),
) -> dict:
    """Reject a pending piece and regenerate it with the reviewer's reason. Returns
    the new content id. The old row is marked 'superseded' so it neither publishes
    nor blocks the new draft on hash/similarity."""
    row = await db.get_content(content_id)
    if row is None:
        raise HTTPException(status_code=404, detail="content not found")
    if row["status"] == "published":
        raise HTTPException(status_code=409, detail="cannot regenerate an already-published piece")

    try:
        new_id = await content_service.regenerate_piece(
            run_id=row["run_id"], platform=row["platform"], reason=payload.reason
        )
    except Exception as exc:  # UniquenessError etc. — logged, surfaced to the reviewer
        logger.exception("regenerate failed for content #%s", content_id)
        raise HTTPException(status_code=502, detail=f"regeneration failed: {exc}")

    await db.supersede_content(content_id)
    logger.info(
        "content #%s (%s) regenerated -> #%s; reason=%r",
        content_id, row["platform"], new_id, payload.reason,
    )
    return {"ok": True, "old_id": content_id, "new_id": new_id, "reason": payload.reason}
