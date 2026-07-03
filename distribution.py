"""Distribution service (Phase 5): pre-publish audit, dry-run, and logging.

Before anything is posted, re-scan the content registry once more to confirm every
piece this run produced is still present and is the sole holder of its (platform,
content_hash) — a final uniqueness integrity check. Then publish: when dry_run is
set, record what *would* post (result 'dry_run') without calling any channel;
otherwise post via the Distributor and record the outcome. Every attempt is written
to distribution_log. Persistence is behind a `DistributionStore` protocol so the
logic is unit-testable without Postgres.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Protocol

from .distributors import Distributor

logger = logging.getLogger(__name__)

_PLATFORMS = ("blog", "linkedin", "facebook", "instagram")


class PublishAuditError(RuntimeError):
    pass


@dataclass
class AuditResult:
    ok: bool
    collisions: list[str]


class DistributionStore(Protocol):
    async def get_content(self, content_id: int) -> Optional[dict]: ...

    async def count_by_hash(self, platform: str, content_hash: str) -> int: ...

    async def log_distribution(
        self,
        run_id: str,
        content_id: Optional[int],
        platform: str,
        result: str,
        external_url: Optional[str],
        detail: Optional[str],
    ) -> None: ...


class DistributionService:
    def __init__(self, *, distributor: Distributor, store: DistributionStore) -> None:
        self._distributor = distributor
        self._store = store

    async def audit(self, run_id: str, content_ids: dict[str, Optional[int]]) -> AuditResult:
        """Re-scan the registry: each produced piece must exist and uniquely hold
        its (platform, content_hash)."""
        collisions: list[str] = []
        for platform, content_id in content_ids.items():
            if content_id is None:
                collisions.append(f"{platform}: no content id")
                continue
            row = await self._store.get_content(content_id)
            if row is None:
                collisions.append(f"{platform}: content #{content_id} missing from registry")
                continue
            count = await self._store.count_by_hash(row["platform"], row["content_hash"])
            if count != 1:
                collisions.append(f"{platform}: {count} registry rows share its hash")
        ok = not collisions
        logger.info("run %s pre-publish audit: %s", run_id, "ok" if ok else collisions)
        return AuditResult(ok=ok, collisions=collisions)

    async def publish(self, run_id: str, generated: dict, *, dry_run: bool) -> dict:
        content = generated.get("content", {})
        content_ids = generated.get("content_ids", {})
        results: dict[str, str] = {}

        for platform in _PLATFORMS:
            text = content.get(platform, "")
            content_id = content_ids.get(platform)

            if dry_run:
                await self._store.log_distribution(
                    run_id, content_id, platform, "dry_run", None, "dry run — not posted"
                )
                results[platform] = "dry_run"
                continue

            try:
                external_url = await self._post(platform, text)
                await self._store.log_distribution(
                    run_id, content_id, platform, "posted", external_url or None, None
                )
                results[platform] = "posted"
            except Exception as exc:  # noqa: BLE001 - logged + recorded, run continues
                logger.exception("run %s distribution to %s failed", run_id, platform)
                await self._store.log_distribution(
                    run_id, content_id, platform, "failed", None, str(exc)[:500]
                )
                results[platform] = "failed"

        return results

    async def _post(self, platform: str, text: str) -> str:
        method = getattr(self._distributor, f"post_to_{platform}")
        return await method(text)
