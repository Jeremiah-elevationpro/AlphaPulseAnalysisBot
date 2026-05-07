-- ============================================================
-- AlphaPulse — Break + Retest Research Column Migration
-- Adds break+retest-specific columns to strategy_research_trades.
-- Safe to run multiple times (ADD COLUMN IF NOT EXISTS).
-- Run in Supabase SQL Editor.
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- Break info columns
-- ─────────────────────────────────────────────────────────────
ALTER TABLE strategy_research_trades
    ADD COLUMN IF NOT EXISTS break_level           NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS break_time            TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS break_close           NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS break_distance_pips   NUMERIC(10, 2),
    ADD COLUMN IF NOT EXISTS source_level_type     TEXT,
    ADD COLUMN IF NOT EXISTS source_strategy_type  TEXT;

-- ─────────────────────────────────────────────────────────────
-- Retest + confirmation columns
-- ─────────────────────────────────────────────────────────────
ALTER TABLE strategy_research_trades
    ADD COLUMN IF NOT EXISTS retest_level               NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS retest_time                TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS retest_confirmation_type   TEXT,
    ADD COLUMN IF NOT EXISTS retest_confirmation_score  NUMERIC(8, 2);

-- ─────────────────────────────────────────────────────────────
-- Failed-engulf origin columns
-- ─────────────────────────────────────────────────────────────
ALTER TABLE strategy_research_trades
    ADD COLUMN IF NOT EXISTS original_engulf_high             NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS original_engulf_low              NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS original_engulf_mid              NUMERIC(12, 3),
    ADD COLUMN IF NOT EXISTS original_engulf_direction        TEXT,
    ADD COLUMN IF NOT EXISTS original_engulf_time             TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS original_quality_rejection_count INTEGER,
    ADD COLUMN IF NOT EXISTS original_structure_break_count   INTEGER,
    ADD COLUMN IF NOT EXISTS original_quality_score           NUMERIC(8, 2);

-- ─────────────────────────────────────────────────────────────
-- Indexes for common query patterns
-- ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_srt_break_level
    ON strategy_research_trades (break_level);

CREATE INDEX IF NOT EXISTS idx_srt_source_level_type
    ON strategy_research_trades (source_level_type);

CREATE INDEX IF NOT EXISTS idx_srt_retest_confirmation_type
    ON strategy_research_trades (retest_confirmation_type);

-- Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
