# Cadence — Addendum: markets, human-voice blog prompt, approval gate

This is an **incremental** handoff. It assumes you already have the Phase 1–5 backend
(`backend/app/...`) and only adds what the dashboard prototype introduced after that
backend was built:

1. **Configurable search focus / market** — a persisted, editable list of markets the
   operator can add, remove, and choose between (the top-bar dropdown). Today
   `POST /hooks/run` takes a single `market` string and otherwise falls back to
   `settings.default_market`; there is no list and no way to manage it.
2. **Human-voice blog prompt + voice validation** — replace the current blog prompt
   with the strict human-voice spec (em-dash limit, banned AI phrases, direct "you"),
   and add a deterministic post-generation check that forces a regenerate when a draft
   breaks the rules. The uniqueness engine is unchanged.
3. **Human approval gate** — a settings flag (default ON) that makes a real run
   generate + audit but **hold at `status='review'`**, distributing nothing until a
   person approves the pieces and releases the run. When OFF, runs auto-publish as
   soon as they clear the uniqueness audit (today's behavior). This is the backend for
   the Settings → "Human approval gate" toggle and the Content review approve actions.
4. **Reject / regenerate reason box** — rejecting a piece captures a reason (quick-pick
   chips + free text) that gets **threaded into the regenerate prompt**, then the new
   draft is re-run through the uniqueness engine and blog voice check. Adds
   `POST /content/{id}/regenerate {reason}` and a `superseded` content status.

Two further prototype additions are **frontend-only** (no new backend — see
`INTEGRATION.md §11`): the **run-detail approval panel** (uses the §9 endpoints) and the
**Automation "Controls" hub + grouped sidebar** (consolidates existing controls).

Nothing else in the pipeline changes. The uniqueness registry, research, leads,
campaigns, and distribution channels all stay as-is.

---

## Files in this addendum

| File | What to do with it |
|---|---|
| `SCHEMA_additions.sql` | Append to `handoff/SCHEMA.sql`, then re-run `python -m scripts.init_db` (idempotent). Adds the `markets` table; the approval gate needs **no** schema change (it reuses `content_registry.status`/`locked_at` and `runs.status`). |
| `markets_router.py` | Drop in as `backend/app/routers/markets.py`. |
| `blog_voice.py` | Drop in as `backend/app/blog_voice.py`. |
| `approvals_router.py` | Drop in as `backend/app/routers/approvals.py` (the gate release + per-piece approve endpoints). |
| `regenerate_router.py` | Drop in as `backend/app/routers/regenerate.py` (regenerate-one-piece-with-reason endpoint). |
| `INTEGRATION.md` | Step-by-step edits to existing files (`db.py`, `prompts.py`, `content.py`, `config.py`, `orchestrator.py`, `main.py`, `routers/hooks.py`), plus frontend wiring and tests. |

---

## Integration checklist (ordered, easiest → involved)

**Markets**
1. **Schema** — append `SCHEMA_additions.sql`, re-run `init_db`. *(trivial)*
2. **Markets DB layer** — paste the functions from `INTEGRATION.md` §1 into `app/db.py`. *(easy)*
3. **Markets router** — add `app/routers/markets.py`, register it in `app/main.py`, seed the default market on startup. *(easy)*
4. **Hook uses active market** — one-line change in `routers/hooks.py` §3. *(easy)*

**Human-voice blog**
5. **Blog prompt** — replace `BLOG_SYSTEM` / `BLOG_USER` in `app/prompts.py`, add `BLOG_VOICE_REGENERATE_SUFFIX`. *(easy)*
6. **Voice validation** — add `app/blog_voice.py`, wire the optional `validate` callback into `ContentService._ensure_unique` and pass it for the blog. *(medium)*

**Approval gate**
7. **Config flag** — add `human_approval_gate: bool = True` to `config.py` §9a. *(trivial)*
8. **Orchestrator hold** — add the `approval_gate` param + the "hold before publish" branch to `run_pipeline` §9b. *(easy)*
9. **Hook wiring** — resolve the gate (per-run override → setting), pass it through, and set run status `review` vs `published` §9c. *(easy)*
10. **DB helpers** — add `get_run`, `list_run_content`, `approve_content`, `count_unapproved_content`, `get_run_generated`, `mark_run_content_published` to `app/db.py` §9d. *(easy)*
11. **Approvals router** — add `app/routers/approvals.py`, register in `main.py` §9e. *(easy)*

**Frontend + tests**
12. **Frontend** — dropdown → `/markets`; Content review approve buttons → `/content/{id}/approve` + `/runs/{id}/publish`; Settings toggle → the `human_approval_gate` setting §6–§9f. *(medium)*
13. **Tests** — markets, blog voice, and the gate hold/release paths §8, §9g. *(medium)*

Steps are independent across the three features; within the gate, 7→11 are ordered.

---

## How the gate changes the run lifecycle

```
                         approval_gate = OFF (or dry_run)
research → generate → audit ─────────────────────────────► publish → status=published

                         approval_gate = ON
research → generate → audit ──► HOLD (status=review, pieces 'pending')
                                     │
              person approves pieces │  POST /content/{id}/approve
                                     ▼
                              POST /runs/{id}/publish ──► re-audit → publish → status=published
```

The gate never bypasses the uniqueness audit — release re-runs it before posting.

---

## Notes / decisions to confirm

- **Single active market.** The schema enforces "at most one active" with a partial
  unique index. If you'd rather run *every* market each cycle, iterate `list_markets()`
  in the orchestrator instead — the prototype models a single active focus.
- **Voice check is deterministic, no model call**, so it's free to run on every draft;
  a non-empty problem list makes `_ensure_unique` regenerate (same loop as a collision).
- **Gate default is ON**, matching the prototype default and the safe choice — nothing
  posts without a click. A per-run `approval_gate` in the POST body overrides the
  setting (e.g. a trusted automated market could pass `false`).
- **Per-run override vs global.** `human_approval_gate` is the global default;
  `RunRequest.approval_gate` (nullable) overrides per call. Karbon's scheduled webhook
  can omit it and inherit the setting.
- **Editable prompt persistence is optional** (see `INTEGRATION.md §7`): by default the
  blog prompt and the gate flag live in code/env; add a small `app_settings` table only
  if operators must change them live from the dashboard without a redeploy.
