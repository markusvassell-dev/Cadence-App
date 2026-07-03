import pytest

from app import prompts
from app.content import ContentService, UniquenessError, content_hash
from app.embeddings import HashingEmbedder, cosine_similarity

EMBEDDER = HashingEmbedder()


def _blog(body, n=1):
    return {
        "meta_title": f"Title {n}",
        "meta_description": f"Desc {n}",
        "headers": [f"H2 {n}a", f"H2 {n}b"],
        "body_markdown": body,
        "internal_link_suggestions": [{"anchor": "a", "target_topic": "t"}],
        "cta": "Download the guide",
        "word_count": len(body.split()),
    }


def _socials(tag):
    return {
        "linkedin": f"LinkedIn post about {tag}. Download the guide.",
        "facebook": f"Facebook post about {tag}. Download the guide.",
        "instagram": f"Instagram post about {tag} 🌍",
        "instagram_hashtags": ["#health", "#emergingmarkets"],
    }


_LEAD_MAGNET = {
    "headline": "Stop losing vaccines to heat",
    "subhead": "A practical field guide.",
    "bullets": ["Pick a cooler", "Log temperatures", "Triage failures"],
    "guide_title": "5 Ways to Protect Your Cold Chain",
    "slug": "protect-cold-chain",
}


class FakeContentLLM:
    """Dispatches by system prompt so call ordering/retries don't matter."""

    def __init__(self, *, lead_magnet, blogs, socials):
        self._lead_magnet = lead_magnet
        self._blogs = list(blogs)
        self._socials = list(socials)
        self.blog_prompts: list[str] = []
        self.social_prompts: list[str] = []

    @staticmethod
    def _next(queue):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def complete_json(self, *, system, user, model, temperature, max_tokens):
        if system == prompts.LEAD_MAGNET_SYSTEM:
            return self._lead_magnet
        if system == prompts.BLOG_SYSTEM:
            self.blog_prompts.append(user)
            return self._next(self._blogs)
        if system == prompts.SOCIAL_SYSTEM:
            self.social_prompts.append(user)
            return self._next(self._socials)
        raise AssertionError(f"unexpected system prompt: {system[:40]!r}")


class FakeContentStore:
    """In-memory mirror of DbContentStore (cosine done in Python)."""

    def __init__(self):
        self._by_platform: dict[str, list[tuple[str, list[float]]]] = {}
        self._next_id = 0
        self.blog_meta: list[tuple[int, dict]] = []
        self.lead_magnets: list[tuple[str, str, str]] = []

    def seed(self, platform, text):
        self._by_platform.setdefault(platform, []).append(
            (content_hash(text), EMBEDDER.embed(text))
        )

    async def content_hash_exists(self, platform, h):
        return any(eh == h for eh, _ in self._by_platform.get(platform, []))

    async def content_max_cosine(self, platform, embedding):
        rows = self._by_platform.get(platform, [])
        if not rows:
            return None
        return max(cosine_similarity(embedding, emb) for _, emb in rows)

    async def insert_content(self, run_id, platform, h, snippet, full_text, embedding):
        self._next_id += 1
        self._by_platform.setdefault(platform, []).append((h, embedding))
        return self._next_id

    async def insert_blog_meta(self, content_id, meta):
        self.blog_meta.append((content_id, meta))

    async def insert_lead_magnet(self, run_id, slug, headline, body_html):
        self.lead_magnets.append((slug, headline, body_html))
        return len(self.lead_magnets)


def _service(llm, store, *, threshold=0.60, retries=4):
    return ContentService(
        llm_client=llm,
        embedder=EMBEDDER,
        store=store,
        model="claude-sonnet-4-6",
        temperature=0.7,
        max_tokens=4000,
        sim_threshold=threshold,
        max_retries=retries,
    )


