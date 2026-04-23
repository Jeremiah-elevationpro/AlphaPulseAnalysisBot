"""
AlphaPulse - Lower-Timeframe Micro-Confirmation
================================================
M15 remains the primary confirmation timeframe. This module inspects M5 first,
then optionally M1 when M5 is neutral, to add a small confidence adjustment or
block clear lower-timeframe contradiction near the level.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from config.settings import (
    MICRO_CONFIRMATION_AOI_PIPS,
    MICRO_CONFIRMATION_LOOKBACK,
    MICRO_CONFIRMATION_SCORE,
    MICRO_CONFIRMATION_SCORE_CAP,
    MICRO_CONFIRMATION_USE_M1_FALLBACK,
    PIP_SIZE,
)
from utils.helpers import candle_body, lower_wick, upper_wick
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MicroConfirmationResult:
    confirmation_type: str = "none"
    score: float = 0.0
    decision: str = "neutral"  # "boosted" | "blocked" | "neutral"
    timeframe: str = ""
    details: str = ""


class MicroConfirmationEngine:
    """Detects lower-timeframe patterns near an already-confirmed setup level."""

    def evaluate(self, data: Dict[str, pd.DataFrame], setup) -> MicroConfirmationResult:
        if setup is None or getattr(setup, "level", None) is None:
            return MicroConfirmationResult(details="missing setup")

        m5_df = data.get("M5")
        m1_df = data.get("M1")
        m5_bars = len(m5_df) if m5_df is not None else 0
        m1_bars = len(m1_df) if m1_df is not None else 0
        logger.debug(
            "MICRO DEBUG: evaluating %s @ %.2f | M5=%d bars | M1=%d bars | AOI=±%gpips | lookback=%d",
            setup.direction, setup.level.price,
            m5_bars, m1_bars,
            MICRO_CONFIRMATION_AOI_PIPS, MICRO_CONFIRMATION_LOOKBACK,
        )

        result = self._evaluate_timeframe(m5_df, "M5", setup)
        if result.decision != "neutral":
            self._log(result, setup.direction)
            return result

        if MICRO_CONFIRMATION_USE_M1_FALLBACK:
            m1_result = self._evaluate_timeframe(m1_df, "M1", setup)
            if m1_result.decision != "neutral":
                self._log(m1_result, setup.direction)
                return m1_result

        logger.info(
            "MICRO LAYER: none | %s %.2f %s | M5=%d bars M1=%d bars | checked all detectors",
            getattr(setup.level, "level_type", "level"),
            setup.level.price, setup.direction,
            m5_bars, m1_bars,
        )
        return result

    def _evaluate_timeframe(
        self,
        df: Optional[pd.DataFrame],
        timeframe: str,
        setup,
    ) -> MicroConfirmationResult:
        if df is None or len(df) < 5:
            logger.debug(
                "MICRO DEBUG [%s]: skipped — %d bars available (need ≥5)",
                timeframe, len(df) if df is not None else 0,
            )
            return MicroConfirmationResult(timeframe=timeframe, details="not enough candles")

        window = df.iloc[-MICRO_CONFIRMATION_LOOKBACK:].reset_index(drop=True)
        if len(window) < 5:
            logger.debug("MICRO DEBUG [%s]: window too small (%d bars)", timeframe, len(window))
            return MicroConfirmationResult(timeframe=timeframe, details="not enough window")

        direction = setup.direction
        level = float(setup.level.price)
        tol = MICRO_CONFIRMATION_AOI_PIPS * PIP_SIZE

        # Quick proximity check — log if no candles are near the level at all
        near_level = (
            (window["low"] <= level + tol) | (window["high"] >= level - tol)
        ).any()
        if not near_level:
            logger.debug(
                "MICRO DEBUG [%s]: no candle in window touched level %.2f ±%.1fpips — all detectors will miss",
                timeframe, level, MICRO_CONFIRMATION_AOI_PIPS,
            )

        contradiction = self._detect_contradiction(window, direction, level)
        if contradiction:
            return self._result("micro_contradiction", timeframe, contradiction)

        for detector in (
            self._detect_liquidity_sweep_reclaim,
            self._detect_engulfing_reversal,
            self._detect_double_pattern,
            self._detect_wick_follow_through,
        ):
            details = detector(window, direction, level)
            if details:
                kind = detector.__name__.replace("_detect_", "")
                return self._result(kind, timeframe, details)
            logger.debug(
                "MICRO DEBUG [%s]: %s → no match",
                timeframe, detector.__name__,
            )

        return MicroConfirmationResult(timeframe=timeframe, details="no micro pattern")

    def _result(self, kind: str, timeframe: str, details: str) -> MicroConfirmationResult:
        raw = float(MICRO_CONFIRMATION_SCORE.get(kind, 0.0))
        score = min(raw, MICRO_CONFIRMATION_SCORE_CAP) if raw > 0 else raw
        decision = "blocked" if score < 0 else "boosted" if score > 0 else "neutral"
        return MicroConfirmationResult(
            confirmation_type=kind,
            score=round(score, 1),
            decision=decision,
            timeframe=timeframe,
            details=details,
        )

    def _detect_contradiction(self, window: pd.DataFrame, direction: str, level: float) -> str:
        last = window.iloc[-1]
        o = float(last["open"])
        c = float(last["close"])
        body_pips = candle_body(o, c) / PIP_SIZE
        tol = MICRO_CONFIRMATION_AOI_PIPS * PIP_SIZE

        if direction == "BUY" and c < level - tol and c < o and body_pips >= 5:
            return f"bearish MTF close through support ({c:.2f} < {level:.2f})"
        if direction == "SELL" and c > level + tol and c > o and body_pips >= 5:
            return f"bullish MTF close through resistance ({c:.2f} > {level:.2f})"
        return ""

    def _detect_liquidity_sweep_reclaim(self, window: pd.DataFrame, direction: str, level: float) -> str:
        tol = MICRO_CONFIRMATION_AOI_PIPS * PIP_SIZE
        recent = window.iloc[-3:]
        for _, candle in recent.iterrows():
            h = float(candle["high"])
            l = float(candle["low"])
            c = float(candle["close"])
            if direction == "BUY" and l <= level + tol and c > level:
                return f"swept support then reclaimed ({l:.2f} -> close {c:.2f})"
            if direction == "SELL" and h >= level - tol and c < level:
                return f"swept resistance then reclaimed ({h:.2f} -> close {c:.2f})"
        return ""

    def _detect_engulfing_reversal(self, window: pd.DataFrame, direction: str, level: float) -> str:
        prev = window.iloc[-2]
        cur = window.iloc[-1]
        po = float(prev["open"])
        pc = float(prev["close"])
        co = float(cur["open"])
        cc = float(cur["close"])
        h = float(cur["high"])
        l = float(cur["low"])
        tol = MICRO_CONFIRMATION_AOI_PIPS * PIP_SIZE

        touches = l <= level + tol if direction == "BUY" else h >= level - tol
        if not touches:
            return ""

        if direction == "BUY" and pc < po and cc > co and co <= pc and cc >= po:
            return f"bullish engulfing near support (close {cc:.2f})"
        if direction == "SELL" and pc > po and cc < co and co >= pc and cc <= po:
            return f"bearish engulfing near resistance (close {cc:.2f})"
        return ""

    def _detect_double_pattern(self, window: pd.DataFrame, direction: str, level: float) -> str:
        tol = MICRO_CONFIRMATION_AOI_PIPS * PIP_SIZE
        touches = []
        for idx, candle in window.iterrows():
            h = float(candle["high"])
            l = float(candle["low"])
            if direction == "BUY" and l <= level + tol:
                touches.append(idx)
            elif direction == "SELL" and h >= level - tol:
                touches.append(idx)

        if len(touches) < 2 or touches[-1] - touches[0] < 2:
            return ""

        last = window.iloc[-1]
        c = float(last["close"])
        if direction == "BUY" and c > level:
            return f"double bottom near support ({len(touches)} touches)"
        if direction == "SELL" and c < level:
            return f"double top near resistance ({len(touches)} touches)"
        return ""

    def _detect_wick_follow_through(self, window: pd.DataFrame, direction: str, level: float) -> str:
        tol = MICRO_CONFIRMATION_AOI_PIPS * PIP_SIZE
        for idx in range(max(1, len(window) - 5), len(window) - 1):
            reject = window.iloc[idx]
            follow = window.iloc[idx + 1]
            o = float(reject["open"])
            h = float(reject["high"])
            l = float(reject["low"])
            c = float(reject["close"])
            body = max(candle_body(o, c), PIP_SIZE)

            fo = float(follow["open"])
            fc = float(follow["close"])
            if direction == "BUY":
                wick_ratio = lower_wick(o, l, c) / body
                if l <= level + tol and wick_ratio >= 1.5 and fc > fo and fc > c:
                    return f"lower wick rejection plus bullish follow-through ({wick_ratio:.1f}x)"
            else:
                wick_ratio = upper_wick(o, h, c) / body
                if h >= level - tol and wick_ratio >= 1.5 and fc < fo and fc < c:
                    return f"upper wick rejection plus bearish follow-through ({wick_ratio:.1f}x)"
        return ""

    @staticmethod
    def _log(result: MicroConfirmationResult, direction: str):
        if result.decision == "blocked":
            logger.info(
                "MICRO CONFIRMATION CONTRADICTION: reject %s setup | %s | %s",
                direction,
                result.timeframe,
                result.details,
            )
            return
        logger.info(
            "MICRO CONFIRMATION: %s @ %s | %+g | %s",
            result.confirmation_type,
            result.timeframe,
            result.score,
            result.details,
        )
