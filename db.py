import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from .config import get_settings

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> None:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_settings().database_url)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() first")
    return _pool


def _generate_run_id() -> str:
    return f"RUN-{uuid.uuid4().hex[:8].upper()}"


async def create_run(market: str, dry_run: bool = False) -> str:
    """Insert a new `runs` row with status='running' and return its id."""
    run_id = _generate_run_id()
    async with _get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO runs (id, market, status, dry_run, triggered_at)
            VALUES ($1, $2, 'running', $3, $4)
            """,
            run_id,
            market,
            dry_run,
            datetime.now(timezone.utc),
        )
    logger.info("created run %s (market=%r, dry_run=%s)", run_id, market, dry_run)
    return run_id


async def update_run_status(run_id: str, status: str, error: str | None = None) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE runs
            SET status = $2, error = $3, finished_at = now()
            WHERE id = $1
            """,
            run_id,
            status,
            error,
        )
    logger.info("run %s -> status=%s%s", run_id, status, f" error={error!r}" if error else "")


# ---------------------------------------------------------------------------
# Phase 2: pain points + research metadata on the run.
# ---------------------------------------------------------------------------
async def set_run_research(run_id: str, novelty: int | None, region: str | None) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE runs SET novelty = $2, region = $3 WHERE id = $1",
            run_id,
            novelty,
            region,
        )


async def insert_pain_point(
    run_id: str, text: str, source_insight: str, source_url: str | None
) -> int:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO pain_points (run_id, text, source_insight, source_url)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            run_id,
            text,
            source_insight,
            source_url,
        )
    return row["id"]


async def get_existing_pain_points() -> list[str]:
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT text FROM pain_points")
    return [r["text"] for r in rows]


class DbPainPointStore:
    """Postgres-backed `PainPointStore` used by the live research service."""

    async def get_existing_pain_points(self) -> list[str]:
        return await get_existing_pain_points()

    async def insert_pain_point(
        self, run_id: str, text: str, source_insight: str, source_url: str | None
    ) -> None:
        await insert_pain_point(run_id, text, source_insight, source_url)

    async def set_run_research(
        self, run_id: str, novelty: int | None, region: str | None
    ) -> None:
        await set_run_research(run_id, novelty, region)


# ---------------------------------------------------------------------------
# Phase 3: content registry (pgvector), blog metadata, lead magnets.
# ---------------------------------------------------------------------------
def _vector_literal(embedding: list[float]) -> str:
    """pgvector text input form: '[0.1,0.2,...]'. We pass this as a $N::vector
    param so asyncpg needs no custom codec."""
    return "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"


async def content_hash_exists(platform: str, content_hash: str) -> bool:
    # Superseded (rejected/regenerated) rows are excluded so a replaced draft never
    # blocks its own regeneration or a future run.
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM content_registry "
            "WHERE platform = $1 AND content_hash = $2 AND status <> 'superseded' LIMIT 1",
            platform,
            content_hash,
        )
    return row is not None


async def content_max_cosine(platform: str, embedding: list[float]) -> float | None:
    """Highest cosine similarity to any existing piece of the same platform, or
    None if there are none. Uses pgvector's `<=>` cosine-distance operator (the
    ivfflat index uses vector_cosine_ops)."""
    vec = _vector_literal(embedding)
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 - (embedding <=> $1::vector) AS sim
            FROM content_registry
            WHERE platform = $2 AND embedding IS NOT NULL AND status <> 'superseded'
            ORDER BY embedding <=> $1::vector
            LIMIT 1
            """,
            vec,
            platform,
        )
    return float(row["sim"]) if row else None


async def insert_content(
    run_id: str,
    platform: str,
    content_hash: str,
    snippet: str,
    full_text: str,
    embedding: list[float],
) -> int:
    vec = _vector_literal(embedding)
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO content_registry
                (run_id, platform, content_hash, snippet, full_text, embedding, status)
            VALUES ($1, $2, $3, $4, $5, $6::vector, 'pending')
            RETURNING id
            """,
            run_id,
            platform,
            content_hash,
            snippet,
            full_text,
            vec,
        )
    return row["id"]


