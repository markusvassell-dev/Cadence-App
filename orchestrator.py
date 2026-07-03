import logging
from typing import Protocol

from .distribution import AuditResult, PublishAuditError

logger = logging.getLogger(__name__)


class ResearchProtocol(Protocol):
    async def research(self, run_id: str, market: str) -> dict: ...


class ContentProtocol(Protocol):
    async def generate(self, run_id: str, pain_point: dict) -> dict: ...


class DistributionProtocol(Protocol):
    async def audit(self, run_id: str, content_ids: dict) -> AuditResult: ...

    async def publish(self, run_id: str, generated: dict, *, dry_run: bool) -> dict: ...


async def run_pipeline(
    run_id: str,
    market: str,
    *,
    research_service: ResearchProtocol,
    content_service: ContentProtocol,
    distribution_service: DistributionProtocol,
    dry_run: bool = False,
    approval_gate: bool = True,
) -> dict:
    """research() -> generate() -> audit -> distribute(), chained.

    Phase 5 adds the pre-publish registry audit and a meaningful dry_run: research
    + generation always run; on dry_run the distribution step records what *would*
    post without calling any channel. A failed audit aborts before any posting.

    Addendum: when approval_gate is on (and not a dry run), the pipeline holds after
    the audit — nothing distributes until a person approves the pieces and releases
    the run via POST /runs/{run_id}/publish."""
    pain_point = await research_service.research(run_id, market)
    generated = await content_service.generate(run_id, pain_point)

    audit = await distribution_service.audit(run_id, generated["content_ids"])
    if not audit.ok:
        raise PublishAuditError(f"pre-publish audit failed: {audit.collisions}")

    if approval_gate and not dry_run:
        logger.info("run %s held for human approval (gate on)", run_id)
        return {
            "pain_point": pain_point,
            "generated": generated,
            "distribution": None,
            "dry_run": False,
            "held_for_review": True,
        }

    distribution = await distribution_service.publish(run_id, generated, dry_run=dry_run)
    return {
        "pain_point": pain_point,
        "generated": generated,
        "distribution": distribution,
        "dry_run": dry_run,
        "held_for_review": False,
    }
