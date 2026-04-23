-- AlphaPulse — Add advanced strategy context columns to trades table
-- Run this in the Supabase SQL Editor if you created the table before this update.

ALTER TABLE trades
  ADD COLUMN IF NOT EXISTS is_qm             BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_psychological  BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS is_liquidity_sweep BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS session_name      TEXT    NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS h4_bias           TEXT    NOT NULL DEFAULT '',
  ADD COLUMN IF NOT EXISTS trend_aligned     BOOLEAN NOT NULL DEFAULT TRUE;
