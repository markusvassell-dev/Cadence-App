import logging
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..alerts import alert_admin
from ..config import get_settings
from ..content import ContentService
from ..distribution import DistributionService
from ..distributors import build_distributor
from ..embeddings import HashingEmbedder
from ..llm import AnthropicLLMClient
from ..orchestrator import run_pipeline
from ..research import ResearchService
from ..search import build_search_provider

logger = logging.getLogger(__name__)
router = APIRouter()


@lru_cache
def get_research_service() -> ResearchService:
    """Built once from settings. Exposed as a dependency so tests can override it
    with a fake (no search/LLM network calls). Cached so the Anthropic client is
    reused across runs."""
    settings = get_settings()
    return ResearchService(
        search_provider=build_search_provider(settings),
        llm_client=AnthropicLLMClient(settings.retry_policy()),
        store=db.DbPainPointStore(),
        model=settings.research_model,
        temperature=settings.research_temperature,
        max_tokens=settings.research_max_tokens,
        novelty_threshold=settings.novelty_threshold,
        max_retries=settings.research_max_retries,
        max_sources=settings.search_max_results,
    )


@lru_cache
def get_content_service() -> ContentService:
    """Built once from settings; overridable in tests like the research service."""
    settings = get_settings()
    return ContentService(
        llm_client=AnthropicLLMClient(settings.retry_policy()),
        embedder=HashingEmbedder(),
        store=db.DbContentStore(),
        model=settings.content_model,
        temperature=settings.content_temperature,
        max_tokens=settings.content_max_tokens,
        sim_threshold=settings.content_sim_threshold,
        max_retries=settings.content_max_retries,
    )


@lru_cache
def get_distribution_service() -> DistributionService:
    """Pre-publish audit + distribution. Uses the composed distributor (real
    channels where configured, else stub) and the registry-backed audit store."""
    return DistributionService(
        distributor=build_distributor(get_settings()),
        store=db.DbDistributionStore(),
    )


class RunRequest(BaseModel):
    market: str | None = None
    dry_run: bool = False
    approval_gate: bool | None = None  # None = inherit the human_approval_gate setting


class RunResponse(BaseModel):
    run_id: str
    status: str


@router.post("/hooks/run", response_model=RunResponse)
async def trigger_run(
    payload: RunRequest | None = None,
    research_service: ResearchService = Depends(get_research_service),
    content_service: ContentService = Depends(get_content_service),
    distribution_service: DistributionService = Depends(get_distribution_service),
) -> RunResponse:
    """Karbon's recurring webhook hits this. Creates a `running` run row, chains
    research -> generate -> audit -> distribute, and returns the outcome. With
    dry_run, research + generation run but nothing is posted (status stays review)."""
    market = (
        (payload.market if payload and payload.market else None)
        or await db.get_active_market()
        or get_settings().default_market
    )
    dry_run = payload.dry_run if payload else False
    approval_gate = (
        payload.approval_gate
        if (payload and payload.approval_gate is not None)
        else get_settings().human_approval_gate
    )

    triggered_at = datetime.now(timezone.utc)
    logger.info(
        "POST /hooks/run at %s (market=%r, dry_run=%s, approval_gate=%s)",
        triggered_at.isoformat(),
        market,
        dry_run,
        approval_gate,
    )

    run_id = await db.create_run(market=market, dry_run=dry_run)
    try:
        result = await run_pipeline(
            run_id,
            market,
            research_service=research_service,
            content_service=content_service,
            distribution_service=distribution_service,
            dry_run=dry_run,
            approval_gate=approval_gate,
        )
    except Exception as exc:
        logger.exception("run %s failed", run_id)
        await db.update_run_status(run_id, "failed", error=str(exc))
        # Retries are exhausted by the time the pipeline raises — alert an admin.
        await alert_admin(f"run failed: {exc}", run_id=run_id)
        return RunResponse(run_id=run_id, status="failed")

    # Dry runs preview only; gated real runs hold at review; else published.
    if dry_run or result.get("held_for_review"):
        status = "review"
    else:
        status = "published"
    await db.update_run_status(run_id, status)
    return RunResponse(run_id=run_id, status=status)
