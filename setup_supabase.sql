-- ============================================================
-- AlphaPulse — Supabase Schema Setup
-- Run this ONCE in your Supabase SQL Editor:
--   Dashboard → SQL Editor → New query → paste → Run
-- ============================================================

-- Trades
CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL PRIMARY KEY,
    trade_uuid      UUID UNIQUE NOT NULL,
    setup_type      TEXT NOT NULL DEFAULT 'major',
    pair            TEXT NOT NULL DEFAULT 'XAUUSD',
    direction       TEXT NOT NULL,
    entry_price     NUMERIC(10,2) NOT NULL,
    sl_price        NUMERIC(10,2) NOT NULL,
    tp1             NUMERIC(10,2),
    tp2             NUMERIC(10,2),
    tp3             NUMERIC(10,2),
    tp4             NUMERIC(10,2),
    tp5             NUMERIC(10,2),
    tp1_hit         BOOLEAN DEFAULT FALSE,
    tp2_hit         BOOLEAN DEFAULT FALSE,
    tp3_hit         BOOLEAN DEFAULT FALSE,
    tp4_hit         BOOLEAN DEFAULT FALSE,
    tp5_hit         BOOLEAN DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'PENDING',
    level_type      TEXT,
    level_price     NUMERIC(10,2),
    higher_tf       TEXT,
    lower_tf        TEXT,
    result          TEXT,
    confidence      NUMERIC(5,3) DEFAULT 0.5,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    activated_at    TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ,
    be_moved        BOOLEAN DEFAULT FALSE,
    tp_progress_reached INT DEFAULT 0,
    protected_after_tp1 BOOLEAN DEFAULT FALSE,
    tp1_alert_sent  BOOLEAN DEFAULT FALSE,
    breakeven_exit  BOOLEAN DEFAULT FALSE,
    notes           TEXT
);

-- Performance stats
CREATE TABLE IF NOT EXISTS performance_stats (
    id              BIGSERIAL PRIMARY KEY,
    level_type      TEXT,
    tf_pair         TEXT,
    wins            INT DEFAULT 0,
    losses          INT DEFAULT 0,
    total_trades    INT DEFAULT 0,
    win_rate        NUMERIC(5,3) DEFAULT 0.5,
    reward_score    NUMERIC(8,3) DEFAULT 0.0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(level_type, tf_pair)
);

-- Daily summaries
CREATE TABLE IF NOT EXISTS daily_summaries (
    id              BIGSERIAL PRIMARY KEY,
    summary_date    DATE UNIQUE NOT NULL,
    total_setups    INT DEFAULT 0,
    activated       INT DEFAULT 0,
    wins            INT DEFAULT 0,
    losses          INT DEFAULT 0,
    win_rate        NUMERIC(5,3) DEFAULT 0.0,
    best_level_type TEXT,
    best_tf_pair    TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Confidence scores (RL learning system)
CREATE TABLE IF NOT EXISTS confidence_scores (
    id              BIGSERIAL PRIMARY KEY,
    level_type      TEXT NOT NULL,
    tf_pair         TEXT NOT NULL,
    score           NUMERIC(5,3) DEFAULT 0.5,
    reward_total    NUMERIC(8,3) DEFAULT 0.0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(level_type, tf_pair)
);

-- Manual setups entered from the frontend and later consumed by the bot
CREATE TABLE IF NOT EXISTS manual_setups (
    id                              BIGSERIAL PRIMARY KEY,
    symbol                          TEXT NOT NULL DEFAULT 'XAUUSD',
    direction                       TEXT NOT NULL,
    timeframe_pair                  TEXT NOT NULL,
    entry_price                     NUMERIC(10,2) NOT NULL,
    stop_loss                       NUMERIC(10,2) NOT NULL,
    tp1                             NUMERIC(10,2) NOT NULL,
    tp2                             NUMERIC(10,2),
    tp3                             NUMERIC(10,2),
    bias                            TEXT NOT NULL,
    confirmation_type               TEXT NOT NULL,
    session                         TEXT NOT NULL,
    notes                           TEXT DEFAULT '',
    activation_mode                 TEXT NOT NULL,
    move_sl_to_be_after_tp1         BOOLEAN DEFAULT FALSE,
    enable_telegram_alerts          BOOLEAN DEFAULT FALSE,
    high_priority                   BOOLEAN DEFAULT FALSE,
    status                          TEXT NOT NULL DEFAULT 'draft',
    created_at                      TIMESTAMPTZ DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ DEFAULT NOW()
);

-- Disable RLS so the service-role key can read/write freely
ALTER TABLE trades            DISABLE ROW LEVEL SECURITY;
ALTER TABLE performance_stats DISABLE ROW LEVEL SECURITY;
ALTER TABLE daily_summaries   DISABLE ROW LEVEL SECURITY;
ALTER TABLE confidence_scores DISABLE ROW LEVEL SECURITY;
ALTER TABLE manual_setups     DISABLE ROW LEVEL SECURITY;
