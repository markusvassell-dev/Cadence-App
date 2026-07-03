"""CRUD for the operator-managed market / search-focus list backing the dashboard
dropdown (addendum). Calls the db.* functions directly (same pattern as
routers/leads.py). Registered in app/main.py."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db

router = APIRouter()


class MarketIn(BaseModel):
    label: str


@router.get("/markets")
async def get_markets() -> dict:
    """List all markets, oldest first, with which one is active."""
    return {"markets": await db.list_markets()}


@router.post("/markets")
async def create_market(payload: MarketIn) -> dict:
    """Add a focus and make it the active one (mirrors the prototype's Add)."""
    label = payload.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="label is required")
    return await db.add_market(label, make_active=True)


@router.post("/markets/{market_id}/activate")
async def activate_market(market_id: int) -> dict:
    """Select a focus as the active one."""
    ok = await db.set_active_market(market_id)
    if not ok:
        raise HTTPException(status_code=404, detail="market not found")
    return {"ok": True, "active_id": market_id}


@router.delete("/markets/{market_id}")
async def delete_market(market_id: int) -> dict:
    """Remove a focus. Refuses to delete the last remaining one; if the active
    market is deleted, the most recent remaining market becomes active."""
    result = await db.remove_market(market_id)
    if result == "not_found":
        raise HTTPException(status_code=404, detail="market not found")
    if result == "last":
        raise HTTPException(status_code=409, detail="cannot delete the only market")
    return {"ok": True}
