"""
AlphaPulse - Strategy Manager
==============================
Runs the DEFAULT MultiTimeframeAnalyzer every scan and returns its signals.

Registered strategy
───────────────────
  "default"  MultiTimeframeAnalyzer  structure levels + rejection-candle setups

Per-scan flow
─────────────
  1. Run DEFAULT strategy  → MarketOutlook (watch levels / H4 bias / context)
                           + DEFAULT trade signals
  2. Classify market condition  (TRENDING / RANGING / VOLATILE / LOW_VOLATILITY)
  3. Record strategy performance score  (transparency only — NO gating)
  4. Return StrategyRunResult with all signals
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config.settings import LEVEL_TOLERANCE_PIPS, LIVE_ENABLED_STRATEGIES, PIP_SIZE
from strategies.engulfing_live import LiveEngulfingAnalyzer
from strategies.multi_timeframe import MultiTimeframeAnalyzer, SetupResult, MarketOutlook
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# MARKET CONDITIONS
# ─────────────────────────────────────────────────────────────────────────────

class MarketCondition:
    """String constants for market regime classification."""
    TRENDING       = "TRENDING"
    RANGING        = "RANGING"
    VOLATILE       = "VOLATILE"
    LOW_VOLATILITY = "LOW_VOLATILITY"


# ─────────────────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class StrategyScore:
    """Historical performance record for the strategy (informational only)."""
    name: str
    raw_score: float    # 0-100 from learning engine win-rate / recency
    trades_seen: int    # trade count backing this score


@dataclass
class StrategySignal:
    """
    Unified signal wrapper for DEFAULT signals.

    strategy_name  "default"
    signal_type    "setup"
    """
    strategy_name: str
    signal_type: str
    confidence: float
    direction: str           # "BUY" | "SELL"
    pair: str
    setup: Optional[SetupResult] = None
    confluence_strategies: List[str] = field(default_factory=list)

    # ── Convenience properties ────────────────────────────────────────────────

    @property
    def level_price(self) -> float:
        if self.setup:
            return self.setup.level.price
        return 0.0

    @property
    def confirmed_at(self):
        """UTC timestamp of the confirmation candle."""
        if self.setup:
            return self.setup.confirmation.candle_time
        return None

    @property
    def tf_pair_str(self) -> str:
        if self.setup:
            return f"{self.setup.higher_tf}-{self.setup.lower_tf}"
        return "UNKNOWN-UNKNOWN"

    @property
    def session_name(self) -> str:
        if self.setup:
            return self.setup.session_name
        return ""

    @property
    def is_swing(self) -> bool:
        return False

    @property
    def trend_aligned(self) -> bool:
        if self.setup:
            return self.setup.trend_aligned
        return True

    @property
    def h4_bias(self) -> str:
        if self.setup:
            return self.setup.h4_bias
        return "neutral"

    def fingerprint(self) -> str:
        """Stable deduplication key — unique per setup across scans."""
        if self.setup:
            s = self.setup
            return (
                f"{self.strategy_name}:{s.setup_type}:{s.direction}:{s.level.level_type}:"
                f"{s.confirmation.entry_price:.2f}:{s.higher_tf}-{s.lower_tf}"
            )
        return f"{self.strategy_name}:unknown:{self.direction}:{self.level_price:.2f}"


@dataclass
class StrategyRunResult:
    """Complete output of one StrategyManager.run() call."""
    outlook: MarketOutlook
    market_condition: str
    strategy_scores: Dict[str, StrategyScore]   # for performance logging only
    signals: List[StrategySignal]               # DEFAULT signals


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class StrategyManager:
    """
    Runs the DEFAULT strategy every scan and returns its signals.

    Usage:
        manager = StrategyManager(learning_engine=learning)
        result  = manager.run(data, current_price)
        # result.signals contains DEFAULT signals
    """

    def __init__(self, learning_engine=None):
        self._analyzer = MultiTimeframeAnalyzer()
        self._engulfing = LiveEngulfingAnalyzer()
        self._learning = learning_engine
        self._enabled = list(LIVE_ENABLED_STRATEGIES)
        logger.info("StrategyManager initialised — enabled live strategies: %s", ", ".join(self._enabled))

    # ─────────────────────────────────────────────────────
    # TIMEFRAMES
    # ─────────────────────────────────────────────────────

    def get_required_timeframes(self) -> List[str]:
        """All timeframes needed by the DEFAULT strategy."""
        required = set(self._analyzer.get_required_timeframes())
        if "engulfing_rejection" in self._enabled:
            required.update({"D1", "H4", "H1", "M30"})
        return sorted(required)

    # ─────────────────────────────────────────────────────
    # MAIN ENTRY POINT
    # ─────────────────────────────────────────────────────

    def run(
        self,
        data: Dict[str, pd.DataFrame],
        current_price: Optional[float] = None,
        analysis_time: Optional[datetime] = None,
    ) -> StrategyRunResult:
        """
        Run the DEFAULT strategy and return its signals.

        Always returns a valid StrategyRunResult even when zero signals are found.
        """

        # ── DEFAULT strategy ──────────────────────────────────────────────────
        self._analyzer.learning_engine = self._learning
        outlook, gap_setups = self._analyzer.analyze(
            data, current_price=current_price, analysis_time=analysis_time
        )
        signals: List[StrategySignal] = []
        if "gap_sweep" in self._enabled:
            signals.extend(_wrap_setups(gap_setups, "gap_sweep"))
        if "engulfing_rejection" in self._enabled:
            engulf_setups = self._engulfing.analyze(
                data,
                pair=outlook.pair,
                current_price=current_price,
                context=outlook.context,
            )
            signals.extend(_wrap_setups(engulf_setups, "engulfing_rejection"))
        signals = self._merge_confluence(signals)

        # ── Market condition (classification only — no gating) ────────────────
        condition = self._detect_condition(data)

        # ── Strategy score (learning transparency — does NOT gate execution) ──
        scores: Dict[str, StrategyScore] = {
            "gap_sweep": self._score_strategy("gap_sweep"),
        }
        if "engulfing_rejection" in self._enabled:
            scores["engulfing_rejection"] = self._score_strategy("engulfing_rejection")

        # ── Logging ───────────────────────────────────────────────────────────
        score_parts = [f"{name}={score.raw_score:.0f}pts/{score.trades_seen}T" for name, score in scores.items()]
        logger.info("Market Condition: %s | %s | %d signal(s)", condition, " | ".join(score_parts), len(signals))

        return StrategyRunResult(
            outlook=outlook,
            market_condition=condition,
            strategy_scores=scores,
            signals=signals,
        )

    # ─────────────────────────────────────────────────────
    # MARKET CONDITION DETECTION
    # ─────────────────────────────────────────────────────

    def _detect_condition(self, data: Dict[str, pd.DataFrame]) -> str:
        """
        Classify market regime from H1 candles (M30 fallback), 20-bar window.

        Priority order:
          1. VOLATILE       last-5 avg range  > 1.5× 20-bar avg
          2. LOW_VOLATILITY last-5 avg range  < 0.6× 20-bar avg
          3. TRENDING       two consecutive HH+HL or LH+LL
          4. RANGING        default
        """
        df = data.get("H1")
        if df is None or (hasattr(df, "empty") and df.empty):
            df = data.get("M30")
        if df is None or len(df) < 20:
            return MarketCondition.RANGING

        window = df.iloc[-20:]
        highs  = window["high"].values.astype(float)
        lows   = window["low"].values.astype(float)
        ranges = highs - lows

        avg_range    = float(np.mean(ranges))
        recent_range = float(np.mean(ranges[-5:]))

        if avg_range < 1e-9:
            return MarketCondition.RANGING

        if recent_range > 1.5 * avg_range:
            return MarketCondition.VOLATILE
        if recent_range < 0.6 * avg_range:
            return MarketCondition.LOW_VOLATILITY

        n  = len(highs)
        sh = [highs[i] for i in range(1, n - 1)
              if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]]
        sl = [lows[i]  for i in range(1, n - 1)
              if lows[i]  < lows[i - 1] and lows[i]  < lows[i + 1]]

        if len(sh) >= 2 and len(sl) >= 2:
            if (sh[-1] > sh[-2] and sl[-1] > sl[-2]) or \
               (sh[-1] < sh[-2] and sl[-1] < sl[-2]):
                return MarketCondition.TRENDING

        return MarketCondition.RANGING

    # ─────────────────────────────────────────────────────
    # STRATEGY SCORING  (learning / transparency only)
    # ─────────────────────────────────────────────────────

    def _score_strategy(self, name: str) -> StrategyScore:
        """
        Retrieve a 0-100 performance score from the learning engine.
        Returns 50 (neutral) when no history is available.
        This score is logged only — it does NOT gate signal execution.
        """
        if self._learning is None:
            return StrategyScore(name=name, raw_score=50.0, trades_seen=0)
        try:
            base, n_trades = self._learning.get_strategy_score(name)
            return StrategyScore(
                name=name,
                raw_score=round(base * 100.0, 1),
                trades_seen=n_trades,
            )
        except Exception as exc:
            logger.warning("Score lookup failed for '%s': %s", name, exc)
            return StrategyScore(name=name, raw_score=50.0, trades_seen=0)

    @staticmethod
    def _merge_confluence(signals: List[StrategySignal]) -> List[StrategySignal]:
        if not signals:
            return signals
        tolerance = LEVEL_TOLERANCE_PIPS * PIP_SIZE * 2.0
        merged: List[StrategySignal] = []
        for signal in sorted(signals, key=lambda s: s.confidence, reverse=True):
            existing = next(
                (
                    item for item in merged
                    if item.pair == signal.pair
                    and item.direction == signal.direction
                    and abs(item.level_price - signal.level_price) <= tolerance
                ),
                None,
            )
            if existing is None:
                merged.append(signal)
                continue
            combined = {existing.strategy_name, signal.strategy_name, *existing.confluence_strategies, *signal.confluence_strategies}
            if signal.confidence > existing.confidence:
                signal.confluence_strategies = sorted(s for s in combined if s != signal.strategy_name)
                if signal.setup is not None:
                    signal.setup.confluence_with = list(signal.confluence_strategies)
                merged[merged.index(existing)] = signal
            else:
                existing.confluence_strategies = sorted(s for s in combined if s != existing.strategy_name)
                if existing.setup is not None:
                    existing.setup.confluence_with = list(existing.confluence_strategies)
        return merged


# ─────────────────────────────────────────────────────────────────────────────
# MODULE-LEVEL HELPER
# ─────────────────────────────────────────────────────────────────────────────

def _wrap_setups(setups: List[SetupResult], strategy_name: str) -> List[StrategySignal]:
    wrapped: List[StrategySignal] = []
    for setup in setups:
        setup.strategy_type = strategy_name
        wrapped.append(
            StrategySignal(
                strategy_name=strategy_name,
                signal_type="setup",
                confidence=setup.confidence,
                direction=setup.direction,
                pair=setup.pair,
                setup=setup,
            )
        )
    return wrapped
