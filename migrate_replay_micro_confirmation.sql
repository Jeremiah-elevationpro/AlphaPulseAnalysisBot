-- ============================================================
-- AlphaPulse Historical Replay Micro-Confirmation Migration
-- Run in Supabase SQL Editor before replay if your tables already exist.
-- Safe to run multiple times.
-- ============================================================

ALTER TABLE historical_replay_trades
    ADD COLUMN IF NOT EXISTS micro_confirmation_type TEXT,
    ADD COLUMN IF NOT EXISTS micro_confirmation_score NUMERIC(8,2),
    ADD COLUMN IF NOT EXISTS micro_layer_decision TEXT;

ALTER TABLE historical_replay_stats
    ADD COLUMN IF NOT EXISTS performance_by_micro_confirmation JSONB DEFAULT '{}'::jsonb;
