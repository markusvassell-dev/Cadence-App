"""Distribution channels (Phase 1 stub; Phase 5 real channels).

The `Distributor` interface stays the contract; `CompositeDistributor` routes each
platform to a real client when its credentials are configured, otherwise to the
console stub. Real network calls are wrapped in retry-with-exponential-backoff.

Live note: the social/WordPress APIs can't be exercised from a locked-down
sandbox (and Meta/LinkedIn need business accounts + app review — the non-code
blocker called out in the spec), so the real clients here are correct-shaped but
unverified against the live APIs; the stub is what runs in dev/tests.
"""

import logging
from typing import Awaitable, Callable, Optional, Protocol

import httpx

from .config import Settings
from .retry import RetryPolicy, is_retryable_httpx, retry_async

logger = logging.getLogger(__name__)


class DistributorError(RuntimeError):
    pass


class Distributor(Protocol):
    async def post_to_blog(self, text: str) -> str: ...

    async def post_to_linkedin(self, text: str) -> str: ...

    async def post_to_facebook(self, text: str) -> str: ...

    async def post_to_instagram(self, text: str) -> str: ...


class StubDistributor:
    """Phase 1 distributor: logs instead of posting, prefixed by platform name."""

    async def post_to_blog(self, text: str) -> str:
        logger.info("[blog] %s", text)
        return "logged"

    async def post_to_linkedin(self, text: str) -> str:
        logger.info("[linkedin] %s", text)
        return "logged"

    async def post_to_facebook(self, text: str) -> str:
        logger.info("[facebook] %s", text)
        return "logged"

    async def post_to_instagram(self, text: str) -> str:
        logger.info("[instagram] %s", text)
        return "logged"


def _title_from(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return "New post"


class WordPressDistributor:
    """WordPress REST API (publishes a post via application-password basic auth)."""

    def __init__(self, base_url: str, username: str, app_password: str, policy: RetryPolicy = RetryPolicy()):
        self._base = base_url.rstrip("/")
        self._auth = (username, app_password)
        self._policy = policy

    async def post(self, text: str) -> str:
        async def _call() -> str:
            async with httpx.AsyncClient(timeout=30, auth=self._auth) as client:
                resp = await client.post(
                    f"{self._base}/wp-json/wp/v2/posts",
                    json={"title": _title_from(text), "content": text, "status": "publish"},
                )
                resp.raise_for_status()
                body = resp.json()
                return body.get("link") or str(body.get("id", ""))

        return await retry_async(_call, policy=self._policy, should_retry=is_retryable_httpx, description="WordPress post")


class LinkedInDistributor:
    """LinkedIn UGC Posts API (text share). Requires an OAuth token + author URN."""

    def __init__(self, access_token: str, author_urn: str, policy: RetryPolicy = RetryPolicy()):
        self._token = access_token
        self._author = author_urn
        self._policy = policy

    async def post(self, text: str) -> str:
        payload = {
            "author": self._author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        }

        async def _call() -> str:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post("https://api.linkedin.com/v2/ugcPosts", json=payload, headers=headers)
                resp.raise_for_status()
                post_id = resp.json().get("id") or resp.headers.get("x-restli-id", "")
                return str(post_id)

        return await retry_async(_call, policy=self._policy, should_retry=is_retryable_httpx, description="LinkedIn post")


class FacebookDistributor:
    """Facebook Page feed (Graph API). Requires a page id + page access token."""

    def __init__(self, page_id: str, access_token: str, policy: RetryPolicy = RetryPolicy()):
        self._page_id = page_id
        self._token = access_token
        self._policy = policy

    async def post(self, text: str) -> str:
        async def _call() -> str:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"https://graph.facebook.com/v19.0/{self._page_id}/feed",
                    data={"message": text, "access_token": self._token},
                )
                resp.raise_for_status()
                post_id = resp.json().get("id", "")
                return f"https://facebook.com/{post_id}" if post_id else ""

        return await retry_async(_call, policy=self._policy, should_retry=is_retryable_httpx, description="Facebook post")


class InstagramDistributor:
    """Instagram (Graph API). Feed posts are media-based — a text-only caption
    cannot be published without an image/media container, and the API needs
    app-reviewed Business credentials. The 2-step container/publish flow is left
    for when an image pipeline exists; text-only calls raise a clear error."""

    def __init__(self, business_id: str, access_token: str, policy: RetryPolicy = RetryPolicy()):
        self._business_id = business_id
        self._token = access_token
        self._policy = policy

    async def post(self, text: str) -> str:
        raise DistributorError(
            "Instagram feed posts require an image/media container and app-reviewed "
            "Business credentials; text-only content is not supported."
        )


class CompositeDistributor:
    """Routes each platform to a configured real client or the stub fallback."""

    def __init__(
        self,
        *,
        blog: Callable[[str], Awaitable[str]],
        linkedin: Callable[[str], Awaitable[str]],
        facebook: Callable[[str], Awaitable[str]],
        instagram: Callable[[str], Awaitable[str]],
    ) -> None:
        self._blog = blog
        self._linkedin = linkedin
        self._facebook = facebook
        self._instagram = instagram

    async def post_to_blog(self, text: str) -> str:
        return await self._blog(text)

    async def post_to_linkedin(self, text: str) -> str:
        return await self._linkedin(text)

    async def post_to_facebook(self, text: str) -> str:
        return await self._facebook(text)

    async def post_to_instagram(self, text: str) -> str:
        return await self._instagram(text)


def build_distributor(settings: Settings) -> Distributor:
    """Compose a distributor: real client per platform when configured, else stub."""
    stub = StubDistributor()
    policy = settings.retry_policy()

    if settings.wp_base_url and settings.wp_username and settings.wp_app_password:
        blog = WordPressDistributor(settings.wp_base_url, settings.wp_username, settings.wp_app_password, policy).post
    else:
        blog = stub.post_to_blog

    if settings.linkedin_access_token and settings.linkedin_author_urn:
        linkedin = LinkedInDistributor(settings.linkedin_access_token, settings.linkedin_author_urn, policy).post
    else:
        linkedin = stub.post_to_linkedin

    if settings.facebook_page_id and settings.facebook_access_token:
        facebook = FacebookDistributor(settings.facebook_page_id, settings.facebook_access_token, policy).post
    else:
        facebook = stub.post_to_facebook

    if settings.instagram_business_id and settings.instagram_access_token:
        instagram = InstagramDistributor(settings.instagram_business_id, settings.instagram_access_token, policy).post
    else:
        instagram = stub.post_to_instagram

    return CompositeDistributor(blog=blog, linkedin=linkedin, facebook=facebook, instagram=instagram)
