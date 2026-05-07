-- ============================================================
-- AlphaPulse Historical Replay Quality Tags Migration
-- Run in Supabase SQL Editor if historical_replay_trades already exists.
-- Safe to run multiple times.
-- ============================================================

ALTER TABLE historical_replay_trades
    ADD COLUMN IF NOT EXISTS high_quality_trade BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS micro_strength TEXT;
