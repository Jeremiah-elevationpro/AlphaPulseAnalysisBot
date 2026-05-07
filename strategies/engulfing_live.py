from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

from config.settings import (
    BEARISH_ENGULF_BIAS_BONUS,
    ENGULF_ALLOWED_LIVE_TIMEFRAMES,
    ENGULF_BULLISH_DIRECTION_BONUS,
    ENGULF_H1_RELAXED_QUALITY_SCORE,
    ENGULF_LIVE_CONFIRMATION_TYPE,
    ENGULF_LIVE_H1_RELAXED_QUALITY_SCORE,
    ENGULF_LIVE_MAX_CANDIDATES_PER_SCAN,
    ENGULF_LIVE_MAX_PER_TIMEFRAME_DIRECTION_SESSION,
    ENGULF_LIVE_MIN_QUALITY_REJECTIONS,
    ENGULF_LIVE_MIN_QUALITY_SCORE,
    ENGULF_LIVE_NEW_YORK_STRONG_QUALITY,
    ENGULF_LIVE_SESSION_SCORE,
    ENGULF_LIVE_SETUP_TYPE,
    ENGULF_LIVE_TIMEFRAME_SCORE,
    ENGULF_MODERATE_BIAS_BONUS,
    ENGULF_STRONG_BIAS_BONUS,
    LEVEL_TOLERANCE_PIPS,
    PIP_SIZE,
)
from strategies.confirmation import ConfirmationResult
from strategies.level_detector import LevelDetector, LevelInfo
from strategies.multi_timeframe import SetupResult
from utils.logger import get_logger

logger = get_logger(__name__)

_TOL = LEVEL_TOLERANCE_PIPS * PIP_SIZE
_TF_MINUTES = {"H1": 60, "M30": 30, "M15": 15}


@dataclass
class EngulfingCandidate:
    timeframe: str
    level: LevelInfo
    current_price: float
    session_name: str
    dominant_bias: str
    bias_strength: str
    shortlist_score: float = 0.0
    confirmation_score: float = 0.0
    confirmation_path: str = ""
    revisit_time: Optional[datetime] = None
    confirmation_time: Optional[datetime] = None
    confirmation_candles_used: int = 0


