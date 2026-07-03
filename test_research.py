import pytest

from app.research import ResearchError, ResearchService
from app.search import SearchResult


class FakeSearchProvider:
    def __init__(self, results):
        self._results = results

    async def search(self, market, max_results):
        return self._results[:max_results]


class FakeLLMClient:
    """Returns canned JSON dicts in sequence, one per complete_json call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def complete_json(self, *, system, user, model, temperature, max_tokens):
        self.calls.append(user)
        return self._responses.pop(0)


class FakeStore:
    def __init__(self, existing=None):
        self.existing = list(existing or [])
        self.inserted = []
        self.run_research = {}

    async def get_existing_pain_points(self):
        return list(self.existing)

    async def insert_pain_point(self, run_id, text, source_insight, source_url):
        self.inserted.append((run_id, text, source_insight, source_url))

    async def set_run_research(self, run_id, novelty, region):
        self.run_research[run_id] = (novelty, region)


def _results():
    return [SearchResult(title="Cold chain failures in rural clinics", url="https://example.org/a")]


def _service(llm, store, **overrides):
    kwargs = dict(
        search_provider=FakeSearchProvider(_results()),
        llm_client=llm,
        store=store,
        model="claude-haiku-4-5",
        temperature=0.3,
        max_tokens=1024,
        novelty_threshold=0.70,
        max_retries=5,
        max_sources=8,
    )
    kwargs.update(overrides)
    return ResearchService(**kwargs)


async def test_accepts_unique_pain_point_and_persists():
    llm = FakeLLMClient(
        [
            {
                "pain_point": "Point-of-care hemoglobin tests are unavailable in rural maternal clinics",
                "source_insight": "Source [1] notes frequent cold chain failures",
                "source_url": "https://example.org/a",
                "region": "Sub-Saharan Africa",
                "novelty_self_score": 88,
            }
        ]
    )
    store = FakeStore(existing=["Vaccine spoilage from unreliable refrigeration in remote areas"])

    result = await _service(llm, store).research("RUN-1", "health & wellness")

    assert result["pain_point"].startswith("Point-of-care hemoglobin")
    assert result["region"] == "Sub-Saharan Africa"
    assert result["novelty_self_score"] == 88
    assert len(store.inserted) == 1
    assert store.run_research["RUN-1"] == (88, "Sub-Saharan Africa")


async def test_rejects_near_duplicate_then_requeries():
    existing = ["Rural clinics lack reliable cold chain storage for probiotics"]
    llm = FakeLLMClient(
        [
            # attempt 0: a near-paraphrase of the existing pain point -> rejected
            {
                "pain_point": "Probiotics spoil because rural clinics have no reliable cold chain storage",
                "source_insight": "x",
                "source_url": None,
                "region": "East Africa",
                "novelty_self_score": 20,
            },
            # attempt 1 (after re-query): a distinct pain point -> accepted
            {
                "pain_point": "Maternal anemia goes undiagnosed without point-of-care hemoglobin tests",
                "source_insight": "Source [1]",
                "source_url": None,
                "region": "East Africa",
                "novelty_self_score": 75,
            },
        ]
    )
    store = FakeStore(existing=existing)

    result = await _service(llm, store).research("RUN-2", "health & wellness")

    assert "hemoglobin" in result["pain_point"]
    assert len(llm.calls) == 2  # re-queried once
    assert "distinctly different angle" in llm.calls[1]  # regenerate nudge applied
    assert len(store.inserted) == 1


async def test_uniqueness_exhausted_raises():
    existing = ["Rural clinics lack reliable cold chain storage for probiotics"]
    dup = {
        "pain_point": "Probiotics spoil because rural clinics have no reliable cold chain storage",
        "source_insight": "x",
        "source_url": None,
        "region": "East Africa",
        "novelty_self_score": 10,
    }
    llm = FakeLLMClient([dict(dup) for _ in range(3)])
    store = FakeStore(existing=existing)

    with pytest.raises(ResearchError, match="Uniqueness exhausted"):
        await _service(llm, store, max_retries=3).research("RUN-3", "health & wellness")
    assert store.inserted == []


async def test_no_sources_raises():
    llm = FakeLLMClient([])
    store = FakeStore()
    service = _service(llm, store, search_provider=FakeSearchProvider([]))

    with pytest.raises(ResearchError, match="No source articles"):
        await service.research("RUN-4", "health & wellness")
