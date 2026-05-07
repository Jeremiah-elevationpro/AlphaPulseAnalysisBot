-- ============================================================
-- AlphaPulse — Multi-Strategy Learning Migration
-- Safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS)
-- Run in Supabase SQL Editor
-- ============================================================

-- ─────────────────────────────────────────────────────────────
-- 1. multi_strategy_replay_runs
--    One row per combined replay run across all strategies.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS multi_strategy_replay_runs (
    id                  BIGSERIAL PRIMARY KEY,
    symbol              TEXT          NOT NULL DEFAULT 'XAUUSD',
    strategies          TEXT[]        NOT NULL DEFAULT '{}',
    months_tested       INTEGER,
    replay_start        TIMESTAMPTZ,
    replay_end          TIMESTAMPTZ,
    status              TEXT          DEFAULT 'running',
    total_trades        INTEGER       DEFAULT 0,
    wins                INTEGER       DEFAULT 0,
    losses              INTEGER       DEFAULT 0,
    win_rate            NUMERIC(8,2),
    tp1_rate            NUMERIC(8,2),
    tp2_rate            NUMERIC(8,2),
    tp3_rate            NUMERIC(8,2),
    net_pips            NUMERIC(12,2),
    avg_pips            NUMERIC(12,2),
    strategy_summary    JSONB         DEFAULT '{}'::jsonb,
    session_summary     JSONB         DEFAULT '{}'::jsonb,
    confluence_summary  JSONB         DEFAULT '{}'::jsonb,
    learning_summary    JSONB         DEFAULT '{}'::jsonb,
    notes               TEXT,
    created_at          TIMESTAMPTZ   DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);

-- ─────────────────────────────────────────────────────────────
-- 2. multi_strategy_replay_trades
--    All trades from all strategies in a single replay run.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS multi_strategy_replay_trades (
    id                              BIGSERIAL PRIMARY KEY,
    multi_run_id                    BIGINT REFERENCES multi_strategy_replay_runs(id) ON DELETE CASCADE,

    source                          TEXT          DEFAULT 'multi_strategy_replay',
    symbol                          TEXT          NOT NULL DEFAULT 'XAUUSD',

    strategy_type                   TEXT          NOT NULL,
    setup_type                      TEXT,
    confirmation_type               TEXT,
    confirmation_score              NUMERIC(8,2),

    direction                       TEXT          NOT NULL,
    timeframe                       TEXT,
    timeframe_pair                  TEXT,
    session_name                    TEXT,
    market_condition                TEXT,

    dominant_bias                   TEXT,
    bias_strength                   TEXT,
    d1_bias                         TEXT,
    h4_bias                         TEXT,
    h1_bias                         TEXT,

    level_type                      TEXT,
    level_price                     NUMERIC(12,3),
    level_high                      NUMERIC(12,3),
    level_low                       NUMERIC(12,3),
    level_mid                       NUMERIC(12,3),

    quality_score                   NUMERIC(8,2),
    quality_rejection_count         INTEGER,
    structure_break_count           INTEGER,

    confluence                      BOOLEAN       DEFAULT FALSE,
    confluence_strategy_types       TEXT[]        DEFAULT '{}',
    confluence_level_distance_pips  NUMERIC(10,2),

    entry                           NUMERIC(12,3),
    sl                              NUMERIC(12,3),
    tp1                             NUMERIC(12,3),
    tp2                             NUMERIC(12,3),
    tp3                             NUMERIC(12,3),

    sl_pips                         NUMERIC(10,2),
    tp1_pips                        NUMERIC(10,2),
    tp2_pips                        NUMERIC(10,2),
    tp3_pips                        NUMERIC(10,2),

    activated_at                    TIMESTAMPTZ,
    closed_at                       TIMESTAMPTZ,

    final_result                    TEXT,
    tp_progress                     INTEGER       DEFAULT 0,
    protected_after_tp1             BOOLEAN       DEFAULT FALSE,
    final_pips                      NUMERIC(12,2),
    reward_score                    NUMERIC(8,2),

    learning_feature_key            TEXT,
    learning_weight                 NUMERIC(8,3),
    failure_reason                  TEXT,

    created_at                      TIMESTAMPTZ   DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- 3. strategy_learning_profiles
--    Spencer's learned performance profile per strategy/context.
-- ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS strategy_learning_profiles (
    id                      BIGSERIAL PRIMARY KEY,

    strategy_type           TEXT          NOT NULL,
    symbol                  TEXT          NOT NULL DEFAULT 'XAUUSD',

    session_name            TEXT,
    timeframe               TEXT,
    direction               TEXT,
    dominant_bias           TEXT,
    bias_strength           TEXT,
    confirmation_type       TEXT,
    level_type              TEXT,

    sample_size             INTEGER       DEFAULT 0,
    wins                    INTEGER       DEFAULT 0,
    losses                  INTEGER       DEFAULT 0,

    win_rate                NUMERIC(8,2),
    tp1_rate                NUMERIC(8,2),
    tp2_rate                NUMERIC(8,2),
    tp3_rate                NUMERIC(8,2),

    net_pips                NUMERIC(12,2),
    avg_pips                NUMERIC(12,2),
    reward_score_avg        NUMERIC(8,2),

    confidence_tier         TEXT,
    recommended_weight      NUMERIC(8,3),

    best_session            BOOLEAN       DEFAULT FALSE,
    worst_session           BOOLEAN       DEFAULT FALSE,

    last_replay_run_id      BIGINT,
    last_multi_run_id       BIGINT,

    profile_key             TEXT          UNIQUE,

    created_at              TIMESTAMPTZ   DEFAULT NOW(),
    updated_at              TIMESTAMPTZ   DEFAULT NOW()
);

-- ─────────────────────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_multi_strategy_replay_trades_run
    ON multi_strategy_replay_trades(multi_run_id);

CREATE INDEX IF NOT EXISTS idx_multi_strategy_replay_trades_strategy
    ON multi_strategy_replay_trades(strategy_type);

CREATE INDEX IF NOT EXISTS idx_multi_strategy_replay_trades_symbol
    ON multi_strategy_replay_trades(symbol);

CREATE INDEX IF NOT EXISTS idx_multi_strategy_replay_trades_session
    ON multi_strategy_replay_trades(session_name);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_profiles_strategy
    ON strategy_learning_profiles(strategy_type);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_profiles_symbol
    ON strategy_learning_profiles(symbol);

CREATE INDEX IF NOT EXISTS idx_strategy_learning_profiles_profile_key
    ON strategy_learning_profiles(profile_key);

-- Reload PostgREST schema cache
NOTIFY pgrst, 'reload schema';