async def test_happy_path_generates_all_pieces():
    llm = FakeContentLLM(
        lead_magnet=_LEAD_MAGNET,
        blogs=[_blog("A thorough blog about cold chain logistics in rural clinics.")],
        socials=[_socials("cold chain")],
    )
    store = FakeContentStore()

    result = await _service(llm, store).generate("RUN-AB12", {"pain_point": "cold chain gaps", "source_insight": "src", "region": "East Africa"})

    assert set(result["content"]) == {"blog", "linkedin", "facebook", "instagram"}
    assert "#health" in result["content"]["instagram"]  # hashtags appended
    assert all(isinstance(cid, int) for cid in result["content_ids"].values())
    assert len(store.blog_meta) == 1
    assert store.blog_meta[0][1]["meta_title"] == "Title 1"
    assert result["lead_magnet"]["url"] == f"/lead-magnet/{result['lead_magnet']['slug']}"
    assert result["lead_magnet"]["slug"].endswith("-ab12")  # run-suffixed slug
    assert len(store.lead_magnets) == 1


async def test_voice_failing_blog_regenerates_until_clean():
    # First draft breaks the em-dash rule (4 em-dashes); second draft is clean.
    failing = "Cold chain failures — really — spoil probiotics — across rural clinics — every week."
    clean = "Cold chain failures spoil probiotics across rural clinics almost every single week."
    llm = FakeContentLLM(
        lead_magnet=_LEAD_MAGNET,
        blogs=[_blog(failing, 1), _blog(clean, 2)],
        socials=[_socials("cold chain")],
    )
    store = FakeContentStore()

    result = await _service(llm, store).generate("RUN-V", {"pain_point": "p", "source_insight": "s", "region": "r"})

    # the clean draft (not the em-dash-heavy one) is what gets locked in
    assert result["content"]["blog"] == clean
    assert len(llm.blog_prompts) == 2  # regenerated once past the voice failure


async def test_exact_duplicate_blog_triggers_regeneration():
    v1 = "Cold chain failures spoil probiotics across rural clinics in the region."
    v2 = "Maternal anemia goes undiagnosed without point-of-care hemoglobin testing."
    llm = FakeContentLLM(lead_magnet=_LEAD_MAGNET, blogs=[_blog(v1, 1), _blog(v2, 2)], socials=[_socials("anemia")])
    store = FakeContentStore()
    store.seed("blog", v1)  # exact hash already in registry

    result = await _service(llm, store).generate("RUN-1", {"pain_point": "p", "source_insight": "s", "region": "r"})

    assert result["content"]["blog"] == v2
    assert len(llm.blog_prompts) == 2
    assert prompts.REGENERATE_SUFFIX in llm.blog_prompts[1]


async def test_fuzzy_duplicate_blog_triggers_regeneration():
    seeded = "Cold chain storage failures spoil temperature sensitive probiotics in remote rural clinics"
    near_dup = seeded + " today"  # different hash, very high cosine
    distinct = "Financial literacy gaps leave smallholder farmers unable to access microcredit programs"
    llm = FakeContentLLM(lead_magnet=_LEAD_MAGNET, blogs=[_blog(near_dup, 1), _blog(distinct, 2)], socials=[_socials("microcredit")])
    store = FakeContentStore()
    store.seed("blog", seeded)

    # sanity: the near-dup really is over threshold, the distinct one isn't
    assert cosine_similarity(EMBEDDER.embed(near_dup), EMBEDDER.embed(seeded)) >= 0.60
    assert cosine_similarity(EMBEDDER.embed(distinct), EMBEDDER.embed(seeded)) < 0.60

    result = await _service(llm, store).generate("RUN-2", {"pain_point": "p", "source_insight": "s", "region": "r"})

    assert result["content"]["blog"] == distinct
    assert len(llm.blog_prompts) == 2


async def test_uniqueness_exhausted_raises():
    dup = "Cold chain failures spoil probiotics across rural clinics in the region."
    llm = FakeContentLLM(lead_magnet=_LEAD_MAGNET, blogs=[_blog(dup)], socials=[_socials("x")])
    store = FakeContentStore()
    store.seed("blog", dup)

    with pytest.raises(UniquenessError, match="platform='blog'"):
        await _service(llm, store, retries=3).generate("RUN-3", {"pain_point": "p", "source_insight": "s", "region": "r"})

    # the lead magnet is generated before the blog, so it still persisted
    assert len(store.lead_magnets) == 1
