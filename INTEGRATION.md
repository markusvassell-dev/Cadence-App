# INTEGRATION.md — edits to existing files

Drop-in files (`markets_router.py` → `app/routers/markets.py`, `blog_voice.py` →
`app/blog_voice.py`) are ready as-is. This file covers the edits to files you already
have. Code matches your existing style (asyncpg, module fns + optional store class,
`str | None` hints, `logging`).

---

## §1 — `app/db.py`: market functions

Append to `db.py` (after the Phase-2 pain-point section is a natural home). These are
called directly by `routers/markets.py`, by `routers/hooks.py` (active market), and by
startup seeding.

```python
# ---------------------------------------------------------------------------
# Addendum: operator-managed markets / search focuses.
# ---------------------------------------------------------------------------
async def list_markets() -> list[dict]:
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, label, is_active, created_at FROM markets ORDER BY created_at"
        )
    return [dict(r) for r in rows]


async def get_active_market() -> str | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT label FROM markets WHERE is_active LIMIT 1")
    return row["label"] if row else None


async def add_market(label: str, make_active: bool = True) -> dict:
    """Insert a market (idempotent on label). When make_active, it becomes the
    single active market in the same transaction."""
    async with _get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO markets (label, is_active)
                VALUES ($1, false)
                ON CONFLICT (label) DO UPDATE SET label = EXCLUDED.label
                RETURNING id, label, is_active, created_at
                """,
                label,
            )
            if make_active:
                await conn.execute("UPDATE markets SET is_active = false WHERE is_active")
                await conn.execute("UPDATE markets SET is_active = true WHERE id = $1", row["id"])
                row = await conn.fetchrow(
                    "SELECT id, label, is_active, created_at FROM markets WHERE id = $1",
                    row["id"],
                )
    return dict(row)


async def set_active_market(market_id: int) -> bool:
    """Make `market_id` the sole active market. Returns False if it doesn't exist."""
    async with _get_pool().acquire() as conn:
        async with conn.transaction():
            exists = await conn.fetchrow("SELECT 1 FROM markets WHERE id = $1", market_id)
            if not exists:
                return False
            await conn.execute("UPDATE markets SET is_active = false WHERE is_active")
            await conn.execute("UPDATE markets SET is_active = true WHERE id = $1", market_id)
    return True


async def remove_market(market_id: int) -> str:
    """Delete a market. Returns 'not_found', 'last' (refused — only one left), or 'ok'.
    If the deleted market was active, the most recent remaining one is promoted."""
    async with _get_pool().acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT is_active FROM markets WHERE id = $1", market_id
            )
            if row is None:
                return "not_found"
            total = await conn.fetchval("SELECT count(*) FROM markets")
            if total <= 1:
                return "last"
            await conn.execute("DELETE FROM markets WHERE id = $1", market_id)
            if row["is_active"]:
                await conn.execute(
                    """
                    UPDATE markets SET is_active = true
                    WHERE id = (SELECT id FROM markets ORDER BY created_at DESC LIMIT 1)
                    """
                )
    return "ok"


async def ensure_seed_market(default_label: str) -> None:
    """Seed the table with one active default market if it's empty. Call on startup."""
    async with _get_pool().acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM markets")
        if count == 0:
            await conn.execute(
                "INSERT INTO markets (label, is_active) VALUES ($1, true)", default_label
            )
```

No `Protocol`/`Db*Store` class is needed here — markets are read/written directly by
the router and the hook, not by a service that needs a fake in tests (the tests hit the
real functions against a test DB, same as `test_leads_db.py`).

---

## §2 — `app/main.py`: register router + seed on startup

```python
# add markets to the import
from .routers import hooks, lead_magnets, leads, markets

# inside the lifespan(), after init_pool():
async def lifespan(app: FastAPI):
    await init_pool()
    await db.ensure_seed_market(get_settings().default_market)   # NEW
    yield
    await close_pool()

# with the other include_router calls:
app.include_router(markets.router)                                # NEW
```

Add the imports `from .db import ... ` already present; you'll also need
`from .config import get_settings` and `from . import db` at the top of `main.py`
(only `close_pool, init_pool` are imported today).

---

## §3 — `app/routers/hooks.py`: default to the active market

In `trigger_run`, replace the single `market = ...` line:

