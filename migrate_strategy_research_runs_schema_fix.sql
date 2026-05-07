ALTER TABLE strategy_research_runs
ADD COLUMN IF NOT EXISTS strategy_type TEXT,
ADD COLUMN IF NOT EXISTS strategy_group TEXT,
ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS replay_start TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS replay_end TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'running',
ADD COLUMN IF NOT EXISTS error_message TEXT,
ADD COLUMN IF NOT EXISTS export_learning BOOLEAN DEFAULT FALSE,
ADD COLUMN IF NOT EXISTS rows_exported INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS learning_valid_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS learning_skipped_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS summary JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS funnel_summary JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS reject_summary JSONB DEFAULT '{}'::jsonb;

ALTER TABLE strategy_research_runs
ALTER COLUMN strategy_group DROP NOT NULL;

UPDATE strategy_research_runs
SET strategy_group = COALESCE(strategy_group, strategy_type, 'strategy_research')
WHERE strategy_group IS NULL;

NOTIFY pgrst, 'reload schema';
