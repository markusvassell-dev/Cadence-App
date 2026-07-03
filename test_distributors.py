import json

import httpx
import pytest

from app import distributors
from app.config import Settings
from app.distributors import (
    CompositeDistributor,
    DistributorError,
    InstagramDistributor,
    StubDistributor,
    WordPressDistributor,
    build_distributor,
)


async def test_build_distributor_defaults_to_stub_routing():
    dist = build_distributor(Settings())
    assert isinstance(dist, CompositeDistributor)
    # stub fallback returns the sentinel string and never raises
    assert await dist.post_to_blog("hi") == "logged"
    assert await dist.post_to_linkedin("hi") == "logged"


async def test_build_distributor_routes_blog_to_wordpress_when_configured():
    s = Settings(wp_base_url="https://blog.example.com", wp_username="u", wp_app_password="p")
    dist = build_distributor(s)
    # blog now routes to WordPress; the other platforms remain stubs
    assert "WordPressDistributor" in repr(dist._blog.__self__)
    assert dist._linkedin.__self__.__class__ is StubDistributor


async def test_wordpress_distributor_posts_via_rest(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content.decode()
        return httpx.Response(201, json={"id": 7, "link": "https://blog.example.com/?p=7"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(distributors.httpx, "AsyncClient", patched_client)

    wp = WordPressDistributor("https://blog.example.com", "user", "app-pass")
    link = await wp.post("# My Title\n\nBody paragraph.")

    assert link == "https://blog.example.com/?p=7"
    assert captured["url"].endswith("/wp-json/wp/v2/posts")
    assert captured["method"] == "POST"
    assert captured["auth"].startswith("Basic ")  # application-password basic auth
    sent = json.loads(captured["body"])
    assert sent["title"] == "My Title"  # derived from the markdown H1
    assert sent["status"] == "publish"
    assert sent["content"].startswith("# My Title")


async def test_instagram_distributor_rejects_text_only():
    ig = InstagramDistributor("biz", "token")
    with pytest.raises(DistributorError, match="image/media container"):
        await ig.post("just text")
