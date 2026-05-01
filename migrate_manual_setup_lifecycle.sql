ALTER TABLE manual_setups
ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual',
ADD COLUMN IF NOT EXISTS strategy_type TEXT DEFAULT 'manual_setup',
ADD COLUMN IF NOT EXISTS setup_type TEXT DEFAULT 'manual_setup',
ADD COLUMN IF NOT EXISTS tracking_enabled BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS tracking_status TEXT DEFAULT 'watching',
ADD COLUMN IF NOT EXISTS confirmation_required BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS failed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS completion_note TEXT,
ADD COLUMN IF NOT EXISTS telegram_alert_sent BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS telegram_alert_sent_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS telegram_error TEXT,
ADD COLUMN IF NOT EXISTS last_alert_type TEXT,
ADD COLUMN IF NOT EXISTS last_alert_time TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS current_price NUMERIC(12,2),
ADD COLUMN IF NOT EXISTS distance_to_entry_pips NUMERIC(12,2);

CREATE INDEX IF NOT EXISTS idx_manual_setups_status
ON manual_setups(status);

CREATE INDEX IF NOT EXISTS idx_manual_setups_tracking_enabled
ON manual_setups(tracking_enabled);

CREATE INDEX IF NOT EXISTS idx_manual_setups_strategy_type
ON manual_setups(strategy_type);

NOTIFY pgrst, 'reload schema';
