import pytest

from app.distribution import DistributionService

_ALL = ("blog", "linkedin", "facebook", "instagram")


class RecordingDistributor:
    def __init__(self, fail_on=()):
        self.calls = []
        self._fail_on = set(fail_on)

    async def _post(self, platform, text):
        self.calls.append(platform)
        if platform in self._fail_on:
            raise RuntimeError(f"{platform} boom")
        return f"http://{platform}/1"

    async def post_to_blog(self, text):
        return await self._post("blog", text)

    async def post_to_linkedin(self, text):
        return await self._post("linkedin", text)

    async def post_to_facebook(self, text):
        return await self._post("facebook", text)

    async def post_to_instagram(self, text):
        return await self._post("instagram", text)


class FakeStore:
    def __init__(self, content=None, counts=None):
        self.content = content or {}
        self.counts = counts or {}
        self.logs = []

    async def get_content(self, content_id):
        return self.content.get(content_id)

    async def count_by_hash(self, platform, content_hash):
        return self.counts.get((platform, content_hash), 1)

    async def log_distribution(self, run_id, content_id, platform, result, external_url, detail):
        self.logs.append((platform, result, external_url, detail))


def _generated():
    return {
        "content": {p: f"{p} text" for p in _ALL},
        "content_ids": {"blog": 1, "linkedin": 2, "facebook": 3, "instagram": 4},
    }


def _store_all_present():
    content = {i: {"platform": p, "content_hash": f"h{i}"} for i, p in enumerate(_ALL, start=1)}
    return FakeStore(content=content)


async def test_audit_ok_when_all_present_and_unique():
    result = await DistributionService(distributor=RecordingDistributor(), store=_store_all_present()).audit(
        "RUN-1", {"blog": 1, "linkedin": 2, "facebook": 3, "instagram": 4}
    )
    assert result.ok is True
    assert result.collisions == []


async def test_audit_flags_missing_none_and_duplicate():
    store = FakeStore(
        content={1: {"platform": "blog", "content_hash": "h1"}},
        counts={("blog", "h1"): 2},  # duplicate hash
    )
    result = await DistributionService(distributor=RecordingDistributor(), store=store).audit(
        "RUN-1", {"blog": 1, "linkedin": None, "facebook": 99}
    )
    assert result.ok is False
    joined = " ".join(result.collisions)
    assert "blog" in joined and "2 registry rows" in joined  # duplicate
    assert "linkedin: no content id" in joined  # None
    assert "facebook" in joined and "missing" in joined  # not found


async def test_publish_dry_run_does_not_post():
    dist = RecordingDistributor()
    store = _store_all_present()
    results = await DistributionService(distributor=dist, store=store).publish("RUN-1", _generated(), dry_run=True)

    assert dist.calls == []
    assert results == {p: "dry_run" for p in _ALL}
    assert all(r == "dry_run" for _, r, _, _ in store.logs)


async def test_publish_posts_and_logs_external_url():
    dist = RecordingDistributor()
    store = _store_all_present()
    results = await DistributionService(distributor=dist, store=store).publish("RUN-1", _generated(), dry_run=False)

    assert set(dist.calls) == set(_ALL)
    assert results == {p: "posted" for p in _ALL}
    logged = {p: (r, url) for p, r, url, _ in store.logs}
    assert logged["blog"] == ("posted", "http://blog/1")


async def test_publish_records_per_platform_failure_and_continues():
    dist = RecordingDistributor(fail_on={"facebook"})
    store = _store_all_present()
    results = await DistributionService(distributor=dist, store=store).publish("RUN-1", _generated(), dry_run=False)

    assert results["facebook"] == "failed"
    assert results["blog"] == "posted" and results["instagram"] == "posted"
    failed = [(p, detail) for p, r, _, detail in store.logs if r == "failed"]
    assert failed and failed[0][0] == "facebook" and "boom" in failed[0][1]
