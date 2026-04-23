"""
AlphaPulse - Phase 1: Statistical Learning Engine
====================================================
Aggregates historical trade results and computes:
  - Win rate per level type (A, V, Gap)
  - Win rate per timeframe pair (H4-H1, H1-M30, M30-M15)
  - Best performing setups overall
  - Average TPs reached per setup type

These statistics directly feed the confidence score computation.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from db.database import Database
from db.models import TradeResult
from config.settings import ACTIVE_TIMEFRAME_PAIR_LABELS
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SetupStats:
    """Statistics for a specific (level_type, tf_pair) combination."""
    level_type: str
    tf_pair: str
    wins: int = 0
    losses: int = 0
    total_trades: int = 0
    avg_tps_hit: float = 0.0
    win_rate: float = 0.5

    @property
    def key(self) -> str:
        return f"{self.level_type}|{self.tf_pair}"

    def update(self, result: str, tps_hit: int):
        self.total_trades += 1
        if result in (
            TradeResult.PARTIAL_WIN,
            TradeResult.BREAKEVEN_WIN,
            TradeResult.WIN,
            TradeResult.STRONG_WIN,
        ):
            self.wins += 1
        else:
            self.losses += 1
        # Rolling average TPs hit
        self.avg_tps_hit = (
            (self.avg_tps_hit * (self.total_trades - 1) + tps_hit)
            / self.total_trades
        )
        self.win_rate = self.wins / self.total_trades if self.total_trades > 0 else 0.5


class StatisticalLearner:
    """
    Phase 1 learning: pure statistics, no ML.
    Reads closed trades from the DB and computes win rates.
    """

    def __init__(self, db: Database):
        self._db = db
        self._stats: Dict[str, SetupStats] = {}
        self._best_setup: Optional[str] = None

    def refresh(self):
        """
        Re-read all closed trades from DB and recompute statistics.
        Should be called periodically (e.g., every hour or after each closed trade).
        """
        try:
            closed_trades = self._db.get_all_closed_trades()
            self._compute_stats(closed_trades)
            self._persist_stats()
            logger.info("Statistical learner refreshed — %d setups tracked.",
                        len(self._stats))
        except Exception as e:
            logger.error("StatisticalLearner.refresh() failed: %s", e)

    def _compute_stats(self, trades: list):
        """Recompute all statistics from scratch using all available trade features."""
        self._stats.clear()

        for row in trades:
            try:
                if isinstance(row, dict):
                    # Supabase / modern dict format — richest data
                    result     = row.get("result")
                    level_type = row.get("level_type", "")
                    higher_tf  = row.get("higher_tf", "")
                    lower_tf   = row.get("lower_tf", "")
                    # tp_hit from individual boolean columns
                    tps_hit = sum(
                        1 for col in ("tp1_hit","tp2_hit","tp3_hit","tp4_hit","tp5_hit")
                        if row.get(col)
                    )
                elif isinstance(row, tuple) and len(row) >= 17:
                    # PostgreSQL tuple — column order matches CREATE TABLE
                    result     = row[21] if len(row) > 21 else None
                    level_type = row[17] if len(row) > 17 else ""
                    higher_tf  = row[19] if len(row) > 19 else ""
                    lower_tf   = row[20] if len(row) > 20 else ""
                    tps_hit    = sum(1 for i in range(11, 16)
                                     if len(row) > i and row[i])
                else:
                    continue

                if not result or not level_type or not higher_tf or not lower_tf:
                    continue

                tf_pair = f"{higher_tf}-{lower_tf}"
                if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
                    logger.debug("Skipping disabled timeframe pair in stats: %s", tf_pair)
                    continue
                key     = f"{level_type}|{tf_pair}"

                if key not in self._stats:
                    self._stats[key] = SetupStats(level_type=level_type, tf_pair=tf_pair)
                self._stats[key].update(result, tps_hit)

            except Exception as e:
                logger.debug("Skipping malformed trade row: %s", e)

        # Find best setup (min 3 trades to qualify)
        qualified = [s for s in self._stats.values() if s.total_trades >= 3]
        if qualified:
            best = max(qualified, key=lambda s: (s.win_rate, s.total_trades))
            self._best_setup = (
                f"{best.level_type} on {best.tf_pair} "
                f"({best.win_rate*100:.0f}% WR, {best.total_trades} trades)"
            )
        elif self._stats:
            best = max(self._stats.values(), key=lambda s: s.total_trades)
            self._best_setup = f"{best.level_type} on {best.tf_pair} (early data)"

    def _persist_stats(self):
        """Write aggregated stats back to the database."""
        for stats in self._stats.values():
            try:
                self._db.upsert_performance(
                    level_type=stats.level_type,
                    tf_pair=stats.tf_pair,
                    wins=stats.wins,
                    losses=stats.losses,
                    reward=float(stats.wins - stats.losses),
                )
            except Exception as e:
                logger.error("Failed to persist stats for %s: %s", stats.key, e)

    # ─────────────────────────────────────────────────────
    # QUERY INTERFACE
    # ─────────────────────────────────────────────────────

    def get_win_rate(self, level_type: str, tf_pair: str) -> float:
        key = f"{level_type}|{tf_pair}"
        return self._stats[key].win_rate if key in self._stats else 0.5

    def get_stats(self, level_type: str, tf_pair: str) -> Optional[SetupStats]:
        key = f"{level_type}|{tf_pair}"
        return self._stats.get(key)

    def get_all_stats(self) -> List[SetupStats]:
        return list(self._stats.values())

    def get_best_setup_str(self) -> str:
        return self._best_setup or "Insufficient data"

    def get_leaderboard(self) -> List[SetupStats]:
        """Return stats sorted by win_rate descending (min 5 trades)."""
        qualified = [s for s in self._stats.values() if s.total_trades >= 5]
        return sorted(qualified, key=lambda s: s.win_rate, reverse=True)