```python
# BEFORE
market = (payload.market if payload and payload.market else None) or get_settings().default_market

# AFTER
market = (
    (payload.market if payload and payload.market else None)
    or await db.get_active_market()
    or get_settings().default_market
)
```

`db` is already imported in `hooks.py`. An explicit `market` in the POST body still
wins (Karbon can override per call); otherwise the operator's active selection is used.

---

## §4 — `app/prompts.py`: human-voice blog prompt

Replace the existing `BLOG_SYSTEM` and `BLOG_USER` with the versions below, and add the
voice regenerate suffix. Everything else in `prompts.py` is unchanged.

```python
BLOG_SYSTEM = (
    "You are a thoughtful, warm blogger writing for a general audience, on behalf of a\n"
    "health & wellness brand serving emerging markets. You write specific, credible,\n"
    "non-generic long-form content that reads as if a real person wrote it, not a robot\n"
    "or a corporate AI. You never fabricate statistics; when you reference data, you\n"
    "attribute it to the provided source insight."
)

BLOG_USER = Template(
    """Topic / primary keyword (pain point): $pain_point
Supporting evidence: $source_insight
Region: $region
Lead magnet to promote: "$lead_magnet_title"

Write a long-form blog post (800-1200 words; aim past 1000) targeting the pain point
as the primary keyword. It must sound like an empathetic, knowledgeable human wrote it.

VOICE & STYLE (follow strictly):
- Conversational but not sloppy. Use contractions (it's, you're, we'll) where natural.
- Address the reader directly as "you." Make them feel spoken to, not lectured.
- Mix short and medium sentences for natural rhythm. Avoid long academic run-ons.
- One or two brief, relatable analogies are fine. No fabricated life stories.
- Plain, concrete language. Explain any technical term immediately; avoid jargon.
- SEVERELY limit em-dashes: no more than 2 in the entire post. Rewrite clauses with
  commas, periods, or parentheses instead.
- No hollow AI phrases ("navigating the landscape", "it's important to note"). Never
  use the phrase "delve into".
- No emojis unless the topic truly demands it.
- Short paragraphs, usually 2-4 sentences.

STRUCTURE:
1. Headline: clear, benefit-driven, answers a question. No clickbait, no ALL CAPS.
2. Intro (1-2 short paragraphs): hook with a relatable problem, question, or fact;
   say why it matters and what the reader gains.
3. Body (3-5 scannable sections): casual but descriptive subheadings, one idea each,
   at most one short bullet list per section.
4. Conclusion: summarize the takeaway in a fresh way (don't just repeat); end with a
   gentle, open-ended question or one small action. No pushy calls-to-action.

SEO REQUIREMENTS (in addition to the above):
- meta title <= 60 chars; meta description <= 155 chars
- 4-6 H2 section headers, keyword-aware
- 2-3 internal-link SUGGESTIONS (anchor text + topic) — do not invent URLs
- One lead-magnet call-to-action near the end promoting the guide above, kept gentle
  and in the same human voice

Return ONLY this JSON:
{
  "meta_title": "...",
  "meta_description": "...",
  "headers": ["...", "..."],
  "body_markdown": "...full post in markdown...",
  "internal_link_suggestions": [{"anchor": "...", "target_topic": "..."}],
  "cta": "...",
  "word_count": <int>
}"""
)

# Appended on a regenerate when the deterministic voice check fails, telling the
# model exactly what to fix.
BLOG_VOICE_REGENERATE_SUFFIX = (
    "\n\nYour previous draft broke the voice rules ($problems). Rewrite it from "
    "scratch obeying every rule, especially the em-dash limit and the banned phrases."
)
```

> Note the one em-dash in the bullet above ("anchor text + topic) — do not invent") is
> inside the *prompt instructions*, not the model's output, so it doesn't count against
> the generated post. Leave it or rephrase, your call.

Keep your `handoff/PROMPTS.md` §2 in sync with this so the doc and code don't drift.

---

## §5 — `app/content.py`: run the voice check in the regenerate loop

Two small edits.

**(a)** Import the validator at the top:

```python
from .blog_voice import validate_blog_voice
```

**(b)** Add an optional `validate` callback to `_ensure_unique` and check it right
after the empty-text guard (before the hash check), so a voice failure regenerates the
same way a collision does:

```python
    async def _ensure_unique(
        self,
        run_id: str,
        platform: str,
        make_candidate: Callable[[bool], Awaitable[ContentCandidate]],
        validate: Optional[Callable[[str], list[str]]] = None,   # NEW
    ) -> ContentCandidate:
        for attempt in range(self._max_retries):
            cand = await make_candidate(attempt > 0)
            if not cand.text:
                logger.warning("run %s %s attempt %d: empty text", run_id, platform, attempt)
                continue

            if validate is not None:                              # NEW
                problems = validate(cand.text)
                if problems:
                    logger.info(
                        "run %s %s attempt %d: voice check failed (%s), regenerating",
                        run_id, platform, attempt, "; ".join(problems),
                    )
                    continue

            h = content_hash(cand.text)
            # ... unchanged from here ...
```

**(c)** Pass the validator when generating the blog:

```python
        blog = await self._ensure_unique(run_id, "blog", blog_candidate, validate=validate_blog_voice)
```

**(optional, recommended)** Make the blog regenerate nudge voice-specific. In
`blog_candidate`, when `harder` is true, append `BLOG_VOICE_REGENERATE_SUFFIX` (filled
with the problems) instead of the generic `REGENERATE_SUFFIX`. Simplest version that
needs no extra plumbing — append both:

```python
        async def blog_candidate(harder: bool) -> ContentCandidate:
            user = prompts.BLOG_USER.substitute(...)
            if harder:
                user += prompts.REGENERATE_SUFFIX  # uniqueness nudge
                # voice nudge; $problems is generic here since the loop doesn't pass the
                # specific list down — fine, the rules are already in the base prompt.
                user += prompts.BLOG_VOICE_REGENERATE_SUFFIX.replace(
                    "$problems", "em-dashes and/or banned AI phrases"
                )
            ...
```

(If you want the *exact* problems threaded into the prompt, capture them in a closure
variable inside `_ensure_unique` and let `make_candidate` read it — but the base prompt
already states the rules, so the generic nudge is usually enough.)

The three social platforms keep generating without a voice check (their prompts are
short and platform-native). Add `validate=` to them too if you want the same guard.

---

## §6 — Frontend wiring (dropdown → API)

The prototype's dropdown is local state. Back it with the new endpoints:

```js
// load on mount
const { markets } = await fetch('/markets').then(r => r.json());
// markets: [{id, label, is_active, created_at}]
const active = markets.find(m => m.is_active) ?? markets[0];

// add a focus (becomes active)
await fetch('/markets', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ label }),
});

// select a focus
await fetch(`/markets/${id}/activate`, { method: 'POST' });

// remove (× on a row); handle 409 = "can't delete the only market"
const res = await fetch(`/markets/${id}`, { method: 'DELETE' });
```

`POST /hooks/run` no longer needs a `market` in the body — the server uses the active
one. Keep sending `{ market }` only if a specific run should override the selection.

---

## §7 — (Optional) make the Settings blog prompt actually editable

By default the blog prompt lives in `prompts.py` (source of truth) and the Settings
textarea is display/reference only. If operator edits must take effect:

1. Add a `app_settings (key TEXT PRIMARY KEY, value TEXT)` table.
2. `GET/PUT /settings/blog_prompt` to read/write the template string.
3. In `ContentService`, load the template from the store (falling back to
   `prompts.BLOG_USER`) instead of importing the constant.

Skip this unless you need live editing — it adds a moving part to the most quality-
sensitive prompt in the system.

---

## §8 — Tests

Mirror your existing test style (`tests/test_leads_db.py`, `tests/test_content.py`).

- **`tests/test_blog_voice.py`** (pure, no DB):
  - passes on a clean draft (0 problems)
  - flags a draft with 3+ em-dashes
  - flags each banned phrase ("delve into", etc.), case-insensitively
- **`tests/test_markets_db.py`** (test DB):
  - `add_market` makes it active; `ensure_seed_market` is a no-op when non-empty
  - `set_active_market` leaves exactly one active (assert the partial-index invariant)
  - `remove_market` returns "last" when one remains, promotes a new active when the
    active one is deleted
- **`tests/test_markets_endpoint.py`**: GET/POST/activate/DELETE happy paths + the 409.
- **content regenerate**: extend `test_content.py` with a fake LLM that returns a
  voice-failing blog first (4 em-dashes), then a clean one — assert the clean one is
  what gets locked into the registry.

---

## §9 — Human approval gate

The gate makes a real run generate + audit but **hold** without publishing, until a
person approves the pieces and releases the run. No schema change (see
`SCHEMA_additions.sql`) — it reuses `runs.status` and `content_registry.status`/`locked_at`.