async def insert_blog_meta(content_id: int, meta: dict) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO blog_meta
                (content_id, meta_title, meta_desc, headers, internal_link_suggestions, word_count)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
            """,
            content_id,
            meta.get("meta_title", ""),
            meta.get("meta_desc", ""),
            json.dumps(meta.get("headers") or []),
            json.dumps(meta.get("internal_link_suggestions") or []),
            meta.get("word_count"),
        )


async def insert_lead_magnet(run_id: str, slug: str, headline: str, body_html: str) -> int:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO lead_magnets (run_id, slug, headline, body_html)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            run_id,
            slug,
            headline,
            body_html,
        )
    return row["id"]


async def get_lead_magnet_html(slug: str) -> str | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT body_html FROM lead_magnets WHERE slug = $1", slug)
    return row["body_html"] if row else None


class DbContentStore:
    """Postgres-backed `ContentStore` used by the live content service."""

    async def content_hash_exists(self, platform: str, content_hash: str) -> bool:
        return await content_hash_exists(platform, content_hash)

    async def content_max_cosine(self, platform: str, embedding: list[float]) -> float | None:
        return await content_max_cosine(platform, embedding)

    async def insert_content(
        self,
        run_id: str,
        platform: str,
        content_hash: str,
        snippet: str,
        full_text: str,
        embedding: list[float],
    ) -> int:
        return await insert_content(run_id, platform, content_hash, snippet, full_text, embedding)

    async def insert_blog_meta(self, content_id: int, meta: dict) -> None:
        await insert_blog_meta(content_id, meta)

    async def insert_lead_magnet(
        self, run_id: str, slug: str, headline: str, body_html: str
    ) -> int:
        return await insert_lead_magnet(run_id, slug, headline, body_html)

    async def get_run_context(self, run_id: str) -> dict:
        return await get_run_context(run_id)


# ---------------------------------------------------------------------------
# Phase 4: leads + campaigns (3-email nurture sequences).
# ---------------------------------------------------------------------------
async def run_exists(run_id: str) -> bool:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM runs WHERE id = $1", run_id)
    return row is not None


async def get_run_pain_point(run_id: str) -> str | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT text FROM pain_points WHERE run_id = $1 ORDER BY created_at DESC LIMIT 1",
            run_id,
        )
    return row["text"] if row else None


async def get_lead_magnet_by_slug(slug: str) -> dict | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT run_id, headline FROM lead_magnets WHERE slug = $1", slug
        )
    return dict(row) if row else None


async def insert_lead(
    run_id: str | None,
    content_id: int | None,
    name: str,
    email: str,
    pain_point: str | None,
    ac_contact_id: str | None,
    sync_status: str,
) -> int:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO leads
                (run_id, content_id, name, email, pain_point, ac_contact_id, sync_status)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            run_id,
            content_id,
            name,
            email,
            pain_point,
            ac_contact_id,
            sync_status,
        )
    return row["id"]


async def list_leads(limit: int = 50) -> list[dict]:
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, run_id, content_id, name, email, pain_point,
                   ac_contact_id, sync_status, created_at
            FROM leads
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def insert_campaign(run_id: str | None, lead_id: int) -> int:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO campaigns (run_id, lead_id, status) VALUES ($1, $2, 'draft') RETURNING id",
            run_id,
            lead_id,
        )
    return row["id"]


async def insert_campaign_email(
    campaign_id: int, position: int, goal: str, timing: str, subject: str, body: str
) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO campaign_emails (campaign_id, position, goal, timing, subject, body)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            campaign_id,
            position,
            goal,
            timing,
            subject,
            body,
        )


class DbCampaignStore:
    """Postgres-backed `CampaignStore` used by the live email-sequence service."""

    async def insert_campaign(self, run_id: str | None, lead_id: int) -> int:
        return await insert_campaign(run_id, lead_id)

    async def insert_campaign_email(
        self, campaign_id: int, position: int, goal: str, timing: str, subject: str, body: str
    ) -> None:
        await insert_campaign_email(campaign_id, position, goal, timing, subject, body)


# ---------------------------------------------------------------------------
# Phase 5: pre-publish audit + distribution log.
# ---------------------------------------------------------------------------
async def get_content_row(content_id: int) -> dict | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT platform, content_hash FROM content_registry WHERE id = $1", content_id
        )
    return dict(row) if row else None


async def count_content_by_hash(platform: str, content_hash: str) -> int:
    async with _get_pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM content_registry WHERE platform = $1 AND content_hash = $2",
            platform,
            content_hash,
        )


async def insert_distribution_log(
    run_id: str,
    content_id: int | None,
    platform: str,
    result: str,
    external_url: str | None,
    detail: str | None,
) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO distribution_log
                (run_id, content_id, platform, result, external_url, detail)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            run_id,
            content_id,
            platform,
            result,
            external_url,
            detail,
        )


class DbDistributionStore:
    """Postgres-backed `DistributionStore` used by the live distribution service."""

    async def get_content(self, content_id: int) -> dict | None:
        return await get_content_row(content_id)

    async def count_by_hash(self, platform: str, content_hash: str) -> int:
        return await count_content_by_hash(platform, content_hash)

    async def log_distribution(
        self,
        run_id: str,
        content_id: int | None,
        platform: str,
        result: str,
        external_url: str | None,
        detail: str | None,
    ) -> None:
        await insert_distribution_log(run_id, content_id, platform, result, external_url, detail)


# ---------------------------------------------------------------------------
# Addendum: operator-managed markets / search focuses.
# ---------------------------------------------------------------------------
async def list_markets() -> list[dict]:
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, label, is_active, created_at FROM markets ORDER BY created_at"
        )
    return [dict(r) for r in rows]


async def get_active_market() -> str | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT label FROM markets WHERE is_active LIMIT 1")
    return row["label"] if row else None


async def add_market(label: str, make_active: bool = True) -> dict:
    """Insert a market (idempotent on label). When make_active, it becomes the
    single active market in the same transaction."""
    async with _get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO markets (label, is_active)
                VALUES ($1, false)
                ON CONFLICT (label) DO UPDATE SET label = EXCLUDED.label
                RETURNING id, label, is_active, created_at
                """,
                label,
            )
            if make_active:
                await conn.execute("UPDATE markets SET is_active = false WHERE is_active")
                await conn.execute("UPDATE markets SET is_active = true WHERE id = $1", row["id"])
                row = await conn.fetchrow(
                    "SELECT id, label, is_active, created_at FROM markets WHERE id = $1",
                    row["id"],
                )
    return dict(row)


