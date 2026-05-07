CREATE TABLE IF NOT EXISTS live_setups (
id BIGSERIAL PRIMARY KEY,
setup_key TEXT UNIQUE,
source TEXT DEFAULT 'live_bot',
strategy_type TEXT NOT NULL,
alert_stage TEXT DEFAULT 'setup',
setup_status TEXT DEFAULT 'watching',
symbol TEXT NOT NULL DEFAULT 'XAUUSD',
direction TEXT NOT NULL,
entry NUMERIC(12,3),
sl NUMERIC(12,3),
tp1 NUMERIC(12,3),
tp2 NUMERIC(12,3),
tp3 NUMERIC(12,3),
level_type TEXT,
level_price NUMERIC(12,3),
level_high NUMERIC(12,3),
level_low NUMERIC(12,3),
timeframe TEXT,
timeframe_pair TEXT,
session_name TEXT,
dominant_bias TEXT,
bias_strength TEXT,
confirmation_type TEXT,
confirmation_score NUMERIC(8,2),
pd_location TEXT,
distance_to_level_pips NUMERIC(10,2),
quality_score NUMERIC(10,2),
learning_score NUMERIC(10,2),
learning_context TEXT,
quality_rejection_count INTEGER,
structure_break_count INTEGER,
watchlist_alert_sent BOOLEAN DEFAULT FALSE,
watchlist_alert_sent_at TIMESTAMPTZ,
entry_alert_sent BOOLEAN DEFAULT FALSE,
entry_alert_sent_at TIMESTAMPTZ,
telegram_alert_sent BOOLEAN DEFAULT FALSE,
telegram_alert_sent_at TIMESTAMPTZ,
created_at TIMESTAMPTZ DEFAULT NOW(),
updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE live_setups
ADD COLUMN IF NOT EXISTS setup_key TEXT,
ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'live_bot',
ADD COLUMN IF NOT EXISTS strategy_type TEXT,
ADD COLUMN IF NOT EXISTS alert_stage TEXT DEFAULT 'setup',
ADD COLUMN IF NOT EXISTS setup_status TEXT DEFAULT 'watching',
ADD COLUMN IF NOT EXISTS symbol TEXT DEFAULT 'XAUUSD',
ADD COLUMN IF NOT EXISTS direction TEXT,
ADD COLUMN IF NOT EXISTS entry NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS sl NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS tp1 NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS tp2 NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS tp3 NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS level_type TEXT,
ADD COLUMN IF NOT EXISTS level_price NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS level_high NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS level_low NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS timeframe TEXT,
ADD COLUMN IF NOT EXISTS timeframe_pair TEXT,
ADD COLUMN IF NOT EXISTS session_name TEXT,
ADD COLUMN IF NOT EXISTS dominant_bias TEXT,
ADD COLUMN IF NOT EXISTS bias_strength TEXT,
ADD COLUMN IF NOT EXISTS confirmation_type TEXT,
ADD COLUMN IF NOT EXISTS confirmation_score NUMERIC(8,2),
ADD COLUMN IF NOT EXISTS pd_location TEXT,
ADD COLUMN IF NOT EXISTS distance_to_level_pips NUMERIC(10,2),
ADD COLUMN IF NOT EXISTS quality_score NUMERIC(10,2),
ADD COLUMN IF NOT EXISTS learning_score NUMERIC(10,2),
ADD COLUMN IF NOT EXISTS learning_context TEXT,
ADD COLUMN IF NOT EXISTS quality_rejection_count INTEGER,
ADD COLUMN IF NOT EXISTS structure_break_count INTEGER,
ADD COLUMN IF NOT EXISTS watchlist_alert_sent BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS watchlist_alert_sent_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS entry_alert_sent BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS entry_alert_sent_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS telegram_alert_sent BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS telegram_alert_sent_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW(),
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_live_setups_setup_key
ON live_setups(setup_key);

CREATE INDEX IF NOT EXISTS idx_live_setups_strategy_type
ON live_setups(strategy_type);

CREATE INDEX IF NOT EXISTS idx_live_setups_alert_stage
ON live_setups(alert_stage);

CREATE INDEX IF NOT EXISTS idx_live_setups_setup_status
ON live_setups(setup_status);

NOTIFY pgrst, 'reload schema';