### §9a — `app/config.py`: the flag

Add to the Phase 5 section of `Settings`:

```python
    # Human approval gate: when true (default), a real run generates + audits but
    # holds at status='review' — nothing distributes until a person approves and
    # releases it via POST /runs/{run_id}/publish. Set false to auto-publish on pass.
    human_approval_gate: bool = True
```

### §9b — `app/orchestrator.py`: hold before publish

Add an `approval_gate` parameter and a branch that returns after the audit when the
gate is on. Only the signature and the block before `publish` change:

```python
async def run_pipeline(
    run_id: str,
    market: str,
    *,
    research_service: ResearchProtocol,
    content_service: ContentProtocol,
    distribution_service: DistributionProtocol,
    dry_run: bool = False,
    approval_gate: bool = True,          # NEW
) -> dict:
    pain_point = await research_service.research(run_id, market)
    generated = await content_service.generate(run_id, pain_point)

    audit = await distribution_service.audit(run_id, generated["content_ids"])
    if not audit.ok:
        raise PublishAuditError(f"pre-publish audit failed: {audit.collisions}")

    # NEW: human approval gate — hold real runs for review before posting.
    if approval_gate and not dry_run:
        logger.info("run %s held for human approval (gate on)", run_id)
        return {
            "pain_point": pain_point,
            "generated": generated,
            "distribution": None,
            "dry_run": False,
            "held_for_review": True,
        }

    distribution = await distribution_service.publish(run_id, generated, dry_run=dry_run)
    return {
        "pain_point": pain_point,
        "generated": generated,
        "distribution": distribution,
        "dry_run": dry_run,
        "held_for_review": False,
    }
```

### §9c — `app/routers/hooks.py`: resolve + pass the gate, set status

Add the per-run override to `RunRequest`:

```python
class RunRequest(BaseModel):
    market: str | None = None
    dry_run: bool = False
    approval_gate: bool | None = None      # NEW; None = inherit the setting
```

In `trigger_run`, resolve the gate and pass it, then set status from the result:

```python
    dry_run = payload.dry_run if payload else False
    approval_gate = (
        payload.approval_gate
        if (payload and payload.approval_gate is not None)
        else get_settings().human_approval_gate
    )                                                                      # NEW
    ...
    result = await run_pipeline(                                           # capture result
        run_id,
        market,
        research_service=research_service,
        content_service=content_service,
        distribution_service=distribution_service,
        dry_run=dry_run,
        approval_gate=approval_gate,                                       # NEW
    )
    ...
    # dry runs preview only; gated real runs hold at review; else published.
    if dry_run or result.get("held_for_review"):                          # CHANGED
        status = "review"
    else:
        status = "published"
    await db.update_run_status(run_id, status)
    return RunResponse(run_id=run_id, status=status)
```

A gated run now returns `status="review"`; the operator releases it from the dashboard.

### §9d — `app/db.py`: gate helpers

Append. `get_run_generated` reconstructs exactly the `{content, content_ids}` shape
`DistributionService.publish` expects, straight from the registry.

```python
# ---------------------------------------------------------------------------
# Addendum: human approval gate.
# ---------------------------------------------------------------------------
async def get_run(run_id: str) -> dict | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, market, status, dry_run FROM runs WHERE id = $1", run_id
        )
    return dict(row) if row else None


async def list_run_content(run_id: str) -> list[dict]:
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, platform, status, snippet, content_hash, locked_at
            FROM content_registry WHERE run_id = $1 ORDER BY id
            """,
            run_id,
        )
    return [dict(r) for r in rows]


async def approve_content(content_id: int) -> str:
    """'pending'/'approved' -> 'approved' (locked_at set). Returns 'not_found' or 'ok'."""
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE content_registry
               SET status = 'approved',
                   locked_at = COALESCE(locked_at, now())
             WHERE id = $1 AND status IN ('pending', 'approved')
            RETURNING id
            """,
            content_id,
        )
        if row is None:
            exists = await conn.fetchrow("SELECT 1 FROM content_registry WHERE id = $1", content_id)
            return "ok" if exists else "not_found"  # already published counts as ok
    return "ok"


async def count_unapproved_content(run_id: str) -> int:
    async with _get_pool().acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM content_registry WHERE run_id = $1 AND status = 'pending'",
            run_id,
        )


async def get_run_generated(run_id: str) -> dict:
    """Rebuild {content: {platform: text}, content_ids: {platform: id}} for publish."""
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT platform, id, full_text FROM content_registry WHERE run_id = $1",
            run_id,
        )
    content = {r["platform"]: r["full_text"] for r in rows}
    content_ids = {r["platform"]: r["id"] for r in rows}
    return {"content": content, "content_ids": content_ids}


async def mark_run_content_published(run_id: str) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE content_registry SET status = 'published' WHERE run_id = $1", run_id
        )
```

