from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from .. import db

router = APIRouter()


@router.get("/lead-magnet/{slug}", response_class=HTMLResponse)
async def lead_magnet_page(slug: str) -> HTMLResponse:
    """Serve a generated lead-magnet landing page (Phase 3)."""
    body_html = await db.get_lead_magnet_html(slug)
    if body_html is None:
        raise HTTPException(status_code=404, detail="lead magnet not found")
    return HTMLResponse(content=body_html)
