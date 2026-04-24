CREATE TABLE IF NOT EXISTS manual_setups (
    id                              BIGSERIAL PRIMARY KEY,
    symbol                          VARCHAR(20) NOT NULL DEFAULT 'XAUUSD',
    direction                       VARCHAR(10) NOT NULL,
    timeframe_pair                  VARCHAR(20) NOT NULL,
    entry_price                     NUMERIC(12,2) NOT NULL,
    stop_loss                       NUMERIC(12,2) NOT NULL,
    tp1                             NUMERIC(12,2) NOT NULL,
    tp2                             NUMERIC(12,2),
    tp3                             NUMERIC(12,2),
    bias                            VARCHAR(40),
    confirmation_type               VARCHAR(50),
    session                         VARCHAR(30),
    notes                           TEXT,
    activation_mode                 VARCHAR(50),
    move_sl_to_be_after_tp1         BOOLEAN DEFAULT TRUE,
    enable_telegram_alerts          BOOLEAN DEFAULT TRUE,
    high_priority                   BOOLEAN DEFAULT FALSE,
    status                          VARCHAR(30) NOT NULL DEFAULT 'draft',
    created_at                      TIMESTAMPTZ DEFAULT NOW(),
    updated_at                      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE manual_setups DISABLE ROW LEVEL SECURITY;
