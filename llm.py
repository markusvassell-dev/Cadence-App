"""LLM client wrapper around the Anthropic SDK.

Kept thin and behind a `Protocol` so the research service can be unit-tested with
a fake client (no network, no API key). The Anthropic SDK client is constructed
lazily on first use so the app boots without ANTHROPIC_API_KEY set.
"""

import json
import logging
import re
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class JSONParseError(ValueError):
    pass


_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def extract_json(text: str) -> dict:
    """Parse a JSON object out of an LLM response, tolerating code fences and
    surrounding prose. Raises JSONParseError if no object can be parsed."""
    cleaned = _FENCE_RE.sub("", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise JSONParseError(f"No JSON object found in response: {text[:200]!r}")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JSONParseError(f"Invalid JSON in response: {exc}") from exc


class LLMClient(Protocol):
    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict: ...


def _is_retryable_anthropic(exc: Exception) -> bool:
    """Retry transient Anthropic API failures (connection, rate limit, 5xx).
    Client errors (400/401/403/404) won't succeed on retry, so don't waste attempts."""
    name = type(exc).__name__
    if name in {"APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError"}:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status >= 500


class AnthropicLLMClient:
    """Calls the Anthropic Messages API and parses a JSON object from the reply.

    Phase 5: the API call is wrapped in retry-with-exponential-backoff (on top of
    the SDK's own retries) for transient failures."""

    def __init__(self, policy: "RetryPolicy | None" = None) -> None:
        from .retry import RetryPolicy

        self._client = None  # built lazily so import/boot doesn't require a key
        self._policy = policy or RetryPolicy()

    def _get_client(self):
        if self._client is None:
            import anthropic  # imported here to keep startup light

            self._client = anthropic.AsyncAnthropic()
        return self._client

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        model: str,
        temperature: float,
        max_tokens: int,
    ) -> dict:
        from .retry import retry_async

        client = self._get_client()

        async def _call():
            return await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
                messages=[{"role": "user", "content": user}],
            )

        resp = await retry_async(
            _call,
            policy=self._policy,
            should_retry=_is_retryable_anthropic,
            description=f"Anthropic {model}",
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return extract_json(text)
