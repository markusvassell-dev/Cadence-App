import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import db
from .config import get_settings
from .db import close_pool, init_pool
from .routers import approvals, hooks, lead_magnets, leads, markets, regenerate

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    await db.ensure_seed_market(get_settings().default_market)
    yield
    await close_pool()


app = FastAPI(title="Cadence", lifespan=lifespan)
app.include_router(hooks.router)
app.include_router(lead_magnets.router)
app.include_router(leads.router)
app.include_router(markets.router)
app.include_router(approvals.router)
app.include_router(regenerate.router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
