-- ============================================================
-- AlphaPulse Historical Replay Execution Filter Migration
-- Run in Supabase SQL Editor before replay if your tables already exist.
-- Safe to run multiple times.
-- ============================================================

ALTER TABLE historical_replay_trades
    ADD COLUMN IF NOT EXISTS h1_liquidity_sweep BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS h1_sweep_direction TEXT,
    ADD COLUMN IF NOT EXISTS h1_reclaim_confirmed BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS pd_location TEXT,
    ADD COLUMN IF NOT EXISTS pd_filter_score NUMERIC(8,2),
    ADD COLUMN IF NOT EXISTS bias_gate_result TEXT;

ALTER TABLE historical_replay_stats
    ADD COLUMN IF NOT EXISTS performance_by_h1_sweep JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS performance_by_pd_location JSONB DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS performance_by_bias_gate JSONB DEFAULT '{}'::jsonb;
