import httpx
import pytest

from app.retry import RetryPolicy, is_retryable_httpx, retry_async

_FAST = RetryPolicy(attempts=3, base_delay=0.0, max_delay=0.0, jitter=0.0)


async def test_retries_then_succeeds():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = await retry_async(fn, policy=RetryPolicy(attempts=5, base_delay=0.0, jitter=0.0), should_retry=lambda e: True)
    assert result == "ok"
    assert calls["n"] == 3


async def test_raises_after_exhaustion():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ValueError("always")

    with pytest.raises(ValueError, match="always"):
        await retry_async(fn, policy=_FAST, should_retry=lambda e: True)
    assert calls["n"] == 3  # attempts exhausted


async def test_does_not_retry_non_retryable():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise ValueError("client error")

    with pytest.raises(ValueError):
        await retry_async(fn, policy=_FAST, should_retry=lambda e: False)
    assert calls["n"] == 1  # not retried


def test_is_retryable_httpx():
    req = httpx.Request("GET", "http://x")
    assert is_retryable_httpx(httpx.ConnectError("boom")) is True
    assert is_retryable_httpx(httpx.HTTPStatusError("e", request=req, response=httpx.Response(503, request=req))) is True
    assert is_retryable_httpx(httpx.HTTPStatusError("e", request=req, response=httpx.Response(429, request=req))) is True
    assert is_retryable_httpx(httpx.HTTPStatusError("e", request=req, response=httpx.Response(404, request=req))) is False
    assert is_retryable_httpx(ValueError("nope")) is False