### §9e — `app/main.py`: register the approvals router

```python
from .routers import approvals, hooks, lead_magnets, leads, markets   # add approvals + markets
...
app.include_router(approvals.router)                                   # NEW
```

`approvals_router.py` imports `get_distribution_service` from `hooks.py`, reusing the
same audited distributor — no duplicate wiring.

### §9f — Frontend wiring

- **Settings → Human approval gate toggle** reflects/sets `human_approval_gate`. If you
  keep the flag in env only, render it read-only from a `GET /settings` you expose;
  for live editing, back it with the optional `app_settings` table (§7) and a
  `PUT /settings/human_approval_gate`.
- **Content review approve buttons** → `POST /content/{content_id}/approve` per piece.
- **"Approve all & queue"** → approve each piece, then `POST /runs/{run_id}/publish`
  (or call publish with `?force=true` to approve-and-release in one shot).
- Handle `409` from `/runs/{id}/publish` (pieces still pending, or audit collision) by
  surfacing the `detail` message.

### §9g — Tests

- **`tests/test_gate_orchestrator.py`**: with fakes, `approval_gate=True, dry_run=False`
  returns `held_for_review=True` and calls `publish` **zero** times; `approval_gate=False`
  calls `publish` once. `dry_run=True` holds regardless of the gate.
- **`tests/test_approvals_endpoint.py`** (test DB): approving each piece flips status to
  `approved`; `POST /runs/{id}/publish` with pending pieces returns `409`; after
  approving all, publish marks the run `published` and content `published`; `force=true`
  publishes despite pending pieces.
- **hook status**: `POST /hooks/run` with the gate on returns `status="review"`; with a
  body `{"approval_gate": false}` returns `status="published"`.

---

## §10 — Reject / regenerate reason box

The Content review "Regenerate" (blog) / "Redo" (social) buttons open a reason modal
(quick-pick chips + free text). On submit, that piece is regenerated with the reviewer's
reason **threaded into the model prompt**, then re-run through the uniqueness engine (and
the blog voice check from §5). Drop-in router: `regenerate_router.py` →
`app/routers/regenerate.py`.

### §10a — `app/prompts.py`: a reason-aware regenerate suffix

Keep the generic `REGENERATE_SUFFIX`; add a reason-carrying one:

```python
REGENERATE_WITH_REASON = Template(
    "\n\nA reviewer rejected the previous draft for this reason: \"$reason\".\n"
    "Produce a clearly different draft that fixes exactly that, keep it unique versus "
    "all prior content, and obey every original rule (voice, structure, length)."
)
```

### §10b — `app/content.py`: regenerate one piece with the reason

Add a method that rebuilds the single-platform candidate with the reason appended and
runs it back through `_ensure_unique` (with the blog voice validator for blogs). It pulls
the run's pain point / insight / guide title so the prompt matches the original.

```python
    async def regenerate_piece(self, run_id: str, platform: str, reason: str) -> int:
        ctx = await self._store.get_run_context(run_id)   # see §10c
        pp = ctx["pain_point"]; insight = ctx.get("source_insight") or ""
        region = ctx.get("region") or "the target region"
        guide_title = ctx.get("guide_title") or "Free Guide"
        reason = (reason or "").strip()

        def _with_reason(user: str) -> str:
            user += prompts.REGENERATE_SUFFIX                      # keep the uniqueness nudge
            if reason:
                user += prompts.REGENERATE_WITH_REASON.substitute(reason=reason)
            return user

        if platform == "blog":
            async def cand(_harder: bool) -> ContentCandidate:
                user = _with_reason(prompts.BLOG_USER.substitute(
                    pain_point=pp, source_insight=insight, region=region,
                    lead_magnet_title=guide_title))
                data = await self._complete(prompts.BLOG_SYSTEM, user)
                meta = {
                    "meta_title": data.get("meta_title", ""),
                    "meta_desc": data.get("meta_description", ""),
                    "headers": data.get("headers") or [],
                    "internal_link_suggestions": data.get("internal_link_suggestions") or [],
                    "word_count": data.get("word_count"), "cta": data.get("cta", ""),
                }
                return ContentCandidate(text=(data.get("body_markdown") or "").strip(), meta=meta)
            res = await self._ensure_unique(run_id, "blog", cand, validate=validate_blog_voice)
            await self._store.insert_blog_meta(res.content_id, res.meta or {})
            return res.content_id

        # social platform
        async def cand(_harder: bool) -> ContentCandidate:
            data = await self._complete(prompts.SOCIAL_SYSTEM, _with_reason(
                prompts.SOCIAL_USER.substitute(
                    pain_point=pp, source_insight=insight, lead_magnet_title=guide_title)))
            return ContentCandidate(text=_compose_social(platform, data))
        res = await self._ensure_unique(run_id, platform, cand)
        return res.content_id
```

