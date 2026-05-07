"""
AlphaPulse - Level Detection Engine  (Quality-First Model v2)
=============================================================
Detects Reversal Origin Levels using a strict wick + impulse model,
then scores each candidate on a 100-point quality scale and keeps only
the top-scoring levels.

Level types
───────────
  A-Level (resistance): swing high candle — strong upper wick + forward drop
  V-Level (support):    swing low candle  — strong lower wick + forward rise

Scoring model (100 pts total)
──────────────────────────────
  Displacement strength   25 pts  impulse move size after formation
  Wick quality            20 pts  rejection wick-to-body ratio
  Return distance         15 pts  how far price ran before revisiting
  Touch count freshness   15 pts  fewer touches = cleaner, more reliable
  Range extreme position  10 pts  level near recent high/low, not mid-range
  Psychological confluence 10 pts  alignment with round numbers
  Trend alignment          5 pts  H4 bias favours the level direction

Hard rejection (before scoring)
────────────────────────────────
  • break_count > MAX_LEVEL_BREAKS            → dead level, ignore
  • touch_count > MAX_LEVEL_TOUCHES           → overworked, no edge
  • level too close to current price          → no approach room
  • level in mid-range (inner 40% of range)  → structurally weak
  Crowding (after scoring):
  • weaker level within LEVEL_CROWDING_PIPS  → suppressed

Output caps
───────────
  major scope   → top 2 by quality score
  recent scope  → top 2 by quality score
  previous scope → top 1 by quality score

Configurable parameters (config/settings.py)
────────────────────────────────────────────
  MAX_LEVEL_TOUCHES, MAX_LEVEL_BREAKS,
  LEVEL_MIN_QUALITY_SCORE_{MAJOR,RECENT,PREVIOUS},
  LEVEL_MAX_PER_{MAJOR,RECENT,PREVIOUS},
  LEVEL_CROWDING_PIPS, LEVEL_RANGE_EXTREME_PCT, LEVEL_RANGE_LOOKBACK
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

from config.settings import (
    SWING_LOOKBACK, LEVEL_TOLERANCE_PIPS, PIP_SIZE,
    QM_BREAK_THRESHOLD,
    PREV_LEG_START, PREV_LEG_END,
    PSYCH_MAJOR_STEP, PSYCH_MEDIUM_STEP, PSYCH_MINOR_STEP,
    MAX_LEVEL_TOUCHES, MAX_LEVEL_BREAKS,
    LEVEL_MIN_QUALITY_MAJOR, LEVEL_MIN_QUALITY_RECENT, LEVEL_MIN_QUALITY_PREVIOUS,
    LEVEL_MAX_PER_MAJOR, LEVEL_MAX_PER_RECENT, LEVEL_MAX_PER_PREVIOUS,
    LEVEL_CROWDING_PIPS, LEVEL_RANGE_EXTREME_PCT, LEVEL_RANGE_LOOKBACK,
    MIN_GAP_CANDLES, GAP_BODY_MULTIPLIER,
    LEVEL_AV_QUALITY_BONUS, LEVEL_AV_QUALITY_THRESHOLD,
    LEVEL_AV_TRACE_ENABLED, LEVEL_AV_MIN_BODY_PIPS,
    LEVEL_AV_WICK_RATIO, LEVEL_AV_ORIGIN_MIN_WICK_RATIO,
    LEVEL_AV_ORIGIN_SCORE_BONUS,
    AV_MIN_DISPLACEMENT_PIPS,
    AV_MIN_DISTANCE_FROM_PRICE_PIPS,
    AV_MAX_BREAK_COUNT,
    AV_MID_RANGE_SCORE_PENALTY,
    AV_BROKEN_LEVEL_PENALTY,
    AV_BODY_HARD_REJECT_PIPS,
    AV_BODY_THIN_PENALTY,
    AV_BREAK_COUNT_SOFT_THRESHOLD,
    AV_BREAK_COUNT_PENALTY_PER,
    AV_TOUCH_COUNT_SOFT_THRESHOLD,
    AV_TOUCH_COUNT_PENALTY_PER,
    AV_ORIGIN_DISPLACEMENT_STRONG_PIPS,
    AV_ORIGIN_DISPLACEMENT_BONUS,
    GAP_REJECTION_WICK_TO_BODY_MIN,
    GAP_REJECTION_WICK_RANGE_PCT_MIN,
    GAP_REJECTION_MIN_BODY_PIPS,
    GAP_REJECTION_PUSH_AWAY_PIPS,
    DEBUG_REJECTION_TRACE,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────

@dataclass
class LevelInfo:
    price: float
    level_type: str      # "A" | "V" | "Gap"
    timeframe: str
    strength: float = 1.0    # legacy field, kept for downstream compatibility
    touch_count: int = 1
    confirmed: bool = False
    scope: str = "major"     # "major" | "recent" | "previous" | "psych"
    trade_direction: str = ""   # "BUY" | "SELL" (Gap levels need this explicitly)
    zone_low: float = 0.0       # used by imbalance/gap levels
    zone_high: float = 0.0

    # QM (Quasimodo) tracking
    break_count: int = 0
    is_qm: bool = False

    # Psychological level alignment
    is_psychological: bool = False
    psych_strength: str = ""   # "minor" | "medium" | "major"

    # Displacement (impulse move away from level)
    displacement_pips: float = 0.0

    # Quality scoring (new in v2)
    quality_score: float = 0.0           # 0-100 composite score
    return_distance_pips: float = 0.0   # excursion before first revisit
    selection_score: float = 0.0
    quality_breakdown: Dict[str, int] = field(default_factory=dict)
    accepted_reasons: List[str] = field(default_factory=list)
    rejected_reasons: List[str] = field(default_factory=list)
    origin_index: int = -1
    historical_rejection_count: int = 0
    quality_rejection_count: int = 0
    avg_rejection_wick_ratio: float = 0.0
    avg_push_away_pips: float = 0.0
    strongest_rejection_pips: float = 0.0
    rejection_quality_score: float = 0.0
    wick_ratio_pass_count: int = 0
    wick_percent_pass_count: int = 0
    strong_push_pass_count: int = 0

    def __repr__(self):
        return (
            f"LevelInfo({self.level_type} {self.price:.2f} [{self.timeframe}] "
            f"scope={self.scope} Q={self.quality_score:.0f}/{self.selection_score:.0f} "
            f"disp={self.displacement_pips:.0f}p tc={self.touch_count})"
        )

    def within_tolerance(self, price: float) -> bool:
        """True if the given price is within LEVEL_TOLERANCE_PIPS of this level."""
        return abs(price - self.price) <= (LEVEL_TOLERANCE_PIPS * PIP_SIZE)


# ─────────────────────────────────────────────────────────
# LEVEL DETECTOR CLASS
# ─────────────────────────────────────────────────────────

class LevelDetector:
    """
    Detects Reversal Origin Levels, scores them on a 100-point quality scale,
    applies hard rejection filters, rejects crowded duplicates, and returns only
    the highest-quality levels per scope.
    """

    # ── Candle quality thresholds ──────────────────────────────────────────────
    MIN_BODY_PIPS: float     = 5.0    # minimum candle body (avoids doji)
    MIN_WICK_RATIO: float    = 2.0    # wick must be >= 2× body
    FORWARD_WINDOW: int      = 10     # candles ahead to measure impulse move

    def __init__(self) -> None:
        # A/V candidates that were detected but lost the merge/crowding/top-N race.
        # Reset at the start of each detect_recent_legs call; appended by
        # detect_previous_leg. MultiTimeframeAnalyzer reads this for diversity selection.
        self._rejected_av_candidates: List[LevelInfo] = []

    @staticmethod
    def _gap_push_min_pips(timeframe: str) -> float:
        return float(GAP_REJECTION_PUSH_AWAY_PIPS.get(timeframe, 20.0))

    # ─────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────

    def detect_all(
        self,
        df: pd.DataFrame,
        timeframe: str,
        current_price: Optional[float] = None,
        h4_bias: str = "neutral",
        include_reversals: bool = True,
    ) -> List[LevelInfo]:
        """
        Major structure levels — highest-quality reversal origins from the full DataFrame.
        50-pip impulse minimum. Returns top LEVEL_MAX_PER_MAJOR levels.
        """
        min_bars_needed = SWING_LOOKBACK * 2 + self.FORWARD_WINDOW + 10
        if df is None or len(df) < min_bars_needed:
            logger.warning(
                "[%s] Insufficient data for major level detection (%d bars, need %d)",
                timeframe, len(df) if df is not None else 0, min_bars_needed,
            )
            return []

        levels: List[LevelInfo] = []
        if include_reversals:
            levels = self._detect_reversal_levels(
                df, timeframe,
                current_price=current_price,
                h4_bias=h4_bias,
                scope="major",
                min_quality=LEVEL_MIN_QUALITY_MAJOR,
                min_formation_bars=10,
                distance_filter_pips=30.0,
                min_impulse_pips=50.0,
            )
        else:
            logger.info("[%s] ACTIVE GAP-ONLY: skipping A/V major detection", timeframe)
        levels.extend(
            self._detect_gap_levels(
                df, timeframe,
                current_price=current_price,
                h4_bias=h4_bias,
                scope="major",
                min_quality=max(LEVEL_MIN_QUALITY_MAJOR, 48.0),
                distance_filter_pips=30.0,
            )
        )

        levels = self._merge_nearby_levels(levels, tol_pips=10.0)
        levels = self._reject_crowded(levels)
        levels = self._top_n(levels, LEVEL_MAX_PER_MAJOR)

        _log_level_type_counts(levels, timeframe, "major")
        logger.info(
            "[%s] Major levels: %d kept (top %d by quality)",
            timeframe, len(levels), LEVEL_MAX_PER_MAJOR,
        )
        return levels

    def detect_recent_legs(
        self,
        df: pd.DataFrame,
        timeframe: str,
        lookback: int = 50,
        current_price: Optional[float] = None,
        h4_bias: str = "neutral",
        include_reversals: bool = True,
    ) -> List[LevelInfo]:
        """
        Recent leg levels — from the last `lookback` candles.
        30-pip impulse minimum. Returns top LEVEL_MAX_PER_RECENT levels.
        """
        if df is None or len(df) < 10:
            return []

        recent_df = df.iloc[-min(lookback, len(df)):].reset_index(drop=True)

        self._rejected_av_candidates = []  # reset for each analysis cycle

        levels: List[LevelInfo] = []
        if include_reversals:
            levels = self._detect_reversal_levels(
                recent_df, timeframe,
                current_price=current_price,
                h4_bias=h4_bias,
                scope="recent",
                min_quality=LEVEL_MIN_QUALITY_RECENT,
                min_formation_bars=5,
                distance_filter_pips=15.0,
                min_impulse_pips=30.0,
            )
        else:
            logger.info("[%s] ACTIVE GAP-ONLY: skipping A/V recent-leg detection", timeframe)
        raw_av_recent = [l for l in levels if l.level_type in ("A", "V")]

        levels.extend(
            self._detect_gap_levels(
                recent_df, timeframe,
                current_price=current_price,
                h4_bias=h4_bias,
                scope="recent",
                min_quality=max(LEVEL_MIN_QUALITY_RECENT, 40.0),
                distance_filter_pips=15.0,
            )
        )

        levels = self._merge_nearby_levels(levels, tol_pips=10.0)
        levels = self._reject_crowded(levels)
        levels = self._top_n(levels, LEVEL_MAX_PER_RECENT)

        # Cache A/V candidates that didn't survive merge/crowding/top-N
        _tol = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        _final_prices = {l.price for l in levels}
        for _cand in raw_av_recent:
            if not any(abs(_cand.price - fp) <= _tol for fp in _final_prices):
                self._rejected_av_candidates.append(_cand)
                logger.info(
                    "A/V REJECTED: %s %.2f | Q=%.0f disp=%.0fp | "
                    "filtered from recent top-%d | [%s]",
                    _cand.level_type, _cand.price, _cand.quality_score,
                    _cand.displacement_pips, LEVEL_MAX_PER_RECENT, timeframe,
                )

        _log_level_type_counts(levels, timeframe, "recent")
        logger.info(
            "[%s] Recent-leg levels: %d kept (top %d by quality)",
            timeframe, len(levels), LEVEL_MAX_PER_RECENT,
        )
        return levels

    def detect_previous_leg(
        self,
        df: pd.DataFrame,
        timeframe: str,
        start: int = PREV_LEG_START,
        end: int = PREV_LEG_END,
        current_price: Optional[float] = None,
        h4_bias: str = "neutral",
        include_reversals: bool = True,
    ) -> List[LevelInfo]:
        """
        Previous leg levels — from df[-end:-start].
        25-pip impulse minimum. Returns top LEVEL_MAX_PER_PREVIOUS level.
        """
        n = len(df)
        if n < end:
            end = n
        if start >= end or end - start < 10:
            return []

        prev_df = df.iloc[-end:-start].reset_index(drop=True)

        levels: List[LevelInfo] = []
        if include_reversals:
            levels = self._detect_reversal_levels(
                prev_df, timeframe,
                current_price=current_price,
                h4_bias=h4_bias,
                scope="previous",
                min_quality=LEVEL_MIN_QUALITY_PREVIOUS,
                min_formation_bars=3,
                distance_filter_pips=15.0,
                min_impulse_pips=25.0,
            )
        else:
            logger.info("[%s] ACTIVE GAP-ONLY: skipping A/V previous-leg detection", timeframe)
        raw_av_prev = [l for l in levels if l.level_type in ("A", "V")]

        levels.extend(
            self._detect_gap_levels(
                prev_df, timeframe,
                current_price=current_price,
                h4_bias=h4_bias,
                scope="previous",
                min_quality=max(LEVEL_MIN_QUALITY_PREVIOUS, 36.0),
                distance_filter_pips=15.0,
            )
        )

        levels = self._merge_nearby_levels(levels, tol_pips=10.0)
        levels = self._reject_crowded(levels)
        levels = self._top_n(levels, LEVEL_MAX_PER_PREVIOUS)

        # Append previous-leg A/V rejects to the shared pool (recent already reset it)
        _tol = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        _final_prices = {l.price for l in levels}
        for _cand in raw_av_prev:
            if not any(abs(_cand.price - fp) <= _tol for fp in _final_prices):
                self._rejected_av_candidates.append(_cand)
                logger.info(
                    "A/V REJECTED: %s %.2f | Q=%.0f disp=%.0fp | "
                    "filtered from previous top-%d | [%s]",
                    _cand.level_type, _cand.price, _cand.quality_score,
                    _cand.displacement_pips, LEVEL_MAX_PER_PREVIOUS, timeframe,
                )

        _log_level_type_counts(levels, timeframe, "previous")
        logger.info(
            "[%s] Previous-leg levels: %d kept (top %d by quality)",
            timeframe, len(levels), LEVEL_MAX_PER_PREVIOUS,
        )
        return levels

    # ─────────────────────────────────────────────────────
    # CORE DETECTION
    # ─────────────────────────────────────────────────────

    def _detect_reversal_levels(
        self,
        df: pd.DataFrame,
        timeframe: str,
        current_price: Optional[float] = None,
        h4_bias: str = "neutral",
        scope: str = "major",
        min_quality: float = 40.0,
        min_formation_bars: int = 10,
        distance_filter_pips: float = 30.0,
        min_impulse_pips: float = 50.0,
    ) -> List[LevelInfo]:
        """
        Full detection loop — finds swing highs/lows, applies hard rejections,
        scores each candidate, and returns all that meet the quality minimum.
        """
        if df is None or len(df) < SWING_LOOKBACK + self.FORWARD_WINDOW + min_formation_bars:
            return []

        n      = len(df)
        opens  = df["open"].values.astype(float)
        highs  = df["high"].values.astype(float)
        lows   = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)

        body_min      = LEVEL_AV_MIN_BODY_PIPS * PIP_SIZE       # soft threshold (2.0–3.0p → penalty)
        hard_body_min = AV_BODY_HARD_REJECT_PIPS * PIP_SIZE    # hard reject only at 2.0p
        dist_min = distance_filter_pips * PIP_SIZE
        tol      = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        lb       = max(3, SWING_LOOKBACK)
        fw       = self.FORWARD_WINDOW

        candidates: List[LevelInfo] = []
        trace = {"raw_a": 0, "raw_v": 0, "kept_a": 0, "kept_v": 0, "rejected": 0}

        # ── A-levels (resistance): swing high + upper wick rejection ─────────
        for i in range(lb, n - lb):
            if highs[i] < np.max(highs[max(0, i - lb): i + lb + 1]) - 1e-9:
                continue  # not a local swing high
            if (n - 1 - i) < min_formation_bars:
                continue  # too fresh — not yet proven

            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            body       = abs(c - o)
            upper_wick = h - max(o, c)
            wick_ratio  = upper_wick / body if body > 0 else 0.0
            is_bullish_origin = c > o
            is_valid_wick_rejection = upper_wick >= LEVEL_AV_WICK_RATIO * body
            is_valid_origin_candle = is_bullish_origin and wick_ratio >= LEVEL_AV_ORIGIN_MIN_WICK_RATIO
            level_price = round(c if is_bullish_origin else max(o, c), 2)
            trace["raw_a"] += 1
            _trace_av(
                timeframe, scope,
                "RAW A DETECTED [%s] %s scope: price=%.2f | body=%.1fp wick=%.1fx bullish_origin=%s",
                level_price, body / PIP_SIZE, wick_ratio, is_bullish_origin,
            )

            if body < hard_body_min:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: A %.2f | reason=body too small %.1fp < %.1fp (hard min)",
                    level_price, body / PIP_SIZE, AV_BODY_HARD_REJECT_PIPS,
                )
                continue
            body_thin = body < body_min  # 2.0–3.0p: soft penalty applied after scoring
            if not (is_valid_wick_rejection or is_valid_origin_candle):
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: A %.2f | reason=not wick rejection or bullish reversal-origin candle (wick %.1fx)",
                    level_price, wick_ratio,
                )
                continue

            # ── Directional position — broken levels penalised, not rejected ──
            is_broken = False
            broken_pips = 0.0
            if current_price is not None:
                if level_price <= current_price:
                    broken_pips = (current_price - level_price) / PIP_SIZE
                    is_broken = True
                    _trace_av(
                        timeframe, scope,
                        "A/V PENALIZED [%s] %s scope: A %.2f | broken by %.1fp (price=%.2f) — penalty -%.0f (kept in pool)",
                        level_price, broken_pips, current_price, AV_BROKEN_LEVEL_PENALTY,
                    )
                else:
                    if level_price - current_price < AV_MIN_DISTANCE_FROM_PRICE_PIPS * PIP_SIZE:
                        trace["rejected"] += 1
                        _trace_av(
                            timeframe, scope,
                            "A/V REJECTED [%s] %s scope: A %.2f | reason=too close to current price %.1fp < %.1fp",
                            level_price, (level_price - current_price) / PIP_SIZE, AV_MIN_DISTANCE_FROM_PRICE_PIPS,
                        )
                        continue

            # ── Mid-range: score penalty, not hard rejection ──────────────────
            is_mid_range = not self._is_range_extreme(df, level_price, i)
            if is_mid_range:
                _trace_av(
                    timeframe, scope,
                    "A/V SOFT [%s] %s scope: A %.2f | mid-range position — scoring penalty -%.0f",
                    level_price, AV_MID_RANGE_SCORE_PENALTY,
                )

            # ── Forward impulse ───────────────────────────────────────────────
            fwd_end = min(n, i + 1 + fw)
            if fwd_end <= i + 1:
                continue
            impulse_pips = (level_price - float(np.min(lows[i + 1: fwd_end]))) / PIP_SIZE
            if impulse_pips < AV_MIN_DISPLACEMENT_PIPS:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: A %.2f | reason=displacement %.0fp < %.0fp min",
                    level_price, impulse_pips, AV_MIN_DISPLACEMENT_PIPS,
                )
                continue

            # ── Touch count (local estimate) ──────────────────────────────────
            touch_count = max(1, int(np.sum(
                (highs >= level_price - tol) & (closes < level_price + tol)
            )))

            # ── Break count — hard reject at ceiling only, penalty below ────────
            break_count = self._count_breaks(closes, level_price, "A", tol)
            if break_count > AV_BREAK_COUNT_SOFT_THRESHOLD:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: A %.2f | reason=break count %d > %d (too broken)",
                    level_price, break_count, AV_BREAK_COUNT_SOFT_THRESHOLD,
                )
                continue
            break_count_penalty = (
                max(0.0, (break_count - AV_MAX_BREAK_COUNT) * AV_BREAK_COUNT_PENALTY_PER)
                if break_count > AV_MAX_BREAK_COUNT else 0.0
            )

            # ── Touch count — hard reject at ceiling only, penalty below ─────
            if touch_count > AV_TOUCH_COUNT_SOFT_THRESHOLD:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: A %.2f | reason=touch count %d > %d (overworked)",
                    level_price, touch_count, AV_TOUCH_COUNT_SOFT_THRESHOLD,
                )
                continue
            touch_count_penalty = (
                max(0.0, (touch_count - MAX_LEVEL_TOUCHES) * AV_TOUCH_COUNT_PENALTY_PER)
                if touch_count > MAX_LEVEL_TOUCHES else 0.0
            )

            # ── Retest + return distance ──────────────────────────────────────
            retested, return_dist_pips = self._check_retest(
                closes, lows, level_price, "A", i, fw, n, tol
            )

            # ── Psychological confluence ───────────────────────────────────────
            is_psych, psych_str = _quick_psych_check(level_price)

            # ── Trend alignment ───────────────────────────────────────────────
            # A-level = SELL opportunity → aligned when H4 is bearish
            h4_aligned = (h4_bias == "bearish") or (h4_bias == "neutral")

            # ── Quality score ─────────────────────────────────────────────────
            quality, breakdown = self._score_level(
                impulse_pips=impulse_pips,
                wick_ratio=wick_ratio,
                touch_count=touch_count,
                retested=retested,
                return_distance_pips=return_dist_pips,
                df=df,
                level_price=level_price,
                level_idx=i,
                is_psychological=is_psych,
                psych_strength=psych_str,
                h4_aligned=h4_aligned,
                h4_bias=h4_bias,
            )
            if is_valid_origin_candle and not is_valid_wick_rejection:
                quality = min(100.0, quality + LEVEL_AV_ORIGIN_SCORE_BONUS)
                breakdown["origin"] = LEVEL_AV_ORIGIN_SCORE_BONUS
            # Origin + strong displacement bonus — structural quality reward
            if (is_valid_origin_candle or is_valid_wick_rejection) and impulse_pips >= AV_ORIGIN_DISPLACEMENT_STRONG_PIPS:
                quality = min(100.0, quality + AV_ORIGIN_DISPLACEMENT_BONUS)
                breakdown["orig_disp"] = AV_ORIGIN_DISPLACEMENT_BONUS
            if quality >= LEVEL_AV_QUALITY_THRESHOLD:
                quality = min(100.0, quality + LEVEL_AV_QUALITY_BONUS)
                breakdown["av"] = LEVEL_AV_QUALITY_BONUS
            # Apply accumulated penalties in priority order
            if is_mid_range:
                quality = max(0.0, quality - AV_MID_RANGE_SCORE_PENALTY)
                breakdown["mid_rng"] = -int(AV_MID_RANGE_SCORE_PENALTY)
            if is_broken:
                quality = max(0.0, quality - AV_BROKEN_LEVEL_PENALTY)
                breakdown["broken"] = -int(AV_BROKEN_LEVEL_PENALTY)
            if body_thin:
                quality = max(0.0, quality - AV_BODY_THIN_PENALTY)
                breakdown["thin_body"] = -int(AV_BODY_THIN_PENALTY)
            if break_count_penalty > 0:
                quality = max(0.0, quality - break_count_penalty)
                breakdown["excess_bc"] = -int(break_count_penalty)
            if touch_count_penalty > 0:
                quality = max(0.0, quality - touch_count_penalty)
                breakdown["excess_tc"] = -int(touch_count_penalty)

            if quality < min_quality:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: A %.2f | reason=quality %.0f < %.0f | %s",
                    level_price, quality, min_quality, _fmt_breakdown(breakdown),
                )
                continue

            _basis = "origin-based" if (is_valid_origin_candle and not is_valid_wick_rejection) else "wick-based"
            if not self._duplicate_exists(candidates, level_price):
                lvl = LevelInfo(
                    price=level_price,
                    level_type="A",
                    timeframe=timeframe,
                    strength=round(1.0 + quality / 100.0, 2),
                    touch_count=touch_count,
                    scope=scope,
                    trade_direction="SELL",
                    break_count=break_count,
                    is_psychological=is_psych,
                    psych_strength=psych_str,
                    displacement_pips=round(impulse_pips, 1),
                    quality_score=round(quality, 1),
                    return_distance_pips=round(return_dist_pips, 1),
                    selection_score=round(quality, 1),
                    quality_breakdown=breakdown,
                    accepted_reasons=[
                        f"A/V ACCEPTED ({_basis})",
                        f"{impulse_pips:.0f}p displacement",
                        "fresh structure" if touch_count <= 2 else f"tested tc={touch_count}",
                    ],
                    origin_index=i,
                )
                candidates.append(lvl)
                trace["kept_a"] += 1
                logger.info(
                    "A/V ACCEPTED [%s] SELL %.2f | Q=%.0f | %s | "
                    "disp=%.0fp wick=%.1fx tc=%d bc=%d | basis=%s%s%s",
                    timeframe, level_price, quality,
                    _fmt_breakdown(breakdown),
                    impulse_pips, wick_ratio, touch_count, break_count,
                    _basis,
                    " [BROKEN]" if is_broken else "",
                    " 🔮psych" if is_psych else "",
                )

        # ── V-levels (support): swing low + lower wick rejection ─────────────
        for i in range(lb, n - lb):
            if lows[i] > np.min(lows[max(0, i - lb): i + lb + 1]) + 1e-9:
                continue  # not a local swing low
            if (n - 1 - i) < min_formation_bars:
                continue

            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            body       = abs(c - o)
            lower_wick = min(o, c) - l
            wick_ratio  = lower_wick / body if body > 0 else 0.0
            is_bearish_origin = c < o
            is_valid_wick_rejection = lower_wick >= LEVEL_AV_WICK_RATIO * body
            is_valid_origin_candle = is_bearish_origin and wick_ratio >= LEVEL_AV_ORIGIN_MIN_WICK_RATIO
            level_price = round(c if is_bearish_origin else min(o, c), 2)
            trace["raw_v"] += 1
            _trace_av(
                timeframe, scope,
                "RAW V DETECTED [%s] %s scope: price=%.2f | body=%.1fp wick=%.1fx bearish_origin=%s",
                level_price, body / PIP_SIZE, wick_ratio, is_bearish_origin,
            )

            if body < hard_body_min:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: V %.2f | reason=body too small %.1fp < %.1fp (hard min)",
                    level_price, body / PIP_SIZE, AV_BODY_HARD_REJECT_PIPS,
                )
                continue
            body_thin = body < body_min  # 2.0–3.0p: soft penalty applied after scoring
            if not (is_valid_wick_rejection or is_valid_origin_candle):
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: V %.2f | reason=not wick rejection or bearish reversal-origin candle (wick %.1fx)",
                    level_price, wick_ratio,
                )
                continue

            # ── Directional position — broken levels penalised, not rejected ──
            is_broken = False
            broken_pips = 0.0
            if current_price is not None:
                if level_price >= current_price:
                    broken_pips = (level_price - current_price) / PIP_SIZE
                    is_broken = True
                    _trace_av(
                        timeframe, scope,
                        "A/V PENALIZED [%s] %s scope: V %.2f | broken by %.1fp (price=%.2f) — penalty -%.0f (kept in pool)",
                        level_price, broken_pips, current_price, AV_BROKEN_LEVEL_PENALTY,
                    )
                else:
                    if current_price - level_price < AV_MIN_DISTANCE_FROM_PRICE_PIPS * PIP_SIZE:
                        trace["rejected"] += 1
                        _trace_av(
                            timeframe, scope,
                            "A/V REJECTED [%s] %s scope: V %.2f | reason=too close to current price %.1fp < %.1fp",
                            level_price, (current_price - level_price) / PIP_SIZE, AV_MIN_DISTANCE_FROM_PRICE_PIPS,
                        )
                        continue

            # ── Mid-range: score penalty, not hard rejection ──────────────────
            is_mid_range = not self._is_range_extreme(df, level_price, i)
            if is_mid_range:
                _trace_av(
                    timeframe, scope,
                    "A/V SOFT [%s] %s scope: V %.2f | mid-range position — scoring penalty -%.0f",
                    level_price, AV_MID_RANGE_SCORE_PENALTY,
                )

            # ── Forward impulse ───────────────────────────────────────────────
            fwd_end = min(n, i + 1 + fw)
            if fwd_end <= i + 1:
                continue
            impulse_pips = (float(np.max(highs[i + 1: fwd_end])) - level_price) / PIP_SIZE
            if impulse_pips < AV_MIN_DISPLACEMENT_PIPS:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: V %.2f | reason=displacement %.0fp < %.0fp min",
                    level_price, impulse_pips, AV_MIN_DISPLACEMENT_PIPS,
                )
                continue

            # ── Touch count (local estimate) ──────────────────────────────────
            touch_count = max(1, int(np.sum(
                (lows <= level_price + tol) & (closes > level_price - tol)
            )))

            # ── Break count — hard reject at ceiling only, penalty below ────────
            break_count = self._count_breaks(closes, level_price, "V", tol)
            if break_count > AV_BREAK_COUNT_SOFT_THRESHOLD:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: V %.2f | reason=break count %d > %d (too broken)",
                    level_price, break_count, AV_BREAK_COUNT_SOFT_THRESHOLD,
                )
                continue
            break_count_penalty = (
                max(0.0, (break_count - AV_MAX_BREAK_COUNT) * AV_BREAK_COUNT_PENALTY_PER)
                if break_count > AV_MAX_BREAK_COUNT else 0.0
            )

            # ── Touch count — hard reject at ceiling only, penalty below ─────
            if touch_count > AV_TOUCH_COUNT_SOFT_THRESHOLD:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: V %.2f | reason=touch count %d > %d (overworked)",
                    level_price, touch_count, AV_TOUCH_COUNT_SOFT_THRESHOLD,
                )
                continue
            touch_count_penalty = (
                max(0.0, (touch_count - MAX_LEVEL_TOUCHES) * AV_TOUCH_COUNT_PENALTY_PER)
                if touch_count > MAX_LEVEL_TOUCHES else 0.0
            )

            # ── Retest + return distance ──────────────────────────────────────
            retested, return_dist_pips = self._check_retest(
                closes, highs, level_price, "V", i, fw, n, tol
            )

            # ── Psychological confluence ───────────────────────────────────────
            is_psych, psych_str = _quick_psych_check(level_price)

            # ── Trend alignment ───────────────────────────────────────────────
            # V-level = BUY opportunity → aligned when H4 is bullish
            h4_aligned = (h4_bias == "bullish") or (h4_bias == "neutral")

            # ── Quality score ─────────────────────────────────────────────────
            quality, breakdown = self._score_level(
                impulse_pips=impulse_pips,
                wick_ratio=wick_ratio,
                touch_count=touch_count,
                retested=retested,
                return_distance_pips=return_dist_pips,
                df=df,
                level_price=level_price,
                level_idx=i,
                is_psychological=is_psych,
                psych_strength=psych_str,
                h4_aligned=h4_aligned,
                h4_bias=h4_bias,
            )
            if is_valid_origin_candle and not is_valid_wick_rejection:
                quality = min(100.0, quality + LEVEL_AV_ORIGIN_SCORE_BONUS)
                breakdown["origin"] = LEVEL_AV_ORIGIN_SCORE_BONUS
            # Origin + strong displacement bonus — structural quality reward
            if (is_valid_origin_candle or is_valid_wick_rejection) and impulse_pips >= AV_ORIGIN_DISPLACEMENT_STRONG_PIPS:
                quality = min(100.0, quality + AV_ORIGIN_DISPLACEMENT_BONUS)
                breakdown["orig_disp"] = AV_ORIGIN_DISPLACEMENT_BONUS
            if quality >= LEVEL_AV_QUALITY_THRESHOLD:
                quality = min(100.0, quality + LEVEL_AV_QUALITY_BONUS)
                breakdown["av"] = LEVEL_AV_QUALITY_BONUS
            # Apply accumulated penalties in priority order
            if is_mid_range:
                quality = max(0.0, quality - AV_MID_RANGE_SCORE_PENALTY)
                breakdown["mid_rng"] = -int(AV_MID_RANGE_SCORE_PENALTY)
            if is_broken:
                quality = max(0.0, quality - AV_BROKEN_LEVEL_PENALTY)
                breakdown["broken"] = -int(AV_BROKEN_LEVEL_PENALTY)
            if body_thin:
                quality = max(0.0, quality - AV_BODY_THIN_PENALTY)
                breakdown["thin_body"] = -int(AV_BODY_THIN_PENALTY)
            if break_count_penalty > 0:
                quality = max(0.0, quality - break_count_penalty)
                breakdown["excess_bc"] = -int(break_count_penalty)
            if touch_count_penalty > 0:
                quality = max(0.0, quality - touch_count_penalty)
                breakdown["excess_tc"] = -int(touch_count_penalty)

            if quality < min_quality:
                trace["rejected"] += 1
                _trace_av(
                    timeframe, scope,
                    "A/V REJECTED [%s] %s scope: V %.2f | reason=quality %.0f < %.0f | %s",
                    level_price, quality, min_quality, _fmt_breakdown(breakdown),
                )
                continue

            _basis = "origin-based" if (is_valid_origin_candle and not is_valid_wick_rejection) else "wick-based"
            if not self._duplicate_exists(candidates, level_price):
                lvl = LevelInfo(
                    price=level_price,
                    level_type="V",
                    timeframe=timeframe,
                    strength=round(1.0 + quality / 100.0, 2),
                    touch_count=touch_count,
                    scope=scope,
                    trade_direction="BUY",
                    break_count=break_count,
                    is_psychological=is_psych,
                    psych_strength=psych_str,
                    displacement_pips=round(impulse_pips, 1),
                    quality_score=round(quality, 1),
                    return_distance_pips=round(return_dist_pips, 1),
                    selection_score=round(quality, 1),
                    quality_breakdown=breakdown,
                    accepted_reasons=[
                        f"A/V ACCEPTED ({_basis})",
                        f"{impulse_pips:.0f}p displacement",
                        "fresh structure" if touch_count <= 2 else f"tested tc={touch_count}",
                    ],
                    origin_index=i,
                )
                candidates.append(lvl)
                trace["kept_v"] += 1
                logger.info(
                    "A/V ACCEPTED [%s] BUY %.2f | Q=%.0f | %s | "
                    "disp=%.0fp wick=%.1fx tc=%d bc=%d | basis=%s%s%s",
                    timeframe, level_price, quality,
                    _fmt_breakdown(breakdown),
                    impulse_pips, wick_ratio, touch_count, break_count,
                    _basis,
                    " [BROKEN]" if is_broken else "",
                    " 🔮psych" if is_psych else "",
                )

        _trace_av(
            timeframe, scope,
            "A/V TRACE SUMMARY [%s] %s scope: raw A=%d raw V=%d kept A=%d kept V=%d rejected=%d",
            trace["raw_a"], trace["raw_v"], trace["kept_a"], trace["kept_v"], trace["rejected"],
        )
        logger.info(
            "A/V SURVIVED FILTERS [%s] %s scope: A=%d V=%d total=%d (raw A=%d V=%d, rejected=%d)",
            timeframe, scope,
            trace["kept_a"], trace["kept_v"], trace["kept_a"] + trace["kept_v"],
            trace["raw_a"], trace["raw_v"], trace["rejected"],
        )
        return candidates

    def _detect_gap_levels(
        self,
        df: pd.DataFrame,
        timeframe: str,
        current_price: Optional[float] = None,
        h4_bias: str = "neutral",
        scope: str = "major",
        min_quality: float = 45.0,
        distance_filter_pips: float = 20.0,
    ) -> List[LevelInfo]:
        """
        Detect clean imbalance / gap levels from three-candle fair-value gaps.

        Bullish gap:
          candle[i+1].low > candle[i-1].high with a strong bullish middle candle.
          Revisit idea = BUY from the gap midpoint.

        Bearish gap:
          candle[i+1].high < candle[i-1].low with a strong bearish middle candle.
          Revisit idea = SELL from the gap midpoint.
        """
        if df is None or len(df) < max(15, MIN_GAP_CANDLES + 3):
            return []

        opens = df["open"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)
        closes = df["close"].values.astype(float)
        n = len(df)
        dist_min = distance_filter_pips * PIP_SIZE
        tol = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        avg_body = float(np.mean(np.abs(closes - opens))) if n > 0 else 0.0
        avg_body = max(avg_body, 1.0)

        levels: List[LevelInfo] = []

        for i in range(1, n - 1):
            body = abs(closes[i] - opens[i])
            if body < avg_body * GAP_BODY_MULTIPLIER:
                continue

            bullish_mid = closes[i] > opens[i]
            bearish_mid = closes[i] < opens[i]

            if bullish_mid and lows[i + 1] > highs[i - 1]:
                zone_low = round(float(highs[i - 1]), 2)
                zone_high = round(float(lows[i + 1]), 2)
                midpoint = round((zone_low + zone_high) / 2.0, 2)
                gap_pips = (zone_high - zone_low) / PIP_SIZE
                impulse_pips = (
                    float(np.max(highs[i + 1: min(n, i + 1 + self.FORWARD_WINDOW)])) - midpoint
                ) / PIP_SIZE if i + 2 < n else 0.0
                if gap_pips <= 0 or impulse_pips < 20.0:
                    continue
                if current_price is not None:
                    if midpoint >= current_price or current_price - midpoint < dist_min:
                        continue
                if not self._is_range_extreme(df, midpoint, i):
                    continue

                (
                    touch_count,
                    break_count,
                    retested,
                    return_dist,
                    historical_rejection_count,
                    quality_rejection_count,
                    avg_rejection_wick_ratio,
                    avg_push_away_pips,
                    strongest_rejection_pips,
                    rejection_quality_score,
                    wick_ratio_pass_count,
                    wick_percent_pass_count,
                    strong_push_pass_count,
                ) = self._gap_state(
                    df, timeframe, midpoint, zone_low, zone_high, "BUY", i, tol
                )
                if break_count > MAX_LEVEL_BREAKS or touch_count > MAX_LEVEL_TOUCHES:
                    continue

                quality, breakdown = self._score_gap_level(
                    gap_pips=gap_pips,
                    body_ratio=body / avg_body,
                    impulse_pips=impulse_pips,
                    touch_count=touch_count,
                    retested=retested,
                    return_distance_pips=return_dist,
                    quality_rejection_count=quality_rejection_count,
                    rejection_quality_score=rejection_quality_score,
                    is_psychological=_quick_psych_check(midpoint)[0],
                    psych_strength=_quick_psych_check(midpoint)[1],
                    h4_aligned=h4_bias in ("bullish", "neutral"),
                    h4_bias=h4_bias,
                    df=df,
                    level_price=midpoint,
                    level_idx=i,
                )
                if quality < min_quality:
                    logger.debug(
                        "[%s] Bullish gap %.2f rejected - Q=%.0f < %.0f min | %s",
                        timeframe, midpoint, quality, min_quality, _fmt_breakdown(breakdown),
                    )
                    continue

                lvl = LevelInfo(
                    price=midpoint,
                    level_type="Gap",
                    timeframe=timeframe,
                    strength=round(1.0 + quality / 100.0, 2),
                    touch_count=touch_count,
                    scope=scope,
                    trade_direction="BUY",
                    zone_low=zone_low,
                    zone_high=zone_high,
                    break_count=break_count,
                    is_psychological=_quick_psych_check(midpoint)[0],
                    psych_strength=_quick_psych_check(midpoint)[1],
                    displacement_pips=round(max(gap_pips, impulse_pips), 1),
                    quality_score=round(quality, 1),
                    return_distance_pips=round(return_dist, 1),
                    selection_score=round(quality, 1),
                    quality_breakdown=breakdown,
                    accepted_reasons=[
                        "clean bullish imbalance",
                        f"gap {gap_pips:.0f}p / impulse {impulse_pips:.0f}p",
                        f"quality rejections {quality_rejection_count}",
                    ],
                    origin_index=i,
                    historical_rejection_count=historical_rejection_count,
                    quality_rejection_count=quality_rejection_count,
                    avg_rejection_wick_ratio=round(avg_rejection_wick_ratio, 2),
                    avg_push_away_pips=round(avg_push_away_pips, 1),
                    strongest_rejection_pips=round(strongest_rejection_pips, 1),
                    rejection_quality_score=round(rejection_quality_score, 1),
                    wick_ratio_pass_count=wick_ratio_pass_count,
                    wick_percent_pass_count=wick_percent_pass_count,
                    strong_push_pass_count=strong_push_pass_count,
                )
                levels.append(lvl)
                logger.info(
                    "[%s] BUY gap ACCEPTED: %.2f [%.2f-%.2f] | Q=%.0f | %s",
                    timeframe, midpoint, zone_low, zone_high, quality, _fmt_breakdown(breakdown),
                )

            if bearish_mid and highs[i + 1] < lows[i - 1]:
                zone_low = round(float(highs[i + 1]), 2)
                zone_high = round(float(lows[i - 1]), 2)
                midpoint = round((zone_low + zone_high) / 2.0, 2)
                gap_pips = (zone_high - zone_low) / PIP_SIZE
                impulse_pips = (
                    midpoint - float(np.min(lows[i + 1: min(n, i + 1 + self.FORWARD_WINDOW)]))
                ) / PIP_SIZE if i + 2 < n else 0.0
                if gap_pips <= 0 or impulse_pips < 20.0:
                    continue
                if current_price is not None:
                    if midpoint <= current_price or midpoint - current_price < dist_min:
                        continue
                if not self._is_range_extreme(df, midpoint, i):
                    continue

                (
                    touch_count,
                    break_count,
                    retested,
                    return_dist,
                    historical_rejection_count,
                    quality_rejection_count,
                    avg_rejection_wick_ratio,
                    avg_push_away_pips,
                    strongest_rejection_pips,
                    rejection_quality_score,
                    wick_ratio_pass_count,
                    wick_percent_pass_count,
                    strong_push_pass_count,
                ) = self._gap_state(
                    df, timeframe, midpoint, zone_low, zone_high, "SELL", i, tol
                )
                if break_count > MAX_LEVEL_BREAKS or touch_count > MAX_LEVEL_TOUCHES:
                    continue

                quality, breakdown = self._score_gap_level(
                    gap_pips=gap_pips,
                    body_ratio=body / avg_body,
                    impulse_pips=impulse_pips,
                    touch_count=touch_count,
                    retested=retested,
                    return_distance_pips=return_dist,
                    quality_rejection_count=quality_rejection_count,
                    rejection_quality_score=rejection_quality_score,
                    is_psychological=_quick_psych_check(midpoint)[0],
                    psych_strength=_quick_psych_check(midpoint)[1],
                    h4_aligned=h4_bias in ("bearish", "neutral"),
                    h4_bias=h4_bias,
                    df=df,
                    level_price=midpoint,
                    level_idx=i,
                )
                if quality < min_quality:
                    logger.debug(
                        "[%s] Bearish gap %.2f rejected - Q=%.0f < %.0f min | %s",
                        timeframe, midpoint, quality, min_quality, _fmt_breakdown(breakdown),
                    )
                    continue

                lvl = LevelInfo(
                    price=midpoint,
                    level_type="Gap",
                    timeframe=timeframe,
                    strength=round(1.0 + quality / 100.0, 2),
                    touch_count=touch_count,
                    scope=scope,
                    trade_direction="SELL",
                    zone_low=zone_low,
                    zone_high=zone_high,
                    break_count=break_count,
                    is_psychological=_quick_psych_check(midpoint)[0],
                    psych_strength=_quick_psych_check(midpoint)[1],
                    displacement_pips=round(max(gap_pips, impulse_pips), 1),
                    quality_score=round(quality, 1),
                    return_distance_pips=round(return_dist, 1),
                    selection_score=round(quality, 1),
                    quality_breakdown=breakdown,
                    accepted_reasons=[
                        "clean bearish imbalance",
                        f"gap {gap_pips:.0f}p / impulse {impulse_pips:.0f}p",
                        f"quality rejections {quality_rejection_count}",
                    ],
                    origin_index=i,
                    historical_rejection_count=historical_rejection_count,
                    quality_rejection_count=quality_rejection_count,
                    avg_rejection_wick_ratio=round(avg_rejection_wick_ratio, 2),
                    avg_push_away_pips=round(avg_push_away_pips, 1),
                    strongest_rejection_pips=round(strongest_rejection_pips, 1),
                    rejection_quality_score=round(rejection_quality_score, 1),
                    wick_ratio_pass_count=wick_ratio_pass_count,
                    wick_percent_pass_count=wick_percent_pass_count,
                    strong_push_pass_count=strong_push_pass_count,
                )
                levels.append(lvl)
                logger.info(
                    "[%s] SELL gap ACCEPTED: %.2f [%.2f-%.2f] | Q=%.0f | %s",
                    timeframe, midpoint, zone_low, zone_high, quality, _fmt_breakdown(breakdown),
                )

        return levels

    # ─────────────────────────────────────────────────────
    # 100-POINT QUALITY SCORING
    # ─────────────────────────────────────────────────────

    def _score_level(
        self,
        impulse_pips: float,
        wick_ratio: float,
        touch_count: int,
        retested: bool,
        return_distance_pips: float,
        df: pd.DataFrame,
        level_price: float,
        level_idx: int,
        is_psychological: bool,
        psych_strength: str,
        h4_aligned: bool,
        h4_bias: str,
    ) -> Tuple[float, Dict[str, int]]:
        """
        100-point interpretable quality score.

        Component               Max   Notes
        ──────────────────────────────────────────────────────
        Displacement strength    25   institutional impulse size
        Wick quality             20   clean rejection candle
        Return distance          15   fresh = more reliable
        Touch count freshness    15   fewer touches = cleaner level
        Range extreme position   10   near range high/low, not mid-range
        Psychological confluence 10   round-number alignment
        Trend alignment           5   H4 bias matches level direction
        ──────────────────────────────────────────────────────
        """
        b: Dict[str, int] = {}

        # 1. Displacement strength (0–25)
        if   impulse_pips >= 120: b["disp"] = 25
        elif impulse_pips >= 100: b["disp"] = 22
        elif impulse_pips >=  80: b["disp"] = 18
        elif impulse_pips >=  60: b["disp"] = 14
        elif impulse_pips >=  40: b["disp"] = 10
        elif impulse_pips >=  25: b["disp"] =  6
        else:                     b["disp"] =  3

        # 2. Wick quality (0–20)
        if   wick_ratio >= 6.0: b["wick"] = 20
        elif wick_ratio >= 4.0: b["wick"] = 16
        elif wick_ratio >= 3.0: b["wick"] = 12
        elif wick_ratio >= 2.5: b["wick"] =  8
        elif wick_ratio >= 2.0: b["wick"] =  4
        else:                   b["wick"] =  0

        # 3. Return distance / freshness (0–15)
        if not retested:
            b["ret_dist"] = 15                           # never revisited
        elif return_distance_pips >= 120: b["ret_dist"] = 12
        elif return_distance_pips >=  80: b["ret_dist"] =  9
        elif return_distance_pips >=  50: b["ret_dist"] =  6
        elif return_distance_pips >=  25: b["ret_dist"] =  3
        else:                             b["ret_dist"] =  1

        # 4. Touch count freshness (0–15)
        if   touch_count == 1: b["fresh"] = 15
        elif touch_count == 2: b["fresh"] = 10
        elif touch_count == 3: b["fresh"] =  5
        elif touch_count == 4: b["fresh"] =  2
        else:                  b["fresh"] =  0

        # 5. Range extreme position (0–10)
        in_extreme = self._is_range_extreme(df, level_price, level_idx)
        b["range"] = 10 if in_extreme else 0

        # 6. Psychological confluence (0–10)
        b["psych"] = {"major": 10, "medium": 6, "minor": 3}.get(psych_strength, 0) \
                     if is_psychological else 0

        # 7. Trend alignment (0–5)
        if h4_bias == "neutral":
            b["trend"] = 3   # neutral: slight bonus (both directions allowed)
        elif h4_aligned:
            b["trend"] = 5   # level direction matches H4 bias
        else:
            b["trend"] = 0   # counter-trend: no bonus

        total = float(sum(b.values()))
        return min(100.0, total), b

    def _score_gap_level(
        self,
        gap_pips: float,
        body_ratio: float,
        impulse_pips: float,
        touch_count: int,
        retested: bool,
        return_distance_pips: float,
        quality_rejection_count: int,
        rejection_quality_score: float,
        is_psychological: bool,
        psych_strength: str,
        h4_aligned: bool,
        h4_bias: str,
        df: pd.DataFrame,
        level_price: float,
        level_idx: int,
    ) -> Tuple[float, Dict[str, int]]:
        """100-point score for imbalance/gap levels."""
        b: Dict[str, int] = {}

        if gap_pips >= 20:
            b["gap"] = 20
        elif gap_pips >= 12:
            b["gap"] = 16
        elif gap_pips >= 8:
            b["gap"] = 12
        elif gap_pips >= 5:
            b["gap"] = 8
        else:
            b["gap"] = 4

        if body_ratio >= 3.0:
            b["disp"] = 22
        elif body_ratio >= 2.2:
            b["disp"] = 18
        elif body_ratio >= 1.8:
            b["disp"] = 14
        else:
            b["disp"] = 10

        if impulse_pips >= 120:
            b["move"] = 15
        elif impulse_pips >= 80:
            b["move"] = 12
        elif impulse_pips >= 50:
            b["move"] = 9
        else:
            b["move"] = 5

        if not retested:
            b["fresh"] = 15
        elif touch_count == 1:
            b["fresh"] = 10
        elif touch_count == 2:
            b["fresh"] = 6
        else:
            b["fresh"] = 2

        if return_distance_pips >= 100:
            b["return"] = 10
        elif return_distance_pips >= 60:
            b["return"] = 7
        elif return_distance_pips >= 30:
            b["return"] = 4
        else:
            b["return"] = 1

        b["range"] = 10 if self._is_range_extreme(df, level_price, level_idx) else 0
        b["psych"] = {"major": 5, "medium": 3, "minor": 1}.get(psych_strength, 0) if is_psychological else 0

        if h4_bias == "neutral":
            b["trend"] = 2
        elif h4_aligned:
            b["trend"] = 3
        else:
            b["trend"] = 0

        if quality_rejection_count >= 5:
            b["rejection"] = 10
        elif quality_rejection_count == 4:
            b["rejection"] = 8
        elif quality_rejection_count == 3:
            b["rejection"] = 6
        elif quality_rejection_count == 2:
            b["rejection"] = 3
        elif quality_rejection_count == 1:
            b["rejection"] = 1
        else:
            b["rejection"] = 0

        if rejection_quality_score >= 80:
            b["rej_q"] = 2
        elif rejection_quality_score >= 60:
            b["rej_q"] = 1

        total = float(sum(b.values()))
        return min(100.0, total), b

    # ─────────────────────────────────────────────────────
    # RANGE EXTREME CHECK
    # ─────────────────────────────────────────────────────

    def _is_range_extreme(
        self,
        df: pd.DataFrame,
        level_price: float,
        level_idx: int,
    ) -> bool:
        """
        True when the level sits in the outer LEVEL_RANGE_EXTREME_PCT of the
        recent trading range (i.e. near a range high or range low).

        Uses the LEVEL_RANGE_LOOKBACK candles ending at the level formation bar
        so the range is computed from the context the level formed in.
        """
        lookback = LEVEL_RANGE_LOOKBACK
        start    = max(0, level_idx - lookback)
        window   = df.iloc[start: level_idx + 1]

        if len(window) < 5:
            return True   # not enough context — don't reject

        range_high = float(window["high"].max())
        range_low  = float(window["low"].min())
        range_size = range_high - range_low

        if range_size < 1e-9:
            return True   # degenerate range — don't reject

        band = range_size * LEVEL_RANGE_EXTREME_PCT
        near_high = abs(level_price - range_high) <= band
        near_low  = abs(level_price - range_low)  <= band

        if not (near_high or near_low):
            logger.debug(
                "Range check: %.2f is mid-range [%.2f–%.2f] "
                "(outer band=%.2f, pct=%.0f%%)",
                level_price, range_low, range_high, band,
                LEVEL_RANGE_EXTREME_PCT * 100,
            )
        return near_high or near_low

    # ─────────────────────────────────────────────────────
    # RETEST + RETURN DISTANCE
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _check_retest(
        closes: np.ndarray,
        extremes_for_dist: np.ndarray,   # lows for A-level, highs for V-level
        level_price: float,
        level_type: str,
        formation_idx: int,
        forward_window: int,
        n: int,
        tol: float,
    ) -> Tuple[bool, float]:
        """
        Returns (retested: bool, return_distance_pips: float).

        return_distance: maximum excursion from the level before the first retest.
        For A-level: max drop below level_price in the post-formation window.
        For V-level: max rise above level_price in the post-formation window.
        """
        post_start = formation_idx + 1 + forward_window
        retested   = False
        first_retest_idx = n  # default: never retested

        for k in range(post_start, n):
            if abs(closes[k] - level_price) <= tol:
                retested = True
                first_retest_idx = k
                break

        # Compute return distance from the end of the forward window to first retest
        excursion_end = first_retest_idx  # up to (not including) retest candle
        if post_start < excursion_end and post_start < n:
            post_slice = extremes_for_dist[post_start: min(excursion_end, n)]
            if level_type == "A":
                # Drop below level
                return_dist_pips = max(0.0, (level_price - float(np.min(post_slice))) / PIP_SIZE)
            else:
                # Rise above level
                return_dist_pips = max(0.0, (float(np.max(post_slice)) - level_price) / PIP_SIZE)
        else:
            return_dist_pips = 0.0

        return retested, return_dist_pips

    @staticmethod
    def _gap_state(
        df: pd.DataFrame,
        timeframe: str,
        midpoint: float,
        zone_low: float,
        zone_high: float,
        trade_direction: str,
        formation_idx: int,
        tol: float,
    ) -> Tuple[int, int, bool, float, int, int, float, float, float, float, int, int, int]:
        """
        Estimate freshness, break count, and return distance for a gap level.
        """
        post = df.iloc[formation_idx + 1:].copy()
        if post.empty:
            return 1, 0, False, 0.0, 0, 0, 0.0, 0.0, 0.0, 0.0

        opens = post["open"].values.astype(float)
        highs = post["high"].values.astype(float)
        lows = post["low"].values.astype(float)
        closes = post["close"].values.astype(float)

        touch_mask = (highs >= zone_low - tol) & (lows <= zone_high + tol)
        touch_indices = np.where(touch_mask)[0]
        historical_rejection_count = int(len(touch_indices))

        if trade_direction == "BUY":
            wrong_side = closes < (zone_low - tol)
            return_dist = max(0.0, (float(np.max(highs)) - midpoint) / PIP_SIZE) if len(highs) > 0 else 0.0
        else:
            wrong_side = closes > (zone_high + tol)
            return_dist = max(0.0, (midpoint - float(np.min(lows))) / PIP_SIZE) if len(lows) > 0 else 0.0

        episodes, in_break = 0, False
        for broken in wrong_side:
            if broken and not in_break:
                episodes += 1
                in_break = True
            elif not broken:
                in_break = False

        retested = bool(np.any(touch_mask))
        push_min_pips = LevelDetector._gap_push_min_pips(timeframe)
        wick_ratios: List[float] = []
        push_aways: List[float] = []
        weak_count = 0
        broke_count = 0
        wick_ratio_pass_count = 0
        wick_percent_pass_count = 0
        strong_push_pass_count = 0

        for pos, idx in enumerate(touch_indices):
            o, h, l, c = opens[idx], highs[idx], lows[idx], closes[idx]
            candle_range = max(h - l, 1e-9)
            body = abs(c - o)

            if trade_direction == "SELL":
                wick = h - max(o, c)
                wick_pct = wick / candle_range
                broke_through = c > (zone_high + tol)
                close_deep_inside = zone_low <= c <= zone_high and c > midpoint
            else:
                wick = min(o, c) - l
                wick_pct = wick / candle_range
                broke_through = c < (zone_low - tol)
                close_deep_inside = zone_low <= c <= zone_high and c < midpoint

            wick_ratio = wick / body if body > 0 else 0.0
            next_touch = int(touch_indices[pos + 1]) if pos + 1 < len(touch_indices) else len(post)
            follow_slice_highs = highs[idx + 1:next_touch]
            follow_slice_lows = lows[idx + 1:next_touch]

            if trade_direction == "SELL":
                push_away = max(0.0, (zone_low - float(np.min(follow_slice_lows))) / PIP_SIZE) if len(follow_slice_lows) > 0 else 0.0
                close_away_from_zone = c < zone_low
            else:
                push_away = max(0.0, (float(np.max(follow_slice_highs)) - zone_high) / PIP_SIZE) if len(follow_slice_highs) > 0 else 0.0
                close_away_from_zone = c > zone_high

            wick_ratio_pass = wick_ratio >= GAP_REJECTION_WICK_TO_BODY_MIN and push_away >= push_min_pips
            wick_percent_pass = wick_pct >= GAP_REJECTION_WICK_RANGE_PCT_MIN and push_away >= push_min_pips
            strong_push_pass = push_away >= (2.0 * push_min_pips) and close_away_from_zone

            if broke_through:
                broke_count += 1
                if DEBUG_REJECTION_TRACE:
                    logger.info("REJECTION IGNORED: price broke through zone")
                continue
            if body < GAP_REJECTION_MIN_BODY_PIPS * PIP_SIZE:
                weak_count += 1
                if DEBUG_REJECTION_TRACE:
                    logger.info(
                        "REJECTION IGNORED: weak touch | wick=%.1fx | push=%.0fp",
                        wick_ratio,
                        push_away,
                    )
                continue
            if close_deep_inside:
                weak_count += 1
                if DEBUG_REJECTION_TRACE:
                    logger.info(
                        "REJECTION IGNORED: weak touch | wick=%.1fx | push=%.0fp",
                        wick_ratio,
                        push_away,
                    )
                continue
            if not (wick_ratio_pass or wick_percent_pass or strong_push_pass):
                weak_count += 1
                if DEBUG_REJECTION_TRACE:
                    logger.info(
                        "REJECTION IGNORED: weak touch | wick=%.1fx | push=%.0fp",
                        wick_ratio,
                        push_away,
                    )
                continue

            wick_ratios.append(wick_ratio)
            push_aways.append(push_away)
            if wick_ratio_pass:
                wick_ratio_pass_count += 1
            if wick_percent_pass:
                wick_percent_pass_count += 1
            if strong_push_pass:
                strong_push_pass_count += 1
            if DEBUG_REJECTION_TRACE:
                logger.info(
                    "QUALITY REJECTION COUNTED: %s zone | wick=%.1fx | push=%.0fp",
                    trade_direction,
                    wick_ratio,
                    push_away,
                )

        quality_rejection_count = len(wick_ratios)
        avg_rejection_wick_ratio = float(np.mean(wick_ratios)) if wick_ratios else 0.0
        avg_push_away_pips = float(np.mean(push_aways)) if push_aways else 0.0
        strongest_rejection_pips = float(np.max(push_aways)) if push_aways else 0.0

        if quality_rejection_count >= 5:
            rejection_quality_score = 100.0
        elif quality_rejection_count == 4:
            rejection_quality_score = 85.0
        elif quality_rejection_count == 3:
            rejection_quality_score = 70.0
        elif quality_rejection_count == 2:
            rejection_quality_score = 45.0
        elif quality_rejection_count == 1:
            rejection_quality_score = 20.0
        else:
            rejection_quality_score = 0.0

        if historical_rejection_count > 0:
            logger.info(
                "REJECTION SUMMARY: zone=%s %.2f | touches=%d | quality=%d | weak=%d | broke=%d | avg_wick=%.1fx | avg_push=%.0fp",
                trade_direction,
                midpoint,
                historical_rejection_count,
                quality_rejection_count,
                weak_count,
                broke_count,
                avg_rejection_wick_ratio,
                avg_push_away_pips,
            )

        touch_count = max(1, quality_rejection_count) if retested else 1
        return (
            touch_count,
            episodes,
            retested,
            return_dist,
            historical_rejection_count,
            quality_rejection_count,
            avg_rejection_wick_ratio,
            avg_push_away_pips,
            strongest_rejection_pips,
            rejection_quality_score,
            wick_ratio_pass_count,
            wick_percent_pass_count,
            strong_push_pass_count,
        )

    # ─────────────────────────────────────────────────────
    # BREAK COUNT
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _count_breaks(
        closes: np.ndarray,
        level_price: float,
        level_type: str,
        tol: float,
    ) -> int:
        """Count distinct episodes where price closed clearly through the level."""
        if level_type == "A":
            wrong_side = closes > (level_price + tol)
        else:
            wrong_side = closes < (level_price - tol)

        episodes, in_break = 0, False
        for b in wrong_side:
            if b and not in_break:
                episodes += 1
                in_break = True
            elif not b:
                in_break = False
        return episodes

    # ─────────────────────────────────────────────────────
    # CROWDING REJECTION
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _reject_crowded(levels: List[LevelInfo]) -> List[LevelInfo]:
        """
        Remove weaker levels that are too close to a stronger level.
        Sorts by quality_score descending, then suppresses any level within
        LEVEL_CROWDING_PIPS of a higher-scored already-kept level.
        """
        if not levels:
            return []

        tol    = LEVEL_CROWDING_PIPS * PIP_SIZE
        sorted_lvls = sorted(levels, key=lambda l: l.quality_score, reverse=True)
        kept: List[LevelInfo] = []

        for lvl in sorted_lvls:
            crowded_by = next(
                (
                    k for k in kept
                    if abs(lvl.price - k.price) <= tol
                    and (not lvl.trade_direction or lvl.trade_direction == k.trade_direction)
                    and not (
                        {lvl.level_type, k.level_type} == {"Gap", "A"}
                        or {lvl.level_type, k.level_type} == {"Gap", "V"}
                    )
                ),
                None,
            )
            if crowded_by:
                logger.info(
                    "LEVEL SUPPRESSED: %s %.2f [%s] Q=%.0f — "
                    "crowded by stronger %s %.2f Q=%.0f (within %.0f pips)",
                    lvl.level_type, lvl.price, lvl.timeframe, lvl.quality_score,
                    crowded_by.level_type, crowded_by.price, crowded_by.quality_score,
                    LEVEL_CROWDING_PIPS,
                )
            else:
                kept.append(lvl)

        return kept

    # ─────────────────────────────────────────────────────
    # RANKING HELPER
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _top_n(levels: List[LevelInfo], n: int) -> List[LevelInfo]:
        """Return the top n levels by quality_score, preserving price order."""
        if len(levels) <= n:
            return sorted(levels, key=lambda l: l.price)
        top = sorted(
            levels,
            key=lambda l: (l.selection_score or l.quality_score, l.quality_score),
            reverse=True,
        )[:n]
        return sorted(top, key=lambda l: l.price)

    # ─────────────────────────────────────────────────────
    # QM FLAG COMPUTATION  (called by multi_timeframe.py)
    # ─────────────────────────────────────────────────────

    def compute_qm_flags(
        self, levels: List[LevelInfo], df: pd.DataFrame
    ) -> List[LevelInfo]:
        """
        Recompute break_count from the full lower-TF dataframe and set is_qm flag.
        Overrides the local break_count estimate from detection.
        """
        tol = LEVEL_TOLERANCE_PIPS * PIP_SIZE * 2
        for level in levels:
            closes = df["close"].values
            side = level.level_type
            if side == "Gap":
                side = "A" if level.trade_direction == "SELL" else "V"
            level.break_count = self._count_breaks(
                closes, level.price, side, tol
            )
            level.is_qm = level.break_count >= QM_BREAK_THRESHOLD
        return levels

    # ─────────────────────────────────────────────────────
    # TOUCH COUNT  (called by multi_timeframe.py)
    # ─────────────────────────────────────────────────────

    def compute_touch_counts(
        self, levels: List[LevelInfo], df: pd.DataFrame
    ) -> List[LevelInfo]:
        """Recompute touch_count from the full lower-TF dataframe."""
        if df is None or len(df) < 2:
            return levels

        tol    = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values

        for level in levels:
            lp = level.price
            side = level.level_type
            if side == "Gap":
                side = "A" if level.trade_direction == "SELL" else "V"
            if side == "A":
                count = int(np.sum((highs >= lp - tol) & (closes < lp + tol)))
            else:
                count = int(np.sum((lows <= lp + tol) & (closes > lp - tol)))
            level.touch_count = max(1, count)

        return levels

    # ─────────────────────────────────────────────────────
    # PSYCHOLOGICAL FLAGS  (called by multi_timeframe.py)
    # ─────────────────────────────────────────────────────

    def compute_psych_flags(self, levels: List[LevelInfo]) -> List[LevelInfo]:
        """Mark levels that align with round-number prices."""
        for level in levels:
            is_p, strength = _quick_psych_check(level.price)
            if is_p:
                level.is_psychological = True
                level.psych_strength   = strength
        return levels

    # ─────────────────────────────────────────────────────
    # PSYCHOLOGICAL LEVEL GENERATION
    # ─────────────────────────────────────────────────────

    def generate_psych_levels(
        self, current_price: float, timeframe: str, price_range: float = 200.0
    ) -> List[LevelInfo]:
        """
        Generate standalone psychological levels in current_price ± price_range.
        Only MAJOR and MEDIUM increments emitted to limit clutter.
        """
        tol  = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        low  = current_price - price_range
        high = current_price + price_range
        levels: List[LevelInfo] = []

        for step, strength in [(PSYCH_MAJOR_STEP, "major"), (PSYCH_MEDIUM_STEP, "medium")]:
            val = float(int(low / step) * step)
            while val <= high:
                if abs(val - current_price) > tol:
                    lt = "A" if val > current_price else "V"
                    lvl = LevelInfo(
                        price=round(val, 2),
                        level_type=lt,
                        timeframe=timeframe,
                        strength=1.2 if strength == "major" else 1.0,
                        scope="psych",
                        trade_direction="SELL" if lt == "A" else "BUY",
                        is_psychological=True,
                        psych_strength=strength,
                        quality_score={"major": 30.0, "medium": 20.0}.get(strength, 10.0),
                        selection_score={"major": 30.0, "medium": 20.0}.get(strength, 10.0),
                    )
                    levels.append(lvl)
                val = round(val + step, 2)

        return _merge_nearby_levels(levels)

    # ─────────────────────────────────────────────────────
    # UTILITIES
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _duplicate_exists(levels: List[LevelInfo], price: float) -> bool:
        tol = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        return any(abs(l.price - price) <= tol for l in levels)

    # Keep for downstream compatibility
    @staticmethod
    def _merge_nearby_levels(
        levels: List[LevelInfo],
        tol_pips: float = 0.0,
    ) -> List[LevelInfo]:
        return _merge_nearby_levels(levels, tol_pips)


# ─────────────────────────────────────────────────────────
# MODULE-LEVEL HELPERS
# ─────────────────────────────────────────────────────────

def _quick_psych_check(price: float) -> Tuple[bool, str]:
    """Return (is_psychological, strength) for a price."""
    tol = LEVEL_TOLERANCE_PIPS * PIP_SIZE
    if _is_near_multiple(price, PSYCH_MAJOR_STEP, tol):
        return True, "major"
    if _is_near_multiple(price, PSYCH_MEDIUM_STEP, tol):
        return True, "medium"
    if _is_near_multiple(price, PSYCH_MINOR_STEP, tol):
        return True, "minor"
    return False, ""


def _is_near_multiple(price: float, step: float, tol: float) -> bool:
    remainder = price % step
    return remainder <= tol or (step - remainder) <= tol


def _fmt_breakdown(b: Dict[str, int]) -> str:
    """Format scoring breakdown for log output."""
    return " ".join(f"{k}={v}" for k, v in b.items())


def _trace_av(timeframe: str, scope: str, message: str, *args) -> None:
    if LEVEL_AV_TRACE_ENABLED:
        logger.info(message, timeframe, scope, *args)


def _log_level_type_counts(levels: List[LevelInfo], timeframe: str, scope: str) -> None:
    """Log A/V/Gap counts for the given level list at a pipeline stage."""
    a_count = sum(1 for l in levels if l.level_type == "A")
    v_count = sum(1 for l in levels if l.level_type == "V")
    g_count = sum(1 for l in levels if l.level_type == "Gap")
    if a_count or v_count:
        for lvl in levels:
            if lvl.level_type in ("A", "V"):
                logger.info(
                    "A/V DETECTED [%s] %s scope: %s %.2f | Q=%.0f disp=%.0fp",
                    timeframe, scope, lvl.level_type, lvl.price,
                    lvl.quality_score, lvl.displacement_pips,
                )
    logger.info(
        "LEVEL TYPE COUNTS [%s] %s scope: A=%d V=%d Gap=%d total=%d",
        timeframe, scope, a_count, v_count, g_count, len(levels),
    )


def _merge_nearby_levels(
    levels: List[LevelInfo],
    tol_pips: float = 0.0,
) -> List[LevelInfo]:
    """
    Merge near-duplicates without allowing Gap levels to erase valid A/V
    structure. Gap logic remains intact; the best Gap and best A/V candidate
    can both survive the merge so selector/top-N can make the final choice.
    """
    if not levels:
        return []

    tol = (tol_pips * PIP_SIZE) if tol_pips > 0 else (LEVEL_TOLERANCE_PIPS * PIP_SIZE)
    sorted_lvls = sorted(levels, key=lambda l: l.price)
    merged: List[LevelInfo] = []
    skip: set = set()

    for i, level in enumerate(sorted_lvls):
        if i in skip:
            continue
        cluster = [level]
        for j in range(i + 1, len(sorted_lvls)):
            if abs(sorted_lvls[j].price - level.price) <= tol:
                cluster.append(sorted_lvls[j])
                skip.add(j)

        gap_candidates = [l for l in cluster if l.level_type == "Gap"]
        av_candidates = [l for l in cluster if l.level_type in ("A", "V")]
        other_candidates = [l for l in cluster if l.level_type not in ("A", "V", "Gap")]

        winners: List[LevelInfo] = []
        if gap_candidates:
            winners.append(max(gap_candidates, key=lambda l: (l.quality_score, l.displacement_pips)))
        if av_candidates:
            winners.append(max(av_candidates, key=lambda l: (l.quality_score, l.displacement_pips)))
        if not winners and other_candidates:
            winners.append(max(other_candidates, key=lambda l: (l.quality_score, l.displacement_pips)))

        if gap_candidates and av_candidates:
            best_gap = winners[0]
            best_av = winners[1] if len(winners) > 1 else None
            if best_av:
                logger.info(
                    "A/V PRESERVED NEAR GAP: %s %.2f (Q=%.0f) kept beside Gap %.2f (Q=%.0f) within %.0fpips",
                    best_av.level_type, best_av.price, best_av.quality_score,
                    best_gap.price, best_gap.quality_score,
                    abs(best_av.price - best_gap.price) / PIP_SIZE,
                )

        for best in winners:
            same_type_cluster = [l for l in cluster if l.level_type == best.level_type]
            best.touch_count = max(best.touch_count, len(same_type_cluster))
            if any(l.is_qm for l in same_type_cluster):
                best.is_qm = True
                best.break_count = max(l.break_count for l in same_type_cluster)
            if any(l.is_psychological for l in same_type_cluster):
                best.is_psychological = True
                strengths = [l.psych_strength for l in same_type_cluster if l.psych_strength]
                if strengths:
                    best.psych_strength = max(
                        strengths,
                        key=lambda s: {"major": 3, "medium": 2, "minor": 1}.get(s, 0),
                    )
            best.quality_score = max(l.quality_score for l in same_type_cluster)
            best.selection_score = max(l.selection_score for l in same_type_cluster)
            merged.append(best)

    return merged
