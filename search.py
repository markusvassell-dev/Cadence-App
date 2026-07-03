"""Search/news providers for Phase 2 research.

A swappable `SearchProvider` interface (mirroring the `Distributor` pattern from
Phase 1) so the research step can pull recent source material from whichever
backend is configured. Keyless GDELT is the default so the pipeline runs without
paid API keys; SerpAPI and NewsAPI are available when keys are present.

Phase 5: every request is wrapped in retry-with-exponential-backoff.
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from .config import Settings
from .retry import RetryPolicy, is_retryable_httpx, retry_async

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: Optional[str] = None
    source: Optional[str] = None
    published: Optional[str] = None


class SearchProvider(Protocol):
    async def search(self, market: str, max_results: int) -> list[SearchResult]: ...


def _derive_query(market: str, override: Optional[str]) -> str:
    """Turn a market description into a search query.

    The market string (e.g. "health & wellness, underdeveloped and emerging
    markets") is too long/AND-heavy for most engines, so we take the core topic
    before the first comma and keep alphanumerics + a few words.
    """
    if override:
        return override
    core = market.split(",")[0]
    words = re.findall(r"[A-Za-z0-9]+", core)
    return " ".join(words[:4]) or core.strip()


async def _get_json(url: str, params: dict, *, policy: RetryPolicy, headers: dict | None = None) -> dict:
    async def _call() -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            return resp.json()

    return await retry_async(_call, policy=policy, should_retry=is_retryable_httpx, description=f"GET {url}")


class GDELTSearchProvider:
    """GDELT 2.0 DOC API — free, keyless, recent global news. Returns article
    titles + URLs (no body text), which is enough to seed extraction."""

    BASE = "https://api.gdeltproject.org/api/v2/doc/doc"

    def __init__(self, query: Optional[str] = None, timespan: str = "3m", policy: RetryPolicy = RetryPolicy()):
        self._query = query
        self._timespan = timespan
        self._policy = policy

    async def search(self, market: str, max_results: int) -> list[SearchResult]:
        query = _derive_query(market, self._query)
        params = {
            "query": query,
            "mode": "ArtList",
            "format": "json",
            "maxrecords": str(max_results),
            "sort": "DateDesc",
            "timespan": self._timespan,
        }
        data = await _get_json(self.BASE, params, policy=self._policy)
        articles = data.get("articles") or []
        results = [
            SearchResult(
                title=a.get("title", "").strip(),
                url=a.get("url", ""),
                source=a.get("domain"),
                published=a.get("seendate"),
            )
            for a in articles
            if a.get("title")
        ]
        logger.info("GDELT returned %d articles for query=%r", len(results), query)
        return results[:max_results]


class NewsAPISearchProvider:
    """NewsAPI v2 /everything — requires NEWSAPI_KEY. Provides description text."""

    BASE = "https://newsapi.org/v2/everything"

    def __init__(self, api_key: str, query: Optional[str] = None, policy: RetryPolicy = RetryPolicy()):
        self._api_key = api_key
        self._query = query
        self._policy = policy

    async def search(self, market: str, max_results: int) -> list[SearchResult]:
        query = _derive_query(market, self._query)
        params = {
            "q": query,
            "sortBy": "publishedAt",
            "language": "en",
            "pageSize": str(max_results),
            "apiKey": self._api_key,
        }
        data = await _get_json(self.BASE, params, policy=self._policy)
        articles = data.get("articles") or []
        results = [
            SearchResult(
                title=(a.get("title") or "").strip(),
                url=a.get("url", ""),
                snippet=a.get("description"),
                source=(a.get("source") or {}).get("name"),
                published=a.get("publishedAt"),
            )
            for a in articles
            if a.get("title")
        ]
        logger.info("NewsAPI returned %d articles for query=%r", len(results), query)
        return results[:max_results]


class SerpAPISearchProvider:
    """SerpAPI google_news engine — requires SERPAPI_KEY. Provides snippets."""

    BASE = "https://serpapi.com/search"

    def __init__(self, api_key: str, query: Optional[str] = None, policy: RetryPolicy = RetryPolicy()):
        self._api_key = api_key
        self._query = query
        self._policy = policy

    async def search(self, market: str, max_results: int) -> list[SearchResult]:
        query = _derive_query(market, self._query)
        params = {
            "engine": "google_news",
            "q": query,
            "api_key": self._api_key,
        }
        data = await _get_json(self.BASE, params, policy=self._policy)
        articles = data.get("news_results") or []
        results = [
            SearchResult(
                title=(a.get("title") or "").strip(),
                url=a.get("link", ""),
                snippet=a.get("snippet"),
                source=a.get("source", {}).get("name") if isinstance(a.get("source"), dict) else a.get("source"),
                published=a.get("date"),
            )
            for a in articles
            if a.get("title")
        ]
        logger.info("SerpAPI returned %d articles for query=%r", len(results), query)
        return results[:max_results]


def build_search_provider(settings: Settings) -> SearchProvider:
    provider = settings.search_provider.lower()
    policy = settings.retry_policy()
    if provider == "gdelt":
        return GDELTSearchProvider(query=settings.search_query, timespan=settings.search_timespan, policy=policy)
    if provider == "newsapi":
        if not settings.newsapi_key:
            raise ValueError("SEARCH_PROVIDER=newsapi requires NEWSAPI_KEY")
        return NewsAPISearchProvider(api_key=settings.newsapi_key, query=settings.search_query, policy=policy)
    if provider == "serpapi":
        if not settings.serpapi_key:
            raise ValueError("SEARCH_PROVIDER=serpapi requires SERPAPI_KEY")
        return SerpAPISearchProvider(api_key=settings.serpapi_key, query=settings.search_query, policy=policy)
    raise ValueError(f"Unknown SEARCH_PROVIDER: {settings.search_provider!r}")


def format_sources(results: list[SearchResult]) -> str:
    """Render search results as the numbered source block the prompt expects."""
    lines: list[str] = []
    for i, r in enumerate(results, start=1):
        meta = ", ".join(p for p in (r.source, r.published) if p)
        header = f"[{i}] {r.title}" + (f" ({meta})" if meta else "")
        lines.append(header)
        if r.snippet:
            lines.append(f"    {r.snippet.strip()}")
        if r.url:
            lines.append(f"    {r.url}")
    return "\n".join(lines)
