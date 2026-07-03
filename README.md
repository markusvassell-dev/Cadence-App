# Cadence — deploy-ready backend

A bi-weekly content engine (FastAPI) that researches a fresh market pain point,
generates guaranteed-unique SEO content for 4 platforms, captures leads into
ActiveCampaign, and drafts a 3-email nurture sequence.

**This folder is a flat, deploy-ready copy of the backend.** The `app/` package
sits at the folder root, so uvicorn imports `app.main:app` with no path tricks —
which is the whole point (see "Why this layout" below).

## Deploy to Railway

1. **Push this folder to GitHub.** Either make it the repository root, or keep it
   as a subfolder and set the Railway service's **Root Directory** to it. Either
   way, `app/` must sit at the directory Railway builds from.
2. **Add a Postgres database** (Railway → New → Database → PostgreSQL). It exposes
   a `DATABASE_URL` you'll reference next.
3. **Set environment variables** on the service (Variables tab):
   - `DATABASE_URL` — **required.** Reference the Postgres plugin's value
     (`${{ Postgres.DATABASE_URL }}`). Without it the app crashes on startup.
   - `ANTHROPIC_API_KEY` — required for research/content generation.
   - Optional: `DEFAULT_MARKET`, `SEARCH_PROVIDER`, ActiveCampaign / channel keys —
     see [`.env.example`](.env.example). Every external integration falls back to a
     keyless stub when unset, so the app boots without them.
4. **Initialize the schema once** (creates all tables, including `markets`, which
   the app seeds on startup). From the Railway service shell, or locally with
   `DATABASE_URL` pointed at the Railway database:
   ```bash
   python -m scripts.init_db
   ```
5. **Deploy.** railpack detects Python, installs `requirements.txt`, and runs the
   start command from [`railway.toml`](railway.toml):
   ```
   uvicorn app.main:app --host 0.0.0.0 --port $PORT
   ```
6. **Verify:** `GET /health` returns `{"status":"ok"}`.

## Why this layout (the fix)

Earlier deploys crash-looped with `ModuleNotFoundError: No module named 'app'`.
The app previously lived in a `backend/` subfolder, and the Railway service's Root
Directory pointed at `backend/`. That meant inside the container the *contents* of
`backend/` were at `/app`, so `app/` was `/app/app` and **`/app/backend` did not
exist** — yet the start command kept trying to reach it (`cd backend`,
`--app-dir backend`). Every path-juggling variant missed.

Here the `app/` package is at the deploy root, so `uvicorn app.main:app` resolves
directly, with no `cd` and no `--app-dir`. Nothing to misalign.

Two supporting fixes are baked in:
- `scripts/init_db.py` now applies **both** `handoff/SCHEMA.sql` and
  `handoff/addendum/SCHEMA_additions.sql`, so the `markets` table the startup
  seeder needs actually exists (it was missing from the base schema).
- `Procfile` and `railway.toml` both use the same plain start command, so whichever
  Railway honors, the result is identical.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Postgres (any reachable instance), then point DATABASE_URL at it:
cp .env.example .env            # edit DATABASE_URL if needed
python -m scripts.init_db       # applies SCHEMA.sql + addendum

uvicorn app.main:app --reload
```

Trigger a run (what Karbon's webhook calls on a schedule):

```bash
curl -X POST http://localhost:8000/hooks/run \
  -H "Content-Type: application/json" \
  -d '{"market": "health & wellness, underdeveloped and emerging markets"}'
# -> {"run_id": "RUN-XXXXXXXX", "status": "review"}
```

Run tests (DB-backed tests skip automatically if `DATABASE_URL` isn't set):

```bash
pytest
```

## Layout

```
app/            FastAPI service (Phases 1-5 + addendum)
  main.py         app factory + lifespan (init_pool, seed market)
  routers/        /hooks/run, /leads, /markets, /lead-magnet/{slug},
                  /runs/{id}/content, /content/{id}/approve,
                  /runs/{id}/publish, /content/{id}/regenerate
  research.py content.py embeddings.py search.py uniqueness.py
  activecampaign.py email_sequence.py leads.py
  distributors.py distribution.py retry.py alerts.py blog_voice.py
  llm.py prompts.py landing.py db.py orchestrator.py config.py
scripts/init_db.py   applies handoff/SCHEMA.sql + addendum
handoff/             schema + design/spec reference
tests/               pytest suite
requirements.txt     runtime deps         Procfile / railway.toml  start command
.env.example         all config knobs     requirements-dev.txt     + pytest deps
```
