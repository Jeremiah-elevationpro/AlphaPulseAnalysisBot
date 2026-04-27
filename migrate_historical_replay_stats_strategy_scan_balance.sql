-- migrate_historical_replay_stats_strategy_scan_balance.sql
-- Adds multi-strategy diagnostic columns to historical_replay_stats.
-- Safe to run multiple times (all statements use IF NOT EXISTS).
--
-- Run in Supabase SQL Editor:
--   Dashboard → SQL Editor → New query → paste → Run

ALTER TABLE historical_replay_stats
    ADD COLUMN IF NOT EXISTS strategy_scan_balance JSONB    DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS strategy_summary      JSONB    DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS session_summary       JSONB    DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS confluence_summary    JSONB    DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS learning_summary      JSONB    DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS missing_pips_count    INTEGER  DEFAULT 0,
    ADD COLUMN IF NOT EXISTS validation_warnings   JSONB    DEFAULT '[]'::jsonb;

-- Index for scan-balance queries
CREATE INDEX IF NOT EXISTS idx_replay_stats_run_id
    ON historical_replay_stats (replay_run_id);

-- Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
