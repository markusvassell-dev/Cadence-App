-- Cadence — Postgres schema (Railway)
-- Requires: CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector, for the uniqueness engine
-- Embedding dim below assumes a 1536-dim model; change to match your provider.

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- A pipeline run: one bi-weekly trigger from Karbon.
-- ---------------------------------------------------------------------------
CREATE TABLE runs (
    id            TEXT PRIMARY KEY,                 -- e.g. 'RUN-2412'
    market        TEXT NOT NULL,                    -- 'health & wellness, underdeveloped markets'
    region        TEXT,                             -- resolved sub-region, e.g. 'Sub-Saharan Africa'
    status        TEXT NOT NULL DEFAULT 'running',  -- running | review | published | failed
    novelty       INT,                              -- 0-100 score for the pain point
    dry_run       BOOLEAN NOT NULL DEFAULT false,
    error         TEXT,                             -- populated when status='failed'
    triggered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Pain points — the research output. Uniqueness guard (string similarity)
-- runs against this table before a new one is accepted.
-- ---------------------------------------------------------------------------
CREATE TABLE pain_points (
    id             BIGSERIAL PRIMARY KEY,
    run_id         TEXT REFERENCES runs(id),
    text           TEXT NOT NULL,                   -- the pain point itself
    source_insight TEXT NOT NULL,                   -- supporting evidence / citation
    source_url     TEXT,
    embedding      vector(1536),                    -- optional, for semantic dedupe
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- THE content registry — immutable ledger guaranteeing no repeat content ever.
-- Insert ONLY after a piece passes both exact-hash and fuzzy-similarity checks.
-- Never UPDATE/DELETE rows here (that's what makes uniqueness permanent).
-- ---------------------------------------------------------------------------
CREATE TABLE content_registry (
    id           BIGSERIAL PRIMARY KEY,
    run_id       TEXT REFERENCES runs(id),
    platform     TEXT NOT NULL,                     -- blog | linkedin | facebook | instagram
    content_hash CHAR(64) NOT NULL,                 -- SHA-256 of normalized text (exact-dup guard)
    snippet      TEXT NOT NULL,                     -- first ~280 chars, for human scanning
    full_text    TEXT NOT NULL,
    embedding    vector(1536),                      -- for cosine-similarity fuzzy dedupe
    status       TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | published
    locked_at    TIMESTAMPTZ,                       -- set when approved (becomes immutable)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform, content_hash)                 -- DB-level exact-duplicate guarantee
);
-- ANN index keeps fuzzy checks fast as the registry grows:
CREATE INDEX content_registry_embedding_idx
    ON content_registry USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- ---------------------------------------------------------------------------
-- Blog-specific SEO metadata (1:1 with a blog content_registry row).
-- ---------------------------------------------------------------------------
CREATE TABLE blog_meta (
    content_id    BIGINT PRIMARY KEY REFERENCES content_registry(id),
    meta_title    TEXT NOT NULL,
    meta_desc     TEXT NOT NULL,
    headers       JSONB NOT NULL,                   -- ["H2 ...", ...]
    internal_link_suggestions JSONB,                -- [{anchor, target_topic}, ...]
    word_count    INT
);

-- ---------------------------------------------------------------------------
-- Lead magnet landing page, one per run.
-- ---------------------------------------------------------------------------
CREATE TABLE lead_magnets (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT REFERENCES runs(id),
    slug        TEXT UNIQUE NOT NULL,               -- '/lead-magnet/cold-chain-probiotics-2412'
    headline    TEXT NOT NULL,
    body_html   TEXT,                               -- rendered page (or store JSON + render client-side)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Captured leads → pushed to ActiveCampaign.
-- ---------------------------------------------------------------------------
CREATE TABLE leads (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT REFERENCES runs(id),
    content_id      BIGINT REFERENCES content_registry(id),  -- which piece drove this lead
    name            TEXT NOT NULL,
    email           TEXT NOT NULL,
    pain_point      TEXT,
    ac_contact_id   TEXT,                            -- ActiveCampaign contact id, once synced
    sync_status     TEXT NOT NULL DEFAULT 'pending', -- pending | synced | failed
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX leads_email_idx ON leads (email);

-- ---------------------------------------------------------------------------
-- Drafted 3-email nurture sequences.
-- ---------------------------------------------------------------------------
CREATE TABLE campaigns (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT REFERENCES runs(id),
    lead_id     BIGINT REFERENCES leads(id),         -- null = run-level template
    status      TEXT NOT NULL DEFAULT 'draft',       -- draft | approved | sending | sent
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE campaign_emails (
    id           BIGSERIAL PRIMARY KEY,
    campaign_id  BIGINT REFERENCES campaigns(id) ON DELETE CASCADE,
    position     INT NOT NULL,                        -- 1, 2, 3
    goal         TEXT NOT NULL,                        -- deliver | educate | soft_pitch
    timing       TEXT NOT NULL,                        -- 'immediately' | 'day_3' | 'day_7'
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Distribution channel config + per-run publish results.
-- ---------------------------------------------------------------------------
CREATE TABLE channels (
    platform     TEXT PRIMARY KEY,                    -- blog | linkedin | facebook | instagram
    status       TEXT NOT NULL DEFAULT 'stub',        -- connected | reconnect | stub
    config       JSONB                                -- tokens/urls (or store secrets in env, ids here)
);

CREATE TABLE distribution_log (
    id           BIGSERIAL PRIMARY KEY,
    run_id       TEXT REFERENCES runs(id),
    content_id   BIGINT REFERENCES content_registry(id),
    platform     TEXT NOT NULL,
    result       TEXT NOT NULL,                        -- posted | dry_run | skipped | failed
    external_url TEXT,
    detail       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===========================================================================
-- Addendum (markets & voice): operator-managed search focuses.
-- The human approval gate needs no schema change — it reuses runs.status and
-- content_registry.status/locked_at. See handoff/addendum/.
-- ===========================================================================

-- Operator-managed list of markets / search focuses shown in the dashboard
-- dropdown. Exactly one row may be active at a time (partial unique index below);
-- /hooks/run defaults to the active one.
CREATE TABLE IF NOT EXISTS markets (
    id          BIGSERIAL PRIMARY KEY,
    label       TEXT UNIQUE NOT NULL,            -- e.g. 'Maternal health · South Asia'
    is_active   BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one active market.
CREATE UNIQUE INDEX IF NOT EXISTS markets_single_active
    ON markets (is_active) WHERE is_active;

-- Speeds up the per-run review queries used by the approval gate.
CREATE INDEX IF NOT EXISTS content_registry_run_idx
    ON content_registry (run_id);
