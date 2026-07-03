"""Phase 2 research: real `research(market) -> {pain_point, source_insight, ...}`.

Queries a search/news provider for recent articles, passes the top results to
Claude with the Research Extraction prompt, then runs the uniqueness guard before
accepting and persisting the pain point. Persistence is behind a `PainPointStore`
protocol so the re-query loop can be unit-tested without Postgres.
"""

import logging
from typing import Optional, Protocol

from . import prompts
from .llm import JSONParseError, LLMClient
from .search import SearchProvider, format_sources
from .uniqueness import is_too_similar

logger = logging.getLogger(__name__)


class ResearchError(RuntimeError):
    """Raised when research can't produce a unique, grounded pain point."""


class PainPointStore(Protocol):
    async def get_existing_pain_points(self) -> list[str]: ...

    async def insert_pain_point(
        self, run_id: str, text: str, source_insight: str, source_url: Optional[str]
    ) -> None: ...

    async def set_run_research(
        self, run_id: str, novelty: Optional[int], region: Optional[str]
    ) -> None: ...


def _clamp_novelty(value) -> Optional[int]:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return None


class ResearchService:
    def __init__(
        self,
        *,
        search_provider: SearchProvider,
        llm_client: LLMClient,
        store: PainPointStore,
        model: str,
        temperature: float,
        max_tokens: int,
        novelty_threshold: float,
        max_retries: int,
        max_sources: int,
    ) -> None:
        self._search = search_provider
        self._llm = llm_client
        self._store = store
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._threshold = novelty_threshold
        self._max_retries = max_retries
        self._max_sources = max_sources

    async def research(self, run_id: str, market: str) -> dict:
        """Find one fresh, unique pain point for `market`; persist and return it."""
        existing = await self._store.get_existing_pain_points()

        results = await self._search.search(market, self._max_sources)
        if not results:
            raise ResearchError(f"No source articles found for market={market!r}")
        sources_block = format_sources(results)

        # `rejected` accumulates candidates the guard turned down this run, so each
        # re-query also steers the model away from those, not just prior runs.
        rejected: list[str] = []
        for attempt in range(self._max_retries):
            avoid = existing + rejected
            user = prompts.RESEARCH_USER.substitute(
                market=market,
                sources=sources_block,
                existing="\n".join(f"- {p}" for p in avoid) if avoid else "(none yet)",
            )
            if attempt > 0:
                user += prompts.REGENERATE_SUFFIX

            try:
                data = await self._llm.complete_json(
                    system=prompts.RESEARCH_SYSTEM,
                    user=user,
                    model=self._model,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                )
            except JSONParseError as exc:
                logger.warning("run %s research attempt %d: %s", run_id, attempt, exc)
                continue

            pain_point = (data.get("pain_point") or "").strip()
            if not pain_point:
                logger.warning("run %s research attempt %d: empty pain_point", run_id, attempt)
                continue

            if is_too_similar(pain_point, existing, self._threshold):
                logger.info(
                    "run %s research attempt %d: pain point too similar, re-querying",
                    run_id,
                    attempt,
                )
                rejected.append(pain_point)
                continue

            source_insight = (data.get("source_insight") or "").strip()
            source_url = data.get("source_url") or None
            region = (data.get("region") or "").strip() or None
            novelty = _clamp_novelty(data.get("novelty_self_score"))

            await self._store.insert_pain_point(run_id, pain_point, source_insight, source_url)
            await self._store.set_run_research(run_id, novelty, region)
            logger.info("run %s accepted pain point: %s", run_id, pain_point)

            return {
                "pain_point": pain_point,
                "source_insight": source_insight,
                "source_url": source_url,
                "region": region,
                "novelty_self_score": novelty,
            }

        raise ResearchError(
            f"Uniqueness exhausted after {self._max_retries} attempts for market={market!r}"
        )
