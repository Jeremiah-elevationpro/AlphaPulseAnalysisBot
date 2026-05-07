-- ============================================================
-- AlphaPulse Historical Replay Supabase Tables
-- Run in Supabase SQL Editor before running:
--   python -m historical_replay.run --months 2
-- ============================================================

CREATE TABLE IF NOT EXISTS historical_replay_runs (
    id                          BIGSERIAL PRIMARY KEY,
    symbol                      TEXT NOT NULL DEFAULT 'XAUUSD',
    source                      TEXT NOT NULL DEFAULT 'historical_replay',
    strategy_version            TEXT NOT NULL DEFAULT 'current_alpha_pulse',
    status                      TEXT NOT NULL DEFAULT 'running',
    started_at                  TIMESTAMPTZ DEFAULT NOW(),
    finished_at                 TIMESTAMPTZ,
    replay_start                TIMESTAMPTZ NOT NULL,
    replay_end                  TIMESTAMPTZ NOT NULL,
    total_watchlists            INT DEFAULT 0,
    total_pending_order_ready   INT DEFAULT 0,
    total_activated_trades      INT DEFAULT 0,
    total_wins                  INT DEFAULT 0,
    total_losses                INT DEFAULT 0,
    notes                       TEXT
);

CREATE TABLE IF NOT EXISTS historical_replay_trades (
    id                          BIGSERIAL PRIMARY KEY,
    replay_run_id               BIGINT REFERENCES historical_replay_runs(id) ON DELETE CASCADE,
    source                      TEXT NOT NULL DEFAULT 'historical_replay',
    symbol                      TEXT NOT NULL DEFAULT 'XAUUSD',
    timestamp                   TIMESTAMPTZ,
    direction                   TEXT NOT NULL,
    setup_type                  TEXT,
    level_type                  TEXT,
    timeframe_pair              TEXT,
    dominant_bias               TEXT,
    bias_strength               TEXT,
    h1_state                    TEXT,
    confirmation_pattern        TEXT,
    micro_confirmation_type     TEXT,
    micro_confirmation_score    NUMERIC(8,2),
    micro_layer_decision        TEXT,
    h1_liquidity_sweep          BOOLEAN DEFAULT FALSE,
    h1_sweep_direction          TEXT,
    h1_reclaim_confirmed        BOOLEAN DEFAULT FALSE,
    pd_location                 TEXT,
    pd_filter_score             NUMERIC(8,2),
    bias_gate_result            TEXT,
    high_quality_trade          BOOLEAN DEFAULT FALSE,
    micro_strength              TEXT,
    pending_order_ready_time    TIMESTAMPTZ,
    activation_time             TIMESTAMPTZ,
    entry                       NUMERIC(10,2),
    sl                          NUMERIC(10,2),
    tp1                         NUMERIC(10,2),
    tp2                         NUMERIC(10,2),
    tp3                         NUMERIC(10,2),
    tp4                         NUMERIC(10,2),
    tp5                         NUMERIC(10,2),
    final_result                TEXT,
    tp_progress                 INT DEFAULT 0,
    tp_progress_reached         INT DEFAULT 0,
    protected_after_tp1         BOOLEAN DEFAULT FALSE,
    tp1_alert_sent              BOOLEAN DEFAULT FALSE,
    breakeven_exit              BOOLEAN DEFAULT FALSE,
    pips_to_tp1                 NUMERIC(10,2),
    pips_to_tp2                 NUMERIC(10,2),
    pips_to_tp3                 NUMERIC(10,2),
    pips_to_tp4                 NUMERIC(10,2),
    pips_to_tp5                 NUMERIC(10,2),
    realized_pips               NUMERIC(10,2),
    max_potential_pips          NUMERIC(10,2),
    final_pips                  NUMERIC(10,2),
    max_favorable_excursion     NUMERIC(10,2),
    max_adverse_excursion       NUMERIC(10,2),
    market_condition            TEXT,
    session                     TEXT,
    reward_score                NUMERIC(8,3),
    failure_reason              TEXT,
    created_at                  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS historical_replay_stats (
    id                              BIGSERIAL PRIMARY KEY,
    replay_run_id                   BIGINT REFERENCES historical_replay_runs(id) ON DELETE CASCADE,
    total_watchlists                INT DEFAULT 0,
    total_pending_order_ready       INT DEFAULT 0,
    total_activated_trades          INT DEFAULT 0,
    total_wins                      INT DEFAULT 0,
    total_losses                    INT DEFAULT 0,
    tp1_hit_rate                    NUMERIC(6,3) DEFAULT 0,
    tp2_hit_rate                    NUMERIC(6,3) DEFAULT 0,
    tp3_hit_rate                    NUMERIC(6,3) DEFAULT 0,
    performance_by_timeframe_pair   JSONB DEFAULT '{}'::jsonb,
    performance_by_bias             JSONB DEFAULT '{}'::jsonb,
    performance_by_session          JSONB DEFAULT '{}'::jsonb,
    performance_by_setup_type       JSONB DEFAULT '{}'::jsonb,
    performance_by_micro_confirmation JSONB DEFAULT '{}'::jsonb,
    performance_by_h1_sweep         JSONB DEFAULT '{}'::jsonb,
    performance_by_pd_location      JSONB DEFAULT '{}'::jsonb,
    performance_by_bias_gate        JSONB DEFAULT '{}'::jsonb,
    pip_summary                     JSONB DEFAULT '{}'::jsonb,
    pip_by_timeframe_pair           JSONB DEFAULT '{}'::jsonb,
    pip_by_bias                     JSONB DEFAULT '{}'::jsonb,
    pip_by_session                  JSONB DEFAULT '{}'::jsonb,
    created_at                      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_replay_trades_run ON historical_replay_trades(replay_run_id);
CREATE INDEX IF NOT EXISTS idx_replay_trades_tf ON historical_replay_trades(timeframe_pair);
CREATE INDEX IF NOT EXISTS idx_replay_trades_bias ON historical_replay_trades(dominant_bias);
CREATE INDEX IF NOT EXISTS idx_replay_stats_run ON historical_replay_stats(replay_run_id);

ALTER TABLE historical_replay_runs   DISABLE ROW LEVEL SECURITY;
ALTER TABLE historical_replay_trades DISABLE ROW LEVEL SECURITY;
ALTER TABLE historical_replay_stats  DISABLE ROW LEVEL SECURITY;
