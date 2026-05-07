-- migrate_multi_strategy_replay_crash_fix.sql
-- Adds crash-resilience columns to multi-strategy replay tables.
-- Safe to run multiple times (all statements use IF NOT EXISTS).

-- ── multi_strategy_replay_runs ─────────────────────────────────────────────

ALTER TABLE multi_strategy_replay_runs
    ADD COLUMN IF NOT EXISTS status           TEXT            DEFAULT 'running',
    ADD COLUMN IF NOT EXISTS error_message    TEXT,
    ADD COLUMN IF NOT EXISTS strategy_summary JSONB           DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS session_summary  JSONB           DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS confluence_summary JSONB         DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS learning_summary JSONB           DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS completed_at     TIMESTAMPTZ;

-- ── multi_strategy_replay_trades ───────────────────────────────────────────

ALTER TABLE multi_strategy_replay_trades
    ADD COLUMN IF NOT EXISTS strategy_type       TEXT,
    ADD COLUMN IF NOT EXISTS final_result        TEXT,
    ADD COLUMN IF NOT EXISTS final_pips          NUMERIC(12,2),
    ADD COLUMN IF NOT EXISTS reward_score        NUMERIC(8,2),
    ADD COLUMN IF NOT EXISTS missing_pips        BOOLEAN        DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS validation_warning  TEXT;

-- ── strategy_learning_profiles ─────────────────────────────────────────────

ALTER TABLE strategy_learning_profiles
    ADD COLUMN IF NOT EXISTS last_error  TEXT,
    ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ DEFAULT NOW();

-- Indexes for common query patterns

CREATE INDEX IF NOT EXISTS idx_ms_runs_status
    ON multi_strategy_replay_runs (status);

CREATE INDEX IF NOT EXISTS idx_ms_trades_missing_pips
    ON multi_strategy_replay_trades (missing_pips)
    WHERE missing_pips = TRUE;

CREATE INDEX IF NOT EXISTS idx_ms_trades_strategy_result
    ON multi_strategy_replay_trades (strategy_type, final_result);

-- Reload PostgREST schema cache

NOTIFY pgrst, 'reload schema';
