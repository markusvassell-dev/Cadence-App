"""Retry-with-exponential-backoff for external calls (Phase 5).

The handoff spec wants every external call (search, Claude, ActiveCampaign,
distribution) wrapped so transient failures don't fail a whole run. Non-retryable
errors (4xx other than 429) are raised immediately so we don't burn attempts on
requests that will never succeed.
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 16.0
    jitter: float = 0.1


def is_retryable_httpx(exc: Exception) -> bool:
    """Network/timeout errors and 429/5xx responses are worth retrying."""
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    should_retry: Callable[[Exception], bool],
    description: str = "external call",
) -> T:
    """Call `fn`, retrying with exponential backoff while `should_retry(exc)` and
    attempts remain. Re-raises the last exception once exhausted."""
    for attempt in range(1, policy.attempts + 1):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below if not retryable
            if attempt >= policy.attempts or not should_retry(exc):
                raise
            delay = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, policy.jitter)
            logger.warning(
                "%s failed (attempt %d/%d): %s — retrying in %.2fs",
                description,
                attempt,
                policy.attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
    # Unreachable: the loop either returns or raises.
    raise RuntimeError("retry_async exhausted without raising")
