CREATE TABLE IF NOT EXISTS strategy_learning_trades (
id BIGSERIAL PRIMARY KEY,

source TEXT NOT NULL DEFAULT 'replay',
source_run_id BIGINT,
strategy_type TEXT NOT NULL,
setup_type TEXT,

symbol TEXT NOT NULL DEFAULT 'XAUUSD',
direction TEXT,
timeframe TEXT,
timeframe_pair TEXT,
session_name TEXT,
market_condition TEXT,

dominant_bias TEXT,
bias_strength TEXT,

confirmation_type TEXT,
confirmation_score NUMERIC(8,2),

level_type TEXT,
level_price NUMERIC(12,3),
level_high NUMERIC(12,3),
level_low NUMERIC(12,3),
level_mid NUMERIC(12,3),

entry NUMERIC(12,3),
sl NUMERIC(12,3),
tp1 NUMERIC(12,3),
tp2 NUMERIC(12,3),
tp3 NUMERIC(12,3),

final_result TEXT,
final_pips NUMERIC(12,2),
reward_score NUMERIC(8,2),

tp_progress INTEGER DEFAULT 0,
protected_after_tp1 BOOLEAN DEFAULT FALSE,

activated_at TIMESTAMPTZ,
closed_at TIMESTAMPTZ,

quality_rejection_count INTEGER,
structure_break_count INTEGER,
pd_location TEXT,

break_level NUMERIC(12,3),
break_distance_pips NUMERIC(10,2),
retest_level NUMERIC(12,3),
retest_confirmation_type TEXT,

original_engulf_high NUMERIC(12,3),
original_engulf_low NUMERIC(12,3),
original_engulf_direction TEXT,

learning_valid BOOLEAN DEFAULT TRUE,
validation_warning TEXT,

created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE strategy_learning_trades
ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'replay',
ADD COLUMN IF NOT EXISTS source_run_id BIGINT,
ADD COLUMN IF NOT EXISTS strategy_type TEXT,
ADD COLUMN IF NOT EXISTS setup_type TEXT,
ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT 'XAUUSD',
ADD COLUMN IF NOT EXISTS direction TEXT,
ADD COLUMN IF NOT EXISTS timeframe TEXT,
ADD COLUMN IF NOT EXISTS timeframe_pair TEXT,
ADD COLUMN IF NOT EXISTS session_name TEXT,
ADD COLUMN IF NOT EXISTS market_condition TEXT,
ADD COLUMN IF NOT EXISTS dominant_bias TEXT,
ADD COLUMN IF NOT EXISTS bias_strength TEXT,
ADD COLUMN IF NOT EXISTS confirmation_type TEXT,
ADD COLUMN IF NOT EXISTS confirmation_score NUMERIC(8,2),
ADD COLUMN IF NOT EXISTS level_type TEXT,
ADD COLUMN IF NOT EXISTS level_price NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS level_high NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS level_low NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS level_mid NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS entry NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS sl NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS tp1 NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS tp2 NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS tp3 NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS final_result TEXT,
ADD COLUMN IF NOT EXISTS final_pips NUMERIC(12,2),
ADD COLUMN IF NOT EXISTS reward_score NUMERIC(8,2),
ADD COLUMN IF NOT EXISTS tp_progress INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS protected_after_tp1 BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS quality_rejection_count INTEGER,
ADD COLUMN IF NOT EXISTS structure_break_count INTEGER,
ADD COLUMN IF NOT EXISTS pd_location TEXT,
ADD COLUMN IF NOT EXISTS break_level NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS break_distance_pips NUMERIC(10,2),
ADD COLUMN IF NOT EXISTS retest_level NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS retest_confirmation_type TEXT,
ADD COLUMN IF NOT EXISTS original_engulf_high NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS original_engulf_low NUMERIC(12,3),
ADD COLUMN IF NOT EXISTS original_engulf_direction TEXT,
ADD COLUMN IF NOT EXISTS learning_valid BOOLEAN DEFAULT TRUE,
ADD COLUMN IF NOT EXISTS validation_warning TEXT,
ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

CREATE INDEX IF NOT EXISTS idx_strategy_learning_trades_strategy
ON strategy_learning_trades(strategy_type);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_trades_source
ON strategy_learning_trades(source);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_trades_session
ON strategy_learning_trades(session_name);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_trades_timeframe
ON strategy_learning_trades(timeframe);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_trades_bias
ON strategy_learning_trades(dominant_bias, bias_strength);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_trades_result
ON strategy_learning_trades(final_result);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_trades_source_run
ON strategy_learning_trades(source_run_id);

CREATE TABLE IF NOT EXISTS strategy_learning_profiles (
id BIGSERIAL PRIMARY KEY,
profile_key TEXT UNIQUE,
strategy_type TEXT NOT NULL,
symbol TEXT DEFAULT 'XAUUSD',
session_name TEXT,
timeframe TEXT,
direction TEXT,
dominant_bias TEXT,
bias_strength TEXT,
confirmation_type TEXT,
sample_size INTEGER DEFAULT 0,
wins INTEGER DEFAULT 0,
losses INTEGER DEFAULT 0,
win_rate NUMERIC(8,4) DEFAULT 0,
tp1_rate NUMERIC(8,4) DEFAULT 0,
net_pips NUMERIC(12,2) DEFAULT 0,
avg_pips NUMERIC(12,2) DEFAULT 0,
reward_score_avg NUMERIC(12,2) DEFAULT 0,
recommended_weight NUMERIC(8,4) DEFAULT 0.5,
confidence_tier TEXT DEFAULT 'insufficient',
last_multi_run_id BIGINT,
last_updated TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE strategy_learning_profiles
ADD COLUMN IF NOT EXISTS profile_key TEXT,
ADD COLUMN IF NOT EXISTS strategy_type TEXT,
ADD COLUMN IF NOT EXISTS symbol TEXT DEFAULT 'XAUUSD',
ADD COLUMN IF NOT EXISTS session_name TEXT,
ADD COLUMN IF NOT EXISTS timeframe TEXT,
ADD COLUMN IF NOT EXISTS direction TEXT,
ADD COLUMN IF NOT EXISTS dominant_bias TEXT,
ADD COLUMN IF NOT EXISTS bias_strength TEXT,
ADD COLUMN IF NOT EXISTS confirmation_type TEXT,
ADD COLUMN IF NOT EXISTS sample_size INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS wins INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS losses INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS win_rate NUMERIC(8,4) DEFAULT 0,
ADD COLUMN IF NOT EXISTS tp1_rate NUMERIC(8,4) DEFAULT 0,
ADD COLUMN IF NOT EXISTS net_pips NUMERIC(12,2) DEFAULT 0,
ADD COLUMN IF NOT EXISTS avg_pips NUMERIC(12,2) DEFAULT 0,
ADD COLUMN IF NOT EXISTS reward_score_avg NUMERIC(12,2) DEFAULT 0,
ADD COLUMN IF NOT EXISTS recommended_weight NUMERIC(8,4) DEFAULT 0.5,
ADD COLUMN IF NOT EXISTS confidence_tier TEXT DEFAULT 'insufficient',
ADD COLUMN IF NOT EXISTS last_multi_run_id BIGINT,
ADD COLUMN IF NOT EXISTS last_updated TIMESTAMPTZ DEFAULT NOW();

CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_learning_profiles_profile_key
ON strategy_learning_profiles(profile_key);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_profiles_strategy
ON strategy_learning_profiles(strategy_type);

NOTIFY pgrst, 'reload schema';
