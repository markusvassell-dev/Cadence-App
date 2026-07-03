from app import prompts
from app.content import ContentService
from app.embeddings import HashingEmbedder, cosine_similarity

EMBEDDER = HashingEmbedder()


def _blog(body):
    return {
        "meta_title": "T", "meta_description": "D", "headers": ["H2 a"],
        "body_markdown": body, "internal_link_suggestions": [], "cta": "c",
        "word_count": len(body.split()),
    }


class FakeRegenLLM:
    def __init__(self, blogs):
        self._blogs = list(blogs)
        self.blog_prompts = []

    async def complete_json(self, *, system, user, model, temperature, max_tokens):
        assert system == prompts.BLOG_SYSTEM
        self.blog_prompts.append(user)
        return self._blogs.pop(0) if len(self._blogs) > 1 else self._blogs[0]


class FakeRegenStore:
    def __init__(self, context, seed_texts=()):
        self._context = context
        self._by_platform = {}
        self._next_id = 100
        self.blog_meta = []
        for t in seed_texts:
            from app.content import content_hash

            self._by_platform.setdefault("blog", []).append((content_hash(t), EMBEDDER.embed(t)))

    async def get_run_context(self, run_id):
        return dict(self._context)

    async def content_hash_exists(self, platform, h):
        return any(eh == h for eh, _ in self._by_platform.get(platform, []))

    async def content_max_cosine(self, platform, embedding):
        rows = self._by_platform.get(platform, [])
        return max((cosine_similarity(embedding, e) for _, e in rows), default=None)

    async def insert_content(self, run_id, platform, h, snippet, full_text, embedding):
        self._next_id += 1
        self._by_platform.setdefault(platform, []).append((h, embedding))
        return self._next_id

    async def insert_blog_meta(self, content_id, meta):
        self.blog_meta.append((content_id, meta))


def _service(llm, store):
    return ContentService(
        llm_client=llm, embedder=EMBEDDER, store=store,
        model="claude-sonnet-4-6", temperature=0.7, max_tokens=4000,
        sim_threshold=0.60, max_retries=4,
    )


async def test_regenerate_threads_reason_and_regenerates_past_near_dup():
    seeded = "Cold chain storage failures spoil temperature sensitive probiotics in remote rural clinics"
    near_dup = seeded + " today"        # high cosine -> first attempt rejected
    distinct = "Financial literacy gaps leave smallholder farmers unable to access microcredit programs"
    llm = FakeRegenLLM([_blog(near_dup), _blog(distinct)])
    store = FakeRegenStore(
        {"pain_point": "cold chain gaps", "source_insight": "src", "region": "R", "guide_title": "Guide"},
        seed_texts=[seeded],
    )

    new_id = await _service(llm, store).regenerate_piece("RUN-1", "blog", reason="Too generic, add specifics")

    assert isinstance(new_id, int)
    # the reviewer's reason was threaded into the model prompt
    assert 'Too generic, add specifics' in llm.blog_prompts[0]
    assert "A reviewer rejected the previous draft" in llm.blog_prompts[0]
    # regenerated once past the near-duplicate, and blog meta was written for the new piece
    assert len(llm.blog_prompts) == 2
    assert store.blog_meta and store.blog_meta[0][0] == new_id