class LiveEngulfingAnalyzer:
    """Research-backed engulfing live forward-testing lane."""

    def __init__(self) -> None:
        self.level_detector = LevelDetector()

    def analyze(
        self,
        data: Dict[str, pd.DataFrame],
        *,
        pair: str,
        current_price: Optional[float],
        context,
    ) -> List[SetupResult]:
        if current_price is None or context is None:
            return []

        raw_candidates: List[EngulfingCandidate] = []
        for timeframe in ("H1", "M30", "M15"):
            if timeframe not in ENGULF_ALLOWED_LIVE_TIMEFRAMES:
                if timeframe == "M15":
                    logger.info("ENGULF REJECTED: M15 disabled for research refinement")
                continue
            df = data.get(timeframe)
            if df is None or len(df) < 40:
                continue
            levels = self.level_detector._detect_gap_levels(
                df,
                timeframe,
                current_price=current_price,
                h4_bias=getattr(context, "h4_bias", "neutral"),
                scope="research",
                min_quality=0.0,
                distance_filter_pips=0.0,
            )
            for level in levels:
                if level.level_type != "Gap":
                    continue
                candidate = EngulfingCandidate(
                    timeframe=timeframe,
                    level=level,
                    current_price=current_price,
                    session_name=getattr(context, "session_name", "off_session") or "off_session",
                    dominant_bias=(getattr(context, "dominant_bias", "neutral") or "neutral").lower(),
                    bias_strength=(getattr(context, "bias_strength", "weak") or "weak").lower(),
                )
                if not self._candidate_passes(candidate):
                    continue
                candidate.shortlist_score = self._candidate_score(candidate)
                raw_candidates.append(candidate)

        shortlisted = self._select_best_candidates(raw_candidates)
        if shortlisted:
            logger.info(
                "ENGULF SHORTLIST RELAXED: selected %d from %d candidates",
                len(shortlisted),
                len(raw_candidates),
            )

        setups: List[SetupResult] = []
        for candidate in shortlisted:
            df = data.get(candidate.timeframe)
            if df is None or len(df) < 6:
                continue
            decision = self._evaluate_revisit_confirmation(candidate=candidate, confirm_df=df.reset_index(drop=True))
            if not decision.get("confirmed"):
                continue
            candidate.confirmation_score = float(decision.get("score") or 0.0)
            candidate.confirmation_path = str(decision.get("path") or "combined")
            candidate.revisit_time = decision.get("revisit_time")
            candidate.confirmation_time = decision.get("confirmation_time")
            candidate.confirmation_candles_used = int(decision.get("confirmation_candles_used") or 0)
            setups.append(self._build_setup(pair, candidate, decision))

        return sorted(setups, key=lambda s: (s.final_score, s.confidence), reverse=True)

    def _candidate_passes(self, candidate: EngulfingCandidate) -> bool:
        direction = (candidate.level.trade_direction or "").upper()
        bias = candidate.dominant_bias
        bias_strength = candidate.bias_strength

        if candidate.timeframe not in ENGULF_ALLOWED_LIVE_TIMEFRAMES:
            return False
        if bias in {"mixed", "neutral"}:
            logger.info("ENGULF REJECTED: weak bias environment | bias=%s", bias)
            return False
        if bias_strength == "weak":
            logger.info("ENGULF REJECTED: weak bias blocked")
            return False
        if (direction == "BUY" and bias != "bullish") or (direction == "SELL" and bias != "bearish"):
            logger.info("ENGULF REJECTED: direction not aligned with dominant bias")
            return False
        if int(getattr(candidate.level, "quality_rejection_count", 0) or 0) < ENGULF_LIVE_MIN_QUALITY_REJECTIONS:
            logger.info(
                "ENGULF REJECTED: insufficient quality rejections | quality=%d < %d",
                int(getattr(candidate.level, "quality_rejection_count", 0) or 0),
                ENGULF_LIVE_MIN_QUALITY_REJECTIONS,
            )
            return False

        threshold = ENGULF_LIVE_MIN_QUALITY_SCORE
        if candidate.timeframe == "H1" and int(getattr(candidate.level, "quality_rejection_count", 0) or 0) >= 5:
            threshold = min(threshold, ENGULF_LIVE_H1_RELAXED_QUALITY_SCORE, ENGULF_H1_RELAXED_QUALITY_SCORE)
        quality_score = float(getattr(candidate.level, "quality_score", 0.0) or 0.0)
        if quality_score < threshold:
            logger.info("ENGULF REJECTED: quality below threshold")
            return False

        if candidate.session_name == "new_york" and quality_score < ENGULF_LIVE_NEW_YORK_STRONG_QUALITY:
            logger.info(
                "ENGULF REJECTED: new york caution mode requires stronger quality | Q=%.1f < %.1f",
                quality_score,
                ENGULF_LIVE_NEW_YORK_STRONG_QUALITY,
            )
            return False

        logger.info("ENGULF QUALITY PASS: Q=%.1f", quality_score)
        return True

    def _candidate_score(self, candidate: EngulfingCandidate) -> float:
        direction = (candidate.level.trade_direction or "").upper()
        bias = candidate.dominant_bias
        bias_strength = candidate.bias_strength
        session_name = candidate.session_name
        distance_pips = abs(candidate.level.price - candidate.current_price) / PIP_SIZE

        direction_bonus = 0
        if direction == "SELL" and bias == "bearish":
            direction_bonus += BEARISH_ENGULF_BIAS_BONUS
        elif direction == "BUY" and bias == "bullish":
            direction_bonus += ENGULF_BULLISH_DIRECTION_BONUS
        logger.info("ENGULF DIRECTION SCORE: direction=%s | bonus=%d", direction or "unknown", direction_bonus)

        bias_bonus = ENGULF_STRONG_BIAS_BONUS if bias_strength == "strong" else ENGULF_MODERATE_BIAS_BONUS
        session_bonus = ENGULF_LIVE_SESSION_SCORE.get(session_name, 0)
        tf_bonus = ENGULF_LIVE_TIMEFRAME_SCORE.get(candidate.timeframe, 0)
        if session_name:
            logger.info("ENGULF SESSION SCORE: session=%s | bonus=%d", session_name, session_bonus)

        structure_bonus = self._structure_break_bonus(int(getattr(candidate.level, "break_count", 0) or 0))
        quality_rejection_bonus = self._quality_rejection_bonus(
            int(getattr(candidate.level, "quality_rejection_count", 0) or 0)
        )
        return (
            float(getattr(candidate.level, "quality_score", 0.0) or 0.0)
            + direction_bonus
            + bias_bonus
            + session_bonus
            + tf_bonus
            + structure_bonus
            + quality_rejection_bonus
            - min(distance_pips / 50.0, 8.0)
        )

    @staticmethod
    def _structure_break_bonus(count: int) -> int:
        if count == 1:
            bonus = 5
        elif count == 2:
            bonus = -4
        elif count >= 3:
            bonus = 5
        else:
            bonus = 1
        logger.info("ENGULF STRUCTURE BREAK SCORE: count=%d | bonus=%d", count, bonus)
        return bonus

    @staticmethod
    def _quality_rejection_bonus(count: int) -> int:
        if count >= 8:
            return 3
        if count >= 5:
            return 3
        return 2

    def _select_best_candidates(self, candidates: List[EngulfingCandidate]) -> List[EngulfingCandidate]:
        per_bucket: Dict[tuple, List[EngulfingCandidate]] = {}
        for candidate in candidates:
            bucket = (candidate.timeframe, candidate.level.trade_direction, candidate.session_name)
            per_bucket.setdefault(bucket, []).append(candidate)

        shortlisted: List[EngulfingCandidate] = []
        for bucket_candidates in per_bucket.values():
            bucket_candidates.sort(
                key=lambda c: (
                    c.shortlist_score,
                    float(getattr(c.level, "quality_score", 0.0) or 0.0),
                    int(getattr(c.level, "quality_rejection_count", 0) or 0),
                    ENGULF_LIVE_TIMEFRAME_SCORE.get(c.timeframe, 0),
                    -abs(c.level.price - c.current_price),
                ),
                reverse=True,
            )
            shortlisted.extend(bucket_candidates[:ENGULF_LIVE_MAX_PER_TIMEFRAME_DIRECTION_SESSION])

        shortlisted.sort(
            key=lambda c: (
                c.shortlist_score,
                float(getattr(c.level, "quality_score", 0.0) or 0.0),
                int(getattr(c.level, "quality_rejection_count", 0) or 0),
                ENGULF_LIVE_TIMEFRAME_SCORE.get(c.timeframe, 0),
                -abs(c.level.price - c.current_price),
            ),
            reverse=True,
        )
        return shortlisted[:ENGULF_LIVE_MAX_CANDIDATES_PER_SCAN]

    def _evaluate_revisit_confirmation(self, *, candidate: EngulfingCandidate, confirm_df: pd.DataFrame) -> Dict[str, object]:
        direction = (candidate.level.trade_direction or "").upper()
        zone_low = float(getattr(candidate.level, "zone_low", candidate.level.price) or candidate.level.price)
        zone_high = float(getattr(candidate.level, "zone_high", candidate.level.price) or candidate.level.price)
        zone_mid = round((zone_low + zone_high) / 2.0, 2)
        push_min = {"M15": 12.0, "M30": 18.0, "H1": 25.0}.get(candidate.timeframe, 12.0)

        closed = confirm_df.iloc[:-1].tail(4).reset_index(drop=True)
        if closed.empty:
            return {"confirmed": False}

        for idx in range(len(closed)):
            candle = closed.iloc[idx]
            window = closed.iloc[idx : min(len(closed), idx + 4)]
            high = float(candle["high"])
            low = float(candle["low"])
            open_ = float(candle["open"])
            close = float(candle["close"])
            touched = high >= zone_low - _TOL and low <= zone_high + _TOL
            if not touched:
                continue

            revisit_time = self._to_datetime(candle.get("time"))
            score = 0.0
            paths: List[str] = []
            next_close = float(window.iloc[1]["close"]) if len(window) > 1 else close

            wick_ratio, wick_percent = self._wick_metrics(candle, direction)
            if direction == "SELL":
                if close > zone_high + _TOL:
                    logger.info("ENGULF CONFIRMATION REJECT: score=0 | reason=hard_zone_break")
                    return {"confirmed": False}
                if wick_ratio >= 1.2 and close <= max(zone_mid, candidate.level.price):
                    score += 25
                    paths.append("wick_rejection")
                if close < zone_mid and next_close <= zone_high:
                    score += 20
                    paths.append("close_rejection")
                if high > zone_high and close <= zone_high and next_close < close:
                    score += 30
                    paths.append("micro_sweep")
                push_away = max(0.0, (zone_low - float(window["low"].min())) / PIP_SIZE)
                if push_away >= push_min and float(window["close"].max()) <= zone_high + _TOL:
                    score += 20
                    paths.append("momentum_rejection")
                if close > open_:
                    score -= 20
            else:
                if close < zone_low - _TOL:
                    logger.info("ENGULF CONFIRMATION REJECT: score=0 | reason=hard_zone_break")
                    return {"confirmed": False}
                if wick_ratio >= 1.2 and close >= min(zone_mid, candidate.level.price):
                    score += 25
                    paths.append("wick_rejection")
                if close > zone_mid and next_close >= zone_low:
                    score += 20
                    paths.append("close_rejection")
                if low < zone_low and close >= zone_low and next_close > close:
                    score += 30
                    paths.append("micro_sweep")
                push_away = max(0.0, (float(window["high"].max()) - zone_high) / PIP_SIZE)
                if push_away >= push_min and float(window["close"].min()) >= zone_low - _TOL:
                    score += 20
                    paths.append("momentum_rejection")
                if close < open_:
                    score -= 20

            if candidate.dominant_bias in {"bullish", "bearish"}:
                score += 10

            if score >= 35 and paths:
                path = "combined" if len(paths) > 1 else paths[0]
                logger.info("ENGULF CONFIRMATION PASS: path=%s | score=%.0f", path, score)
                return {
                    "confirmed": True,
                    "path": path,
                    "score": score,
                    "revisit_time": revisit_time,
                    "confirmation_time": self._to_datetime(window.iloc[len(window) - 1].get("time")),
                    "confirmation_candles_used": len(window),
                    "entry_price": float(candidate.level.price),
                    "sl_price": round(zone_high + _TOL, 2) if direction == "SELL" else round(zone_low - _TOL, 2),
                    "candle_high": high,
                    "candle_low": low,
                    "candle_close": close,
                    "wick_ratio": wick_ratio if wick_ratio >= 1.2 or wick_percent >= 35.0 else 0.0,
                }

            logger.info("ENGULF CONFIRMATION REJECT: score=%.0f | reason=no_pushaway", score)
        return {"confirmed": False}

    def _build_setup(self, pair: str, candidate: EngulfingCandidate, decision: Dict[str, object]) -> SetupResult:
        direction = (candidate.level.trade_direction or "").upper()
        entry_price = float(decision.get("entry_price") or candidate.level.price)
        sl_price = float(decision.get("sl_price") or (candidate.level.zone_low if direction == "BUY" else candidate.level.zone_high))
        candle_time = pd.Timestamp(candidate.confirmation_time or candidate.revisit_time or datetime.utcnow())
        confirmation = ConfirmationResult(
            confirmed=True,
            direction=direction,
            level=candidate.level,
            candle_index=max(0, len(str(candidate.timeframe))),
            candle_time=candle_time,
            entry_price=round(entry_price, 2),
            sl_price=round(sl_price, 2),
            candle_high=float(decision.get("candle_high") or candidate.level.zone_high or candidate.level.price),
            candle_low=float(decision.get("candle_low") or candidate.level.zone_low or candidate.level.price),
            candle_close=float(decision.get("candle_close") or candidate.level.price),
            wick_ratio=float(decision.get("wick_ratio") or 0.0),
            sl_pips=round(abs(entry_price - sl_price) / PIP_SIZE, 1),
            note=f"{direction} engulf live forward-test",
            confirmation_type=ENGULF_LIVE_CONFIRMATION_TYPE,
        )
        confidence = min(0.95, max(0.68, candidate.shortlist_score / 100.0))
        setup = SetupResult(
            pair=pair,
            direction=direction,
            higher_tf=candidate.timeframe,
            lower_tf=candidate.timeframe,
            level=candidate.level,
            confirmation=confirmation,
            confidence=round(confidence, 3),
            setup_type=ENGULF_LIVE_SETUP_TYPE,
            session_name=candidate.session_name,
            h4_bias=candidate.dominant_bias,
            bias_strength=candidate.bias_strength,
            trend_aligned=True,
            final_score=round(candidate.shortlist_score, 1),
        )
        setup.strategy_type = "engulfing_rejection"
        setup.source = "live_bot"
        setup.dominant_bias = candidate.dominant_bias
        setup.quality_rejection_count = int(getattr(candidate.level, "quality_rejection_count", 0) or 0)
        setup.structure_break_count = int(getattr(candidate.level, "break_count", 0) or 0)
        setup.confirmation_score = float(candidate.confirmation_score or 0.0)
        setup.confirmation_path = candidate.confirmation_path or "combined"
        setup.revisit_time = candidate.revisit_time
        setup.confirmation_time = candidate.confirmation_time
        setup.confirmation_candles_used = candidate.confirmation_candles_used
        return setup

    @staticmethod
    def _wick_metrics(candle: pd.Series, direction: str) -> tuple[float, float]:
        high = float(candle["high"])
        low = float(candle["low"])
        open_ = float(candle["open"])
        close = float(candle["close"])
        body = max(abs(close - open_), PIP_SIZE * 0.2)
        range_ = max(high - low, PIP_SIZE * 0.2)
        wick = (high - max(open_, close)) if direction == "SELL" else (min(open_, close) - low)
        wick = max(wick, 0.0)
        return wick / body, (wick / range_) * 100.0

    @staticmethod
    def _to_datetime(value) -> Optional[datetime]:
        if value is None:
            return None
        try:
            return pd.Timestamp(value).to_pydatetime()
        except Exception:
            return None
