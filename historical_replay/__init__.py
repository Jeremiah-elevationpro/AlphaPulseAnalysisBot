"""
Historical replay subsystem for AlphaPulse.

This package is intentionally separate from the live scan/Telegram/trade
tracking flow. It reuses the current strategy stack and stores replay output
in Supabase replay tables.
"""

import os

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from historical_replay.engine import HistoricalReplayEngine
from historical_replay.engulfing_research import EngulfingResearchEngine
from historical_replay.break_retest_research import BreakRetestResearchEngine
from historical_replay.multi_strategy_engine import MultiStrategyReplayEngine

__all__ = [
    "HistoricalReplayEngine",
    "EngulfingResearchEngine",
    "BreakRetestResearchEngine",
    "MultiStrategyReplayEngine",
]
