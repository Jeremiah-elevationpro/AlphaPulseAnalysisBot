-- Add engulf zone level columns to strategy_research_trades.
-- Required for failed_engulf_break_retest to read zone boundaries
-- stored by EngulfingResearchEngine without falling back to notes JSON.

ALTER TABLE strategy_research_trades
    ADD COLUMN IF NOT EXISTS engulf_high  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS engulf_low   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS engulf_mid   DOUBLE PRECISION;

NOTIFY pgrst, 'reload schema';
