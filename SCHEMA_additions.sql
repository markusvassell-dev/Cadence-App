-- ===========================================================================
-- ADDENDUM to handoff/SCHEMA.sql — configurable search focus / markets.
-- Append this block to SCHEMA.sql and re-run: python -m scripts.init_db
-- All statements are idempotent (IF NOT EXISTS), so re-running is safe.
-- ===========================================================================

-- The operator-managed list of markets / search focuses shown in the dashboard
-- dropdown. Exactly one row may be active at a time (the partial unique index
-- below enforces it); the /hooks/run endpoint defaults to the active one.
CREATE TABLE IF NOT EXISTS markets (
    id          BIGSERIAL PRIMARY KEY,
    label       TEXT UNIQUE NOT NULL,            -- e.g. 'Maternal health · South Asia'
    is_active   BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one active market. Postgres allows many rows where the predicate is
-- false, but only one where is_active = true.
CREATE UNIQUE INDEX IF NOT EXISTS markets_single_active
    ON markets (is_active) WHERE is_active;

-- ===========================================================================
-- Human approval gate — NO schema change required.
-- The gate reuses columns that already exist in handoff/SCHEMA.sql:
--   • runs.status                 -> 'review' while held, 'published' on release
--   • content_registry.status     -> 'pending' -> 'approved' -> 'published'
--   • content_registry.locked_at  -> set when a piece is approved
-- The index below is optional but makes the per-run review queries
-- (list_run_content / count_unapproved_content / get_run_generated) fast.
CREATE INDEX IF NOT EXISTS content_registry_run_idx
    ON content_registry (run_id);

