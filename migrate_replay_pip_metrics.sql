-- ============================================================
-- AlphaPulse Historical Replay Pip Metrics Migration
-- Run this once in Supabase SQL Editor before replaying again:
--   python -m historical_replay.run --months 2
-- ============================================================

ALTER TABLE historical_replay_trades
    ADD COLUMN IF NOT EXISTS pips_to_tp1        NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS pips_to_tp2        NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS pips_to_tp3        NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS pips_to_tp4        NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS pips_to_tp5        NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS realized_pips      NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS max_potential_pips NUMERIC(10,2),
    ADD COLUMN IF NOT EXISTS final_pips         NUMERIC(10,2);

ALTER TABLE historical_replay_stats
    ADD COLUMN IF NOT EXISTS pip_summary           JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS pip_by_timeframe_pair JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS pip_by_bias           JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS pip_by_session        JSONB DEFAULT '{}'::jsonb;
