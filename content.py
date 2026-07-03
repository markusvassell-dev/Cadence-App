"""Phase 3 content generation + the hard uniqueness engine.

`generate(pain_point)` produces a blog post (with SEO metadata), three social posts,
and a lead-magnet landing page. Every generated piece must clear the uniqueness
engine before it's inserted into the immutable `content_registry`:

  1. SHA-256 of normalized text -> exact-duplicate check.
  2. Cosine similarity vs. prior pieces of the SAME platform -> regenerate if too close.
  3. Only on passing both does the piece get inserted (status 'pending').

Persistence is behind a `ContentStore` protocol so the engine is unit-testable with
an in-memory store (no Postgres/pgvector).
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol

from . import prompts
from .blog_voice import validate_blog_voice
from .embeddings import Embedder
from .landing import build_slug, render_lead_magnet_html
from .llm import JSONParseError, LLMClient

logger = logging.getLogger(__name__)

_SOCIAL_PLATFORMS = ("linkedin", "facebook", "instagram")

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")


class UniquenessError(RuntimeError):
    """Raised when a platform can't produce a unique piece within the retry cap."""


def normalize_for_hash(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace (handoff PROMPTS.md)."""
    t = _PUNCT_RE.sub("", text.lower())
    return _WS_RE.sub(" ", t).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize_for_hash(text).encode("utf-8")).hexdigest()


@dataclass
class ContentCandidate:
    text: str
    meta: Optional[dict] = None  # blog SEO metadata; None for socials
    content_id: Optional[int] = None


class ContentStore(Protocol):
    async def content_hash_exists(self, platform: str, content_hash: str) -> bool: ...

    async def content_max_cosine(
        self, platform: str, embedding: list[float]
    ) -> Optional[float]: ...

    async def insert_content(
        self,
        run_id: str,
        platform: str,
        content_hash: str,
        snippet: str,
        full_text: str,
        embedding: list[float],
    ) -> int: ...

    async def insert_blog_meta(self, content_id: int, meta: dict) -> None: ...

    async def insert_lead_magnet(
        self, run_id: str, slug: str, headline: str, body_html: str
    ) -> int: ...

    async def get_run_context(self, run_id: str) -> dict: ...


def _compose_social(platform: str, socials: dict) -> str:
    text = (socials.get(platform) or "").strip()
    if platform == "instagram":
        tags = socials.get("instagram_hashtags") or []
        tag_line = " ".join(
            t if str(t).startswith("#") else f"#{t}" for t in tags if str(t).strip()
        )
        if tag_line:
            text = f"{text}\n\n{tag_line}".strip()
    return text


class ContentService:
    def __init__(
        self,
        *,
        llm_client: LLMClient,
        embedder: Embedder,
        store: ContentStore,
        model: str,
        temperature: float,
        max_tokens: int,
        sim_threshold: float,
        max_retries: int,
    ) -> None:
        self._llm = llm_client
        self._embedder = embedder
        self._store = store
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._threshold = sim_threshold
        self._max_retries = max_retries

    async def _complete(self, system: str, user: str) -> dict:
        return await self._llm.complete_json(
            system=system,
            user=user,
            model=self._model,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

    async def _ensure_unique(
        self,
        run_id: str,
        platform: str,
        make_candidate: Callable[[bool], Awaitable[ContentCandidate]],
        validate: Optional[Callable[[str], list[str]]] = None,
    ) -> ContentCandidate:
        """Run a candidate through the uniqueness engine, regenerating on collision.
        An optional `validate` callback (e.g. the blog voice check) can force a
        regenerate the same way a collision does — a non-empty problem list retries."""
        for attempt in range(self._max_retries):
            cand = await make_candidate(attempt > 0)
            if not cand.text:
                logger.warning("run %s %s attempt %d: empty text", run_id, platform, attempt)
                continue

            if validate is not None:
                problems = validate(cand.text)
                if problems:
                    logger.info(
                        "run %s %s attempt %d: voice check failed (%s), regenerating",
                        run_id, platform, attempt, "; ".join(problems),
                    )
                    continue

            h = content_hash(cand.text)
            if await self._store.content_hash_exists(platform, h):
                logger.info("run %s %s attempt %d: exact duplicate, regenerating", run_id, platform, attempt)
                continue

            embedding = self._embedder.embed(cand.text)
            sim = await self._store.content_max_cosine(platform, embedding)
            if sim is not None and sim >= self._threshold:
                logger.info(
                    "run %s %s attempt %d: cosine %.3f >= %.2f, regenerating",
                    run_id, platform, attempt, sim, self._threshold,
                )
                continue

            cand.content_id = await self._store.insert_content(
                run_id, platform, h, cand.text[:280], cand.text, embedding
            )
            logger.info("run %s %s: locked content #%s into registry", run_id, platform, cand.content_id)
            return cand

        raise UniquenessError(
            f"Uniqueness exhausted for platform={platform!r} after {self._max_retries} attempts"
        )

    async def generate(self, run_id: str, pain_point: dict) -> dict:
        pp = pain_point["pain_point"]
        insight = pain_point.get("source_insight") or ""
        region = pain_point.get("region") or "the target region"

        # 1. Lead-magnet copy first — its guide title is the CTA the blog/socials promote.
        lm = await self._complete(
            prompts.LEAD_MAGNET_SYSTEM,
            prompts.LEAD_MAGNET_USER.substitute(pain_point=pp, region=region),
        )
        guide_title = (lm.get("guide_title") or "Free Guide").strip()
        slug = build_slug(lm.get("slug") or guide_title, run_id)
        body_html = render_lead_magnet_html(lm, slug, run_id)
        await self._store.insert_lead_magnet(run_id, slug, lm.get("headline", ""), body_html)

        # 2. Blog (uniqueness- and voice-guarded), plus its SEO metadata.
        async def blog_candidate(harder: bool) -> ContentCandidate:
            user = prompts.BLOG_USER.substitute(
                pain_point=pp, source_insight=insight, region=region, lead_magnet_title=guide_title
            )
            if harder:
                user += prompts.REGENERATE_SUFFIX  # uniqueness nudge
                # voice nudge; the specific problems aren't threaded down, but the
                # base prompt already states the rules, so a generic reminder suffices.
                user += prompts.BLOG_VOICE_REGENERATE_SUFFIX.replace(
                    "$problems", "em-dashes and/or banned AI phrases"
                )
            data = await self._complete(prompts.BLOG_SYSTEM, user)
            meta = {
                "meta_title": data.get("meta_title", ""),
                "meta_desc": data.get("meta_description", ""),
                "headers": data.get("headers") or [],
                "internal_link_suggestions": data.get("internal_link_suggestions") or [],
                "word_count": data.get("word_count"),
                "cta": data.get("cta", ""),
            }
            return ContentCandidate(text=(data.get("body_markdown") or "").strip(), meta=meta)

        blog = await self._ensure_unique(run_id, "blog", blog_candidate, validate=validate_blog_voice)
        await self._store.insert_blog_meta(blog.content_id, blog.meta or {})

        # 3. Social posts (generated as a batch, uniqueness-guarded per platform).
        socials_state: dict = {"data": await self._gen_socials(pp, insight, guide_title, harder=False)}
        content_texts = {"blog": blog.text}
        content_ids = {"blog": blog.content_id}

        for platform in _SOCIAL_PLATFORMS:
            async def social_candidate(harder: bool, platform=platform) -> ContentCandidate:
                if harder:
                    socials_state["data"] = await self._gen_socials(pp, insight, guide_title, harder=True)
                return ContentCandidate(text=_compose_social(platform, socials_state["data"]))

            res = await self._ensure_unique(run_id, platform, social_candidate)
            content_texts[platform] = res.text
            content_ids[platform] = res.content_id

        return {
            "content": content_texts,
            "content_ids": content_ids,
            "blog_meta": blog.meta,
            "lead_magnet": {
                "slug": slug,
                "headline": lm.get("headline", ""),
                "guide_title": guide_title,
                "url": f"/lead-magnet/{slug}",
            },
        }

    async def _gen_socials(self, pp: str, insight: str, guide_title: str, *, harder: bool) -> dict:
        user = prompts.SOCIAL_USER.substitute(
            pain_point=pp, source_insight=insight, lead_magnet_title=guide_title
        )
        if harder:
            user += prompts.REGENERATE_SUFFIX
        try:
            return await self._complete(prompts.SOCIAL_SYSTEM, user)
        except JSONParseError as exc:
            logger.warning("social generation parse failure: %s", exc)
            return {}

    async def regenerate_piece(self, run_id: str, platform: str, reason: str) -> int:
        """Regenerate ONE piece of a run with a reviewer's reason threaded into the
        prompt, re-run through the uniqueness engine (and the blog voice check for
        blogs). Inserts a new 'pending' registry row and returns its id; the caller
        marks the old row 'superseded'."""
        ctx = await self._store.get_run_context(run_id)
        pp = ctx["pain_point"]
        insight = ctx.get("source_insight") or ""
        region = ctx.get("region") or "the target region"
        guide_title = ctx.get("guide_title") or "Free Guide"
        reason = (reason or "").strip()

        def _with_reason(user: str) -> str:
            user += prompts.REGENERATE_SUFFIX  # keep the uniqueness nudge
            if reason:
                user += prompts.REGENERATE_WITH_REASON.substitute(reason=reason)
            return user

        if platform == "blog":
            async def cand(_harder: bool) -> ContentCandidate:
                user = _with_reason(
                    prompts.BLOG_USER.substitute(
                        pain_point=pp, source_insight=insight, region=region,
                        lead_magnet_title=guide_title,
                    )
                )
                data = await self._complete(prompts.BLOG_SYSTEM, user)
                meta = {
                    "meta_title": data.get("meta_title", ""),
                    "meta_desc": data.get("meta_description", ""),
                    "headers": data.get("headers") or [],
                    "internal_link_suggestions": data.get("internal_link_suggestions") or [],
                    "word_count": data.get("word_count"),
                    "cta": data.get("cta", ""),
                }
                return ContentCandidate(text=(data.get("body_markdown") or "").strip(), meta=meta)

            res = await self._ensure_unique(run_id, "blog", cand, validate=validate_blog_voice)
            await self._store.insert_blog_meta(res.content_id, res.meta or {})
            return res.content_id

        # social platform
        async def cand(_harder: bool) -> ContentCandidate:
            data = await self._complete(
                prompts.SOCIAL_SYSTEM,
                _with_reason(
                    prompts.SOCIAL_USER.substitute(
                        pain_point=pp, source_insight=insight, lead_magnet_title=guide_title
                    )
                ),
            )
            return ContentCandidate(text=_compose_social(platform, data))

        res = await self._ensure_unique(run_id, platform, cand)
        return res.content_id
