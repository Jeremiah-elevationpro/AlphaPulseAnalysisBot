-- Migration: manual_setups tracking columns + 24/7 session context labels
-- Run in Supabase SQL Editor or directly against your PostgreSQL database.
-- All statements are idempotent (IF NOT EXISTS / DO NOTHING).

ALTER TABLE manual_setups
    ADD COLUMN IF NOT EXISTS source                 VARCHAR(30)   DEFAULT 'manual',
    ADD COLUMN IF NOT EXISTS strategy_type          VARCHAR(50)   DEFAULT 'manual_setup',
    ADD COLUMN IF NOT EXISTS setup_type             VARCHAR(50)   DEFAULT 'manual_setup',
    ADD COLUMN IF NOT EXISTS tracking_enabled       BOOLEAN       DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS tracking_status        VARCHAR(50)   DEFAULT 'watching',
    ADD COLUMN IF NOT EXISTS confirmation_required  BOOLEAN       DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS telegram_alert_sent    BOOLEAN       DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS telegram_alert_sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS telegram_error         TEXT,
    ADD COLUMN IF NOT EXISTS approach_alert_sent_at TIMESTAMPTZ;

-- Backfill existing rows
UPDATE manual_setups
SET
    source       = 'manual'        WHERE source IS NULL;
UPDATE manual_setups
SET
    strategy_type = 'manual_setup' WHERE strategy_type IS NULL;
UPDATE manual_setups
SET
    setup_type   = 'manual_setup'  WHERE setup_type IS NULL;
UPDATE manual_setups
SET
    tracking_enabled = TRUE        WHERE tracking_enabled IS NULL;
UPDATE manual_setups
SET
    tracking_status = 'watching'   WHERE tracking_status IS NULL;

-- Reload PostgREST schema cache (Supabase)
NOTIFY pgrst, 'reload schema';