async def set_active_market(market_id: int) -> bool:
    """Make `market_id` the sole active market. Returns False if it doesn't exist."""
    async with _get_pool().acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchrow("SELECT 1 FROM markets WHERE id = $1", market_id)
            if not exists:
                return False
            await conn.execute("UPDATE markets SET is_active = false WHERE is_active")
            await conn.execute("UPDATE markets SET is_active = true WHERE id = $1", market_id)
    return True


async def remove_market(market_id: int) -> str:
    """Delete a market. Returns 'not_found', 'last' (refused — only one left), or 'ok'.
    If the deleted market was active, the most recent remaining one is promoted."""
    async with _get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow("SELECT is_active FROM markets WHERE id = $1", market_id)
            if row is None:
                return "not_found"
            total = await conn.fetchval("SELECT count(*) FROM markets")
            if total <= 1:
                return "last"
            await conn.execute("DELETE FROM markets WHERE id = $1", market_id)
            if row["is_active"]:
                await conn.execute(
                    """
                    UPDATE markets SET is_active = true
                    WHERE id = (SELECT id FROM markets ORDER BY created_at DESC LIMIT 1)
                    """
                )
    return "ok"


async def ensure_seed_market(default_label: str) -> None:
    """Seed the table with one active default market if it's empty. Call on startup."""
    async with _get_pool().acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM markets")
        if count == 0:
            await conn.execute(
                "INSERT INTO markets (label, is_active) VALUES ($1, true)", default_label
            )


# ---------------------------------------------------------------------------
# Addendum: human approval gate (reuses runs.status + content_registry.status).
# ---------------------------------------------------------------------------
async def get_run(run_id: str) -> dict | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, market, status, dry_run FROM runs WHERE id = $1", run_id
        )
    return dict(row) if row else None


async def list_run_content(run_id: str) -> list[dict]:
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, platform, status, snippet, content_hash, locked_at
            FROM content_registry WHERE run_id = $1 ORDER BY id
            """,
            run_id,
        )
    return [dict(r) for r in rows]


async def approve_content(content_id: int) -> str:
    """'pending'/'approved' -> 'approved' (locked_at set). Returns 'not_found' or 'ok'."""
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE content_registry
               SET status = 'approved',
                   locked_at = COALESCE(locked_at, now())
             WHERE id = $1 AND status IN ('pending', 'approved')
            RETURNING id
            """,
            content_id,
        )
        if row is None:
            exists = await conn.fetchrow("SELECT 1 FROM content_registry WHERE id = $1", content_id)
            return "ok" if exists else "not_found"  # already published counts as ok
    return "ok"


async def count_unapproved_content(run_id: str) -> int:
    async with _get_pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM content_registry WHERE run_id = $1 AND status = 'pending'",
            run_id,
        )


async def get_run_generated(run_id: str) -> dict:
    """Rebuild {content: {platform: text}, content_ids: {platform: id}} for publish.
    Excludes superseded rows so a rejected draft never gets published."""
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT platform, id, full_text FROM content_registry "
            "WHERE run_id = $1 AND status <> 'superseded'",
            run_id,
        )
    content = {r["platform"]: r["full_text"] for r in rows}
    content_ids = {r["platform"]: r["id"] for r in rows}
    return {"content": content, "content_ids": content_ids}


async def mark_run_content_published(run_id: str) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE content_registry SET status = 'published' "
            "WHERE run_id = $1 AND status <> 'superseded'",
            run_id,
        )


# ---------------------------------------------------------------------------
# Addendum: reject / regenerate one piece with a reviewer reason.
# ---------------------------------------------------------------------------
async def get_content(content_id: int) -> dict | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, run_id, platform, status, snippet FROM content_registry WHERE id = $1",
            content_id,
        )
    return dict(row) if row else None


async def supersede_content(content_id: int) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE content_registry SET status = 'superseded' WHERE id = $1", content_id
        )


async def get_run_context(run_id: str) -> dict:
    """Pain point + insight + region + lead-magnet guide title, to rebuild a prompt."""
    async with _get_pool().acquire() as conn:
        pp = await conn.fetchrow(
            "SELECT text, source_insight, source_url FROM pain_points "
            "WHERE run_id = $1 ORDER BY created_at DESC LIMIT 1",
            run_id,
        )
        lm = await conn.fetchrow(
            "SELECT headline FROM lead_magnets WHERE run_id = $1 LIMIT 1", run_id
        )
    return {
        "pain_point": pp["text"] if pp else "",
        "source_insight": pp["source_insight"] if pp else "",
        "region": None,  # store region on pain_points if you want it verbatim
        "guide_title": (lm["headline"] if lm else "") or "Free Guide",
    }
