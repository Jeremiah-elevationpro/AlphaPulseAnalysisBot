from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from config.settings import (
    LEVEL_TOLERANCE_PIPS,
    MAX_SL_PIPS,
    MIN_SL_PIPS,
    PIP_SIZE,
    STANDARD_BRT_ALLOWED_CONFIRMATIONS,
    TP_PIPS,
)
from strategies.confirmation import ConfirmationResult
from strategies.level_detector import LevelInfo
from strategies.multi_timeframe import MarketOutlook, SetupResult
from utils.logger import get_logger

logger = get_logger(__name__)

_BREAK_MIN_PIPS = {"M15": 10.0, "M30": 15.0, "H1": 20.0}
_MAX_BARS_TO_RETEST = {"M15": 32, "M30": 16, "H1": 8}
_RETEST_ZONE_TOL = LEVEL_TOLERANCE_PIPS * PIP_SIZE
_BODY_RATIO_MIN = 0.50
_BODY_PIPS_MIN = 5.0
_CONFIRMATION_THRESHOLD = 25.0
_WICK_SCORE = 25.0
_CLOSE_SCORE = 20.0
_MOMENTUM_SCORE = 15.0
_BIAS_BONUS = 5.0
_ALLOWED_CONFIRMATIONS = set(STANDARD_BRT_ALLOWED_CONFIRMATIONS or ["close_confirmation"])


@dataclass
class LiveBreakCandidate:
    direction: str
    timeframe: str
    break_level: float
    zone_high: float
    zone_low: float
    source_level_type: str
    session_name: str
    dominant_bias: str
    bias_strength: str
    break_time: datetime
    break_close: float
    break_distance_pips: float
    break_body_pips: float
    break_body_ratio: float
    retest_touched: bool = False
    retest_time: Optional[datetime] = None
    retest_level: Optional[float] = None
    bars_since_break: int = 0
    confirmation_bars: int = 0