`_ensure_unique` already inserts the new registry row (status `pending`); the router then
marks the old row `superseded` (§10d) so it can't publish and won't block the new draft.

### §10c — `app/db.py`: helpers for regenerate

```python
async def get_content(content_id: int) -> dict | None:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, run_id, platform, status, snippet FROM content_registry WHERE id = $1",
            content_id,
        )
    return dict(row) if row else None


async def supersede_content(content_id: int) -> None:
    async with _get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE content_registry SET status = 'superseded' WHERE id = $1", content_id
        )


async def get_run_context(run_id: str) -> dict:
    """Pain point + insight + region + lead-magnet guide title, to rebuild a prompt."""
    async with _get_pool().acquire() as conn:
        pp = await conn.fetchrow(
            "SELECT text, source_insight, source_url FROM pain_points "
            "WHERE run_id = $1 ORDER BY created_at DESC LIMIT 1", run_id)
        lm = await conn.fetchrow(
            "SELECT headline FROM lead_magnets WHERE run_id = $1 LIMIT 1", run_id)
    return {
        "pain_point": pp["text"] if pp else "",
        "source_insight": pp["source_insight"] if pp else "",
        "region": None,                       # store region on pain_points if you want it verbatim
        "guide_title": (lm["headline"] if lm else "") or "Free Guide",
    }
```

Expose `get_run_context` on `DbContentStore` too (one-line passthrough, like the others).

**Exclude superseded rows from uniqueness + audit** so a rejected draft never blocks its
own replacement or a future run. In the two registry queries add `AND status <> 'superseded'`:

```python
# content_hash_exists(...)  ->  WHERE platform = $1 AND content_hash = $2 AND status <> 'superseded'
# content_max_cosine(...)   ->  WHERE platform = $1 AND status <> 'superseded'
```

### §10d — `app/main.py`: register the router

```python
from .routers import approvals, hooks, lead_magnets, leads, markets, regenerate  # + regenerate
...
app.include_router(regenerate.router)                                             # NEW
```

### §10e — Tests

- **`tests/test_regenerate.py`** (fakes): a rejected blog whose fake LLM first returns a
  near-duplicate then a distinct draft ends with a new `pending` row and the old row
  `superseded`; assert the reason string reached the prompt (spy on `_complete`).
- **endpoint**: `POST /content/{id}/regenerate {"reason": "..."}` returns `new_id` and
  flips the old row to `superseded`; regenerating a `published` piece returns `409`.

---

## §11 — Frontend-only additions (no new backend)

These prototype additions are pure UI over endpoints this addendum already defines — no
extra server work:

- **Run-detail approval panel** — the per-run approve + release UI. Backed by §9:
  `GET /runs/{id}/content` (pieces + status), `POST /content/{id}/approve`,
  `POST /runs/{id}/publish`. Disable "Release & publish" until
  `count_unapproved_content(run_id) == 0`, or call publish with `?force=true`.
- **Automation "Controls" hub + grouped sidebar** — an information-architecture screen
  that gathers existing controls in one place. It reads/writes the *same* endpoints:
  search focus → `/markets` (§1–§3); approval gate → the `human_approval_gate` setting
  (§9a/§9f); registry summary → a `GET /content/registry` count (or reuse run data);
  blog voice → the Settings prompt (§4/§7); regenerate presets → static labels the
  frontend sends as the `reason` in §10. Build it last; it needs no new routes.

