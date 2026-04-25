ALTER TABLE strategy_research_stats
ADD COLUMN IF NOT EXISTS research_run_id BIGINT,
ADD COLUMN IF NOT EXISTS run_id BIGINT,
ADD COLUMN IF NOT EXISTS strategy_type TEXT,
ADD COLUMN IF NOT EXISTS symbol TEXT,
ADD COLUMN IF NOT EXISTS stats_key TEXT,
ADD COLUMN IF NOT EXISTS stats_value JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS payload JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS funnel_summary JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS reject_summary JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT NOW();

ALTER TABLE strategy_research_runs
ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'running',
ADD COLUMN IF NOT EXISTS finished_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS notes TEXT,
ADD COLUMN IF NOT EXISTS funnel_summary JSONB DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS reject_summary JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_strategy_research_stats_run_id
ON strategy_research_stats(run_id);

CREATE INDEX IF NOT EXISTS idx_strategy_research_stats_research_run_id
ON strategy_research_stats(research_run_id);

CREATE INDEX IF NOT EXISTS idx_strategy_research_stats_strategy
ON strategy_research_stats(strategy_type);

CREATE INDEX IF NOT EXISTS idx_strategy_research_stats_symbol
ON strategy_research_stats(symbol);

NOTIFY pgrst, 'reload schema';