class LiveBreakRetestAnalyzer:
    """Approved live forward-test lane for standard break + retest."""

    def __init__(self) -> None:
        self._strategy_type = "standard_break_retest"

    def analyze(
        self,
        data: Dict[str, pd.DataFrame],
        *,
        pair: str,
        current_price: Optional[float],
        outlook: Optional[MarketOutlook],
    ) -> List[SetupResult]:
        if current_price is None or outlook is None or outlook.context is None:
            return []

        candidates = self._scan_candidates(data, outlook)
        setups: List[SetupResult] = []
        for candidate in candidates[-6:]:
            entry, sl = self._compute_entry_sl(
                candidate,
                candidate.break_close,
                candidate.zone_high,
                candidate.zone_low,
            )
            if sl == 0.0:
                continue
            tp1, tp2, tp3 = self._compute_tps(entry, sl, candidate.direction)
            level = LevelInfo(
                price=float(candidate.break_level),
                level_type=candidate.source_level_type or "Gap",
                timeframe=candidate.timeframe,
                scope="major",
                trade_direction=candidate.direction,
                zone_low=float(candidate.zone_low),
                zone_high=float(candidate.zone_high),
                quality_score=72.0,
                selection_score=72.0,
            )
            # Guard against already-tz-aware Timestamps — modern pandas raises if you
            # pass tz="UTC" alongside a tz-aware value. MT5 candle times come in as
            # tz-aware UTC already, so prefer tz_convert when applicable.
            _ts_src = pd.Timestamp(candidate.retest_time or candidate.break_time)
            candle_time = _ts_src.tz_localize("UTC") if _ts_src.tz is None else _ts_src.tz_convert("UTC")
            confirmation = ConfirmationResult(
                confirmed=True,
                direction=candidate.direction,
                level=level,
                candle_index=-1,
                candle_time=candle_time,
                entry_price=round(entry, 2),
                sl_price=round(sl, 2),
                candle_high=float(candidate.zone_high),
                candle_low=float(candidate.zone_low),
                candle_close=round(entry, 2),
                confirmation_type="close_confirmation",
                note="approved break + retest close confirmation",
            )
            setup = SetupResult(
                pair=pair,
                direction=candidate.direction,
                higher_tf=candidate.timeframe,
                lower_tf=candidate.timeframe,
                level=level,
                confirmation=confirmation,
                confidence=min(0.95, 0.68 + (_CLOSE_SCORE / 100.0)),
                setup_type="break_retest",
                session_name=candidate.session_name,
                h4_bias=candidate.dominant_bias,
                bias_strength=candidate.bias_strength,
                trend_aligned=True,
                final_score=round(65.0 + _CLOSE_SCORE + _BIAS_BONUS, 1),
                strategy_type=self._strategy_type,
                source="live_bot",
                dominant_bias=candidate.dominant_bias,
                confirmation_score=_CLOSE_SCORE + _BIAS_BONUS,
                confirmation_path="close_confirmation",
            )
            setattr(setup, "break_level", float(candidate.break_level))
            setattr(setup, "retest_level", float(candidate.retest_level or candidate.break_level))
            setattr(setup, "retest_confirmation_type", "close_confirmation")
            setattr(setup, "tp1", round(tp1, 2))
            setattr(setup, "tp2", round(tp2, 2))
            setattr(setup, "tp3", round(tp3, 2))
            setups.append(setup)

        return setups

    def _scan_candidates(
        self,
        data: Dict[str, pd.DataFrame],
        outlook: MarketOutlook,
    ) -> List[LiveBreakCandidate]:
        context = outlook.context
        dominant_bias = (getattr(context, "dominant_bias", "") or getattr(context, "h4_bias", "neutral") or "neutral").lower()
        bias_strength = (getattr(context, "bias_strength", "weak") or "weak").lower()
        session_name = getattr(context, "session_name", "off_session") or "off_session"

        levels_by_tf: Dict[str, List[LevelInfo]] = {}
        for tfl in outlook.timeframe_levels:
            levels_by_tf.setdefault(tfl.lower_tf, [])
            levels_by_tf[tfl.lower_tf].extend(tfl.levels + tfl.recent_levels + tfl.previous_levels)

        setups: List[LiveBreakCandidate] = []
        for timeframe, levels in levels_by_tf.items():
            df = data.get(timeframe)
            if df is None or len(df) < 12:
                continue
            bars = df.tail(48).reset_index(drop=True)
            for level in levels:
                candidate = self._find_candidate_for_level(
                    bars=bars,
                    timeframe=timeframe,
                    level=level,
                    session_name=session_name,
                    dominant_bias=dominant_bias,
                    bias_strength=bias_strength,
                )
                if candidate is not None:
                    setups.append(candidate)
        return sorted(setups, key=lambda item: item.break_time, reverse=True)

    def _find_candidate_for_level(
        self,
        *,
        bars: pd.DataFrame,
        timeframe: str,
        level: LevelInfo,
        session_name: str,
        dominant_bias: str,
        bias_strength: str,
    ) -> Optional[LiveBreakCandidate]:
        zone_high = float(getattr(level, "zone_high", level.price + _RETEST_ZONE_TOL) or (level.price + _RETEST_ZONE_TOL))
        zone_low = float(getattr(level, "zone_low", level.price - _RETEST_ZONE_TOL) or (level.price - _RETEST_ZONE_TOL))
        break_candidate: Optional[LiveBreakCandidate] = None

        for idx in range(len(bars)):
            bar = bars.iloc[idx]
            bar_time = self._to_utc(bar.get("time"))
            if bar_time is None:
                continue
            bar_open = float(bar.get("open", bar.get("close", 0.0)) or 0.0)
            bar_close = float(bar.get("close", 0.0) or 0.0)
            bar_high = float(bar.get("high", bar_close) or bar_close)
            bar_low = float(bar.get("low", bar_close) or bar_close)
            direction = self._break_direction(level, bar_close, zone_high, zone_low)
            if break_candidate is None:
                if direction is None:
                    continue
                dist_pips = (
                    (bar_close - zone_high) / PIP_SIZE
                    if direction == "BUY"
                    else (zone_low - bar_close) / PIP_SIZE
                )
                body = abs(bar_close - bar_open)
                rng = max(bar_high - bar_low, PIP_SIZE * 0.2)
                body_ratio = body / rng if rng > 0 else 0.0
                body_pips = body / PIP_SIZE
                break_candidate = LiveBreakCandidate(
                    direction=direction,
                    timeframe=timeframe,
                    break_level=float(level.price),
                    zone_high=zone_high,
                    zone_low=zone_low,
                    source_level_type=level.level_type,
                    session_name=session_name,
                    dominant_bias=dominant_bias,
                    bias_strength=bias_strength,
                    break_time=bar_time,
                    break_close=bar_close,
                    break_distance_pips=dist_pips,
                    break_body_pips=body_pips,
                    break_body_ratio=body_ratio,
                )
                if not self._bias_strength_ok(break_candidate):
                    break_candidate = None
                    continue
                if not self._bias_direction_aligned(break_candidate):
                    break_candidate = None
                    continue
                ok, reason = self._validate_break_quality(break_candidate)
                if not ok:
                    if reason == "wick_only_break":
                        logger.info("STANDARD BRT REJECTED: wick-only break")
                    break_candidate = None
                    continue
                continue

            break_candidate.bars_since_break += 1
            if break_candidate.bars_since_break > _MAX_BARS_TO_RETEST.get(timeframe, 16):
                return None

            in_zone = bar_low <= break_candidate.break_level + _RETEST_ZONE_TOL and bar_high >= break_candidate.break_level - _RETEST_ZONE_TOL
            if not break_candidate.retest_touched and in_zone:
                break_candidate.retest_touched = True
                break_candidate.retest_time = bar_time
                break_candidate.retest_level = break_candidate.break_level
                continue

            if not break_candidate.retest_touched:
                continue

            break_candidate.confirmation_bars += 1
            confirmation_type, confirmation_score = self._evaluate_confirmation(
                break_candidate,
                bar_high,
                bar_low,
                bar_close,
                bar_open,
            )
            if confirmation_type == "rejection_wick" and confirmation_type not in _ALLOWED_CONFIRMATIONS:
                logger.info("STANDARD BRT REJECTED: rejection_wick disabled for approved mode")
                return None
            if confirmation_type != "close_confirmation":
                if break_candidate.confirmation_bars >= 5:
                    return None
                continue
            if confirmation_score < _CONFIRMATION_THRESHOLD:
                continue
            return break_candidate

        return None

    @staticmethod
    def _break_direction(level: LevelInfo, bar_close: float, zone_high: float, zone_low: float) -> Optional[str]:
        if level.level_type in ("A", "Gap") and bar_close > zone_high:
            return "BUY"
        if level.level_type in ("V", "Gap") and bar_close < zone_low:
            return "SELL"
        return None

    @staticmethod
    def _validate_break_quality(candidate: LiveBreakCandidate) -> tuple[bool, str]:
        min_dist = _BREAK_MIN_PIPS.get(candidate.timeframe, 15.0)
        if candidate.break_distance_pips < min_dist:
            return False, "no_valid_break"
        if candidate.break_body_ratio < _BODY_RATIO_MIN:
            return False, "wick_only_break"
        if candidate.break_body_pips < _BODY_PIPS_MIN:
            return False, "no_valid_break"
        return True, ""

    @staticmethod
    def _bias_strength_ok(candidate: LiveBreakCandidate) -> bool:
        return candidate.bias_strength in {"moderate", "strong"}

    @staticmethod
    def _bias_direction_aligned(candidate: LiveBreakCandidate) -> bool:
        bias = candidate.dominant_bias
        return (
            (candidate.direction == "BUY" and bias == "bullish")
            or (candidate.direction == "SELL" and bias == "bearish")
        )

    @staticmethod
    def _evaluate_confirmation(
        candidate: LiveBreakCandidate,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_open: float,
    ) -> tuple[str, float]:
        score = 0.0
        confirmation_type = "none"
        eff_tol = _RETEST_ZONE_TOL
        body = abs(bar_close - bar_open)

        if candidate.direction == "BUY":
            lower_wick = min(bar_open, bar_close) - bar_low
            if body > 0 and lower_wick > body * 1.5 and bar_close > candidate.break_level:
                score += _WICK_SCORE
                confirmation_type = "rejection_wick"
            elif bar_close > candidate.break_level + eff_tol * 0.3:
                score += _CLOSE_SCORE
                confirmation_type = "close_confirmation"
            momentum_threshold = _BREAK_MIN_PIPS.get(candidate.timeframe, 15.0) * PIP_SIZE * 0.3
            if bar_close > candidate.break_level + momentum_threshold:
                score += _MOMENTUM_SCORE
                if confirmation_type == "none":
                    confirmation_type = "momentum"
        else:
            upper_wick = bar_high - max(bar_open, bar_close)
            if body > 0 and upper_wick > body * 1.5 and bar_close < candidate.break_level:
                score += _WICK_SCORE
                confirmation_type = "rejection_wick"
            elif bar_close < candidate.break_level - eff_tol * 0.3:
                score += _CLOSE_SCORE
                confirmation_type = "close_confirmation"
            momentum_threshold = _BREAK_MIN_PIPS.get(candidate.timeframe, 15.0) * PIP_SIZE * 0.3
            if bar_close < candidate.break_level - momentum_threshold:
                score += _MOMENTUM_SCORE
                if confirmation_type == "none":
                    confirmation_type = "momentum"

        if (
            (candidate.direction == "BUY" and candidate.dominant_bias == "bullish")
            or (candidate.direction == "SELL" and candidate.dominant_bias == "bearish")
        ):
            score += _BIAS_BONUS

        return confirmation_type, score

    @staticmethod
    def _compute_entry_sl(
        candidate: LiveBreakCandidate,
        bar_close: float,
        bar_high: float,
        bar_low: float,
    ) -> tuple[float, float]:
        if candidate.direction == "BUY":
            entry = bar_close
            sl = min(bar_low, candidate.break_level - _RETEST_ZONE_TOL)
            sl_pips = (entry - sl) / PIP_SIZE
            if sl_pips < MIN_SL_PIPS:
                sl = entry - MIN_SL_PIPS * PIP_SIZE
            if (entry - sl) / PIP_SIZE > MAX_SL_PIPS:
                return 0.0, 0.0
        else:
            entry = bar_close
            sl = max(bar_high, candidate.break_level + _RETEST_ZONE_TOL)
            sl_pips = (sl - entry) / PIP_SIZE
            if sl_pips < MIN_SL_PIPS:
                sl = entry + MIN_SL_PIPS * PIP_SIZE
            if (sl - entry) / PIP_SIZE > MAX_SL_PIPS:
                return 0.0, 0.0
        return round(entry, 2), round(sl, 2)

    @staticmethod
    def _compute_tps(entry: float, sl: float, direction: str) -> tuple[float, float, float]:
        targets: List[float] = []
        for pip_target in TP_PIPS[:3]:
            if direction == "BUY":
                targets.append(round(entry + pip_target * PIP_SIZE, 2))
            else:
                targets.append(round(entry - pip_target * PIP_SIZE, 2))
        while len(targets) < 3:
            targets.append(round(entry, 2))
        return targets[0], targets[1], targets[2]

    @staticmethod
    def _to_utc(value) -> Optional[datetime]:
        if value is None:
            return None
        try:
            ts = pd.Timestamp(value)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return ts.to_pydatetime()
        except Exception:
            return None
