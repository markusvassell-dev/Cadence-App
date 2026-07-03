from app.distribution import DistributionService
from app.orchestrator import run_pipeline


class RecordingDistributor:
    def __init__(self):
        self.calls = []

    async def post_to_blog(self, text):
        self.calls.append(("blog", text))
        return "http://blog/1"

    async def post_to_linkedin(self, text):
        self.calls.append(("linkedin", text))
        return "http://li/1"

    async def post_to_facebook(self, text):
        self.calls.append(("facebook", text))
        return "http://fb/1"

    async def post_to_instagram(self, text):
        self.calls.append(("instagram", text))
        return "http://ig/1"


class FakeDistributionStore:
    """Audit store where every produced piece exists and uniquely holds its hash."""

    def __init__(self):
        self.logs = []
        self._platform_by_id = {1: "blog", 2: "linkedin", 3: "facebook", 4: "instagram"}

    async def get_content(self, content_id):
        platform = self._platform_by_id.get(content_id)
        return {"platform": platform, "content_hash": f"hash-{content_id}"} if platform else None

    async def count_by_hash(self, platform, content_hash):
        return 1

    async def log_distribution(self, run_id, content_id, platform, result, external_url, detail):
        self.logs.append((platform, result))


class FakeResearchService:
    def __init__(self):
        self.calls = []

    async def research(self, run_id, market):
        self.calls.append((run_id, market))
        return {"pain_point": f"pain point for {market}", "source_insight": "e", "region": "R", "novelty_self_score": 80}


class FakeContentService:
    def __init__(self):
        self.calls = []

    async def generate(self, run_id, pain_point):
        self.calls.append((run_id, pain_point["pain_point"]))
        return {
            "content": {"blog": "blog text", "linkedin": "linkedin text", "facebook": "facebook text", "instagram": "instagram text"},
            "content_ids": {"blog": 1, "linkedin": 2, "facebook": 3, "instagram": 4},
            "lead_magnet": {"slug": "guide-test", "url": "/lead-magnet/guide-test"},
        }


async def test_run_pipeline_audits_then_distributes():
    distributor = RecordingDistributor()
    store = FakeDistributionStore()
    dist = DistributionService(distributor=distributor, store=store)

    result = await run_pipeline(
        "RUN-TEST",
        "test market",
        research_service=FakeResearchService(),
        content_service=FakeContentService(),
        distribution_service=dist,
        approval_gate=False,  # auto-publish path
    )

    assert result["generated"]["content"]["blog"] == "blog text"
    assert result["held_for_review"] is False
    assert {p for p, _ in distributor.calls} == {"blog", "linkedin", "facebook", "instagram"}
    assert dict(distributor.calls)["linkedin"] == "linkedin text"
    assert result["distribution"] == {p: "posted" for p in ("blog", "linkedin", "facebook", "instagram")}


async def test_approval_gate_holds_without_distributing():
    distributor = RecordingDistributor()
    dist = DistributionService(distributor=distributor, store=FakeDistributionStore())

    result = await run_pipeline(
        "RUN-HELD",
        "test market",
        research_service=FakeResearchService(),
        content_service=FakeContentService(),
        distribution_service=dist,
        approval_gate=True,  # default: hold for human approval
    )

    assert result["held_for_review"] is True
    assert result["distribution"] is None
    assert distributor.calls == []  # nothing posted while held


async def test_dry_run_does_not_post():
    distributor = RecordingDistributor()
    dist = DistributionService(distributor=distributor, store=FakeDistributionStore())

    result = await run_pipeline(
        "RUN-DRY",
        "test market",
        research_service=FakeResearchService(),
        content_service=FakeContentService(),
        distribution_service=dist,
        dry_run=True,
    )

    assert distributor.calls == []  # nothing posted
    assert result["distribution"] == {p: "dry_run" for p in ("blog", "linkedin", "facebook", "instagram")}
