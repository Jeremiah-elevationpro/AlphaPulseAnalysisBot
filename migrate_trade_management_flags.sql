-- AlphaPulse trade-management alignment fields.
-- Run in Supabase SQL Editor if your tables already existed before this patch.

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS tp_progress_reached INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS protected_after_tp1 BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS tp1_alert_sent BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS breakeven_exit BOOLEAN DEFAULT FALSE;

ALTER TABLE historical_replay_trades
    ADD COLUMN IF NOT EXISTS tp_progress_reached INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS protected_after_tp1 BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS tp1_alert_sent BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS breakeven_exit BOOLEAN DEFAULT FALSE;
