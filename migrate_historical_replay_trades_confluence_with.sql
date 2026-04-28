-- Migration: add confluence_with to historical_replay_trades
-- Run this in the Supabase SQL Editor (Dashboard → SQL Editor → New query → Run)
--
-- This column stores the raw comma-joined confluence tag string from the replay engine.
-- After applying this migration, replay trade inserts will no longer fall back to
-- stripping this column, and the full trade payload will be persisted.

ALTER TABLE historical_replay_trades
    ADD COLUMN IF NOT EXISTS confluence_with TEXT;

-- Optional index for filtering by confluence
CREATE INDEX IF NOT EXISTS idx_hrt_confluence_with
    ON historical_replay_trades (confluence_with)
    WHERE confluence_with IS NOT NULL AND confluence_with <> '';
