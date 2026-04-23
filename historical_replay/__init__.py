"""
Historical replay subsystem for AlphaPulse.

This package is intentionally separate from the live scan/Telegram/trade
tracking flow. It reuses the current strategy stack and stores replay output
in Supabase replay tables.
"""

import os

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from historical_replay.engine import HistoricalReplayEngine

__all__ = ["HistoricalReplayEngine"]
