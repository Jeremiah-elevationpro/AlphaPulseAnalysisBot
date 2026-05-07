"""Spencer Session Liquidity Intelligence Layer (Gold-specific).

Gold has a strong tendency to take the nearest pool of session liquidity
before reversing or continuing. This module is a **self-contained advisory
layer** that:

* tracks Asia / London / New York / overlap session ranges
* tracks previous-day and current-day extremes
* produces scored ``LiquidityLevel`` objects
* detects sweep events (wick beyond + close back inside)
* classifies the resulting setup type (asian_high_sweep_sell, etc.)
* exposes a market-plan summary, a Telegram-ready section, and a manual-setup
  advisory helper.

The module **does not** modify Market Analyst, Level Intelligence, Scenario
Compliance, PyTorch AI, or the TP/SL engines — it produces structured output
that the existing layers can read. Until ``SESSION_LIQUIDITY_BLOCKING_MODE``
is flipped on, every signal is advisory only.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable

import pandas as pd

from config.settings import (
    ASIA_SESSION_END,
    ASIA_SESSION_START,
    BROKER_TIMEZONE_OFFSET,
    LONDON_NY_OVERLAP_END,
    LONDON_NY_OVERLAP_START,
    LONDON_SESSION_END,
    LONDON_SESSION_START,
    NEW_YORK_SESSION_END,
    NEW_YORK_SESSION_START,
    SESSION_LIQUIDITY_ADVISORY_MODE,
    SESSION_LIQUIDITY_BLOCKING_MODE,
    SESSION_LIQUIDITY_DISPLACEMENT_MIN_PIPS,
    SESSION_LIQUIDITY_ENABLED,
    SESSION_LIQUIDITY_EQUAL_HIGH_TOLERANCE_PIPS,
    SESSION_LIQUIDITY_MIN_LEVEL_SCORE,
    SESSION_LIQUIDITY_RECLAIM_BUFFER_PIPS,
    SESSION_LIQUIDITY_SWEEP_MIN_WICK_PIPS,
    SESSION_TIMEZONE,
)
from utils.logger import get_logger

logger = get_logger(__name__)


SESSION_NAMES: tuple[str, ...] = ("asia", "london", "new_york", "overlap")
LIQUIDITY_LEVEL_TYPES: tuple[str, ...] = (
    "asian_high",
    "asian_low",
    "london_high",
    "london_low",
    "new_york_high",
    "new_york_low",
    "previous_day_high",
    "previous_day_low",
    "current_day_high",
    "current_day_low",
    "equal_highs",
    "equal_lows",
    "intraday_swing_high",
    "intraday_swing_low",
    "range_high",
    "range_low",
)
LIQUIDITY_SETUP_TYPES: tuple[str, ...] = (
    "asian_high_sweep_sell",
    "asian_low_sweep_buy",
    "london_high_sweep_sell",
    "london_low_sweep_buy",
    "new_york_high_sweep_sell",
    "new_york_low_sweep_buy",
    "previous_day_high_sweep_sell",
    "previous_day_low_sweep_buy",
    "equal_highs_sweep_sell",
    "equal_lows_sweep_buy",
    "liquidity_continuation_after_reclaim",
)


_SETUP_TARGET_ROLES: dict[str, list[str]] = {
    "asian_high_sweep_sell": [
        "liquidity:session_midpoint",
        "liquidity:asian_low",
        "liquidity:next_pool",
    ],
    "asian_low_sweep_buy": [
        "liquidity:session_midpoint",
        "liquidity:asian_high",
        "liquidity:next_pool",
    ],
    "london_high_sweep_sell": [
        "liquidity:session_midpoint",
        "liquidity:london_low",
        "liquidity:next_pool",
    ],
    "london_low_sweep_buy": [
        "liquidity:session_midpoint",
        "liquidity:london_high",
        "liquidity:next_pool",
    ],
    "new_york_high_sweep_sell": [
        "liquidity:session_midpoint",
        "liquidity:new_york_low",
        "liquidity:next_pool",
    ],
    "new_york_low_sweep_buy": [
        "liquidity:session_midpoint",
        "liquidity:new_york_high",
        "liquidity:next_pool",
    ],
    "previous_day_high_sweep_sell": [
        "liquidity:session_midpoint",
        "liquidity:previous_day_low",
        "liquidity:next_pool",
    ],
    "previous_day_low_sweep_buy": [
        "liquidity:session_midpoint",
        "liquidity:previous_day_high",
        "liquidity:next_pool",
    ],
    "equal_highs_sweep_sell": ["liquidity:session_midpoint", "liquidity:next_pool"],
    "equal_lows_sweep_buy": ["liquidity:session_midpoint", "liquidity:next_pool"],
    "liquidity_continuation_after_reclaim": ["liquidity:next_pool"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SessionRange:
    session_name: str
    start: datetime | None
    end: datetime | None
    high: float = 0.0
    low: float = 0.0
    midpoint: float = 0.0
    open: float = 0.0
    close: float = 0.0
    range_size_pips: float = 0.0
    direction: str = "neutral"  # "bullish" | "bearish" | "ranging" | "neutral"
    candle_count: int = 0
    finalised: bool = False  # session window has fully elapsed

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_name": self.session_name,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "high": round(float(self.high), 2),
            "low": round(float(self.low), 2),
            "midpoint": round(float(self.midpoint), 2),
            "open": round(float(self.open), 2),
            "close": round(float(self.close), 2),
            "range_size_pips": round(float(self.range_size_pips), 2),
            "direction": self.direction,
            "candle_count": int(self.candle_count),
            "finalised": bool(self.finalised),
        }


@dataclass
class LiquidityLevel:
    level: float
    level_type: str
    session_name: str = ""
    created_at: str = ""
    touched: bool = False
    swept: bool = False
    reclaimed: bool = False
    sweep_direction: str = ""  # "above" | "below" | ""
    strength_score: float = 0.0  # 0-100 obviousness/structural alignment
    liquidity_score: float = 0.0  # 0-100 final composite
    distance_from_current_price: float = 0.0
    direction_relevance: str = "both"  # "BUY" | "SELL" | "both"
    quality_label: str = "IGNORE"
    evidence_summary: str = ""
    aligned_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiquiditySweepEvent:
    swept_level: float
    swept_level_type: str
    sweep_direction: str  # "buy_side" (price went above) | "sell_side" (price went below)
    sweep_candle_time: str
    wick_distance_pips: float
    close_back_inside: bool
    displacement_after_sweep: bool
    reclaim_confirmed: bool
    trap_quality_score: float = 0.0  # 0-100
    expected_direction: str = ""  # "BUY" | "SELL"
    invalidation_level: float = 0.0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LiquiditySetup:
    setup_type: str
    direction: str  # "BUY" | "SELL"
    swept_level: float
    swept_level_type: str
    sweep_event: LiquiditySweepEvent | None
    target_roles: list[str] = field(default_factory=list)
    target_levels: list[float] = field(default_factory=list)
    invalidation_level: float = 0.0
    confirmation_required: list[str] = field(default_factory=list)
    advisory_or_blocking: str = "advisory"
    quality_label: str = "WATCH"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["sweep_event"] = self.sweep_event.to_dict() if self.sweep_event else None
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_hhmm(value: str, *, default: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    try:
        match = re.match(r"^(\d{1,2}):(\d{2})$", str(value or "").strip())
        if not match:
            return default
        h = int(match.group(1)) % 24
        m = int(match.group(2)) % 60
        return h, m
    except Exception:
        return default


def _round2(value: float) -> float:
    try:
        return round(float(value or 0.0), 2)
    except Exception:
        return 0.0


def _direction_for_session(open_price: float, close_price: float, *, threshold_pips: float = 5.0) -> str:
    if not open_price or not close_price:
        return "neutral"
    delta = close_price - open_price
    if abs(delta) < threshold_pips:
        return "ranging"
    return "bullish" if delta > 0 else "bearish"


def _within_window(candle_time: datetime, start_h: int, start_m: int, end_h: int, end_m: int) -> bool:
    t = candle_time.timetz() if candle_time.tzinfo else candle_time.time()
    if isinstance(t, time):
        cur = t
    else:
        cur = candle_time.time()
    start = time(start_h, start_m)
    end = time(end_h, end_m)
    if start <= end:
        return start <= cur < end
    # Wrap-around midnight
    return cur >= start or cur < end


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────


class SessionLiquidityEngine:
    """Self-contained Session Liquidity intelligence engine for Gold.

    Inputs
    ------
    The engine consumes the same dict-of-DataFrames the rest of Spencer uses:
    ``{"H4": DataFrame, "H1": DataFrame, "M15": DataFrame}`` where each frame
    has columns ``time, open, high, low, close``. Times must be timezone-aware
    UTC (the engine treats the broker feed as UTC; ``SESSION_TIMEZONE`` /
    ``BROKER_TIMEZONE_OFFSET`` are surfaced for display purposes only).

    Outputs
    -------
    * ``compute_session_ranges`` - per-session ``SessionRange`` map
    * ``derive_levels`` - scored ``LiquidityLevel`` list
    * ``detect_sweeps`` - ``LiquiditySweepEvent`` list ordered by recency
    * ``classify_setups`` - ``LiquiditySetup`` list ordered by quality
    * ``build_market_plan_summary`` - dict ready to attach to plan + telegram
    * ``evaluate_manual_setup`` - advisory text for user-supplied setups
    """

    def __init__(self) -> None:
        self.enabled = bool(SESSION_LIQUIDITY_ENABLED)
        self.advisory_mode = bool(SESSION_LIQUIDITY_ADVISORY_MODE) and not bool(SESSION_LIQUIDITY_BLOCKING_MODE)
        self.blocking_mode = bool(SESSION_LIQUIDITY_BLOCKING_MODE)
        self.asia_window = (
            _parse_hhmm(ASIA_SESSION_START, default=(0, 0)),
            _parse_hhmm(ASIA_SESSION_END, default=(7, 0)),
        )
        self.london_window = (
            _parse_hhmm(LONDON_SESSION_START, default=(7, 0)),
            _parse_hhmm(LONDON_SESSION_END, default=(13, 0)),
        )
        self.new_york_window = (
            _parse_hhmm(NEW_YORK_SESSION_START, default=(13, 0)),
            _parse_hhmm(NEW_YORK_SESSION_END, default=(21, 0)),
        )
        self.overlap_window = (
            _parse_hhmm(LONDON_NY_OVERLAP_START, default=(13, 0)),
            _parse_hhmm(LONDON_NY_OVERLAP_END, default=(16, 0)),
        )
        self._logged_config = False

    # ── Public API ──────────────────────────────────────────────────────────

    def log_config(self) -> None:
        if self._logged_config:
            return
        self._logged_config = True
        logger.info(
            "SESSION LIQUIDITY CONFIG: enabled=%s advisory=%s blocking=%s tz=%s offset=%s "
            "Asia=%02d:%02d->%02d:%02d London=%02d:%02d->%02d:%02d NY=%02d:%02d->%02d:%02d "
            "Overlap=%02d:%02d->%02d:%02d",
            self.enabled,
            self.advisory_mode,
            self.blocking_mode,
            SESSION_TIMEZONE,
            BROKER_TIMEZONE_OFFSET,
            *self.asia_window[0], *self.asia_window[1],
            *self.london_window[0], *self.london_window[1],
            *self.new_york_window[0], *self.new_york_window[1],
            *self.overlap_window[0], *self.overlap_window[1],
        )

    def compute_session_ranges(
        self,
        m15: pd.DataFrame | None,
        *,
        as_of: datetime | None = None,
    ) -> dict[str, SessionRange]:
        """Return per-session ranges for the session day containing ``as_of``."""
        out: dict[str, SessionRange] = {
            name: SessionRange(session_name=name, start=None, end=None) for name in SESSION_NAMES
        }
        if m15 is None or m15.empty:
            return out
        df = m15.copy()
        if "time" not in df.columns:
            return out
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.dropna(subset=["time"]).reset_index(drop=True)
        if df.empty:
            return out
        last_time = df["time"].iloc[-1].to_pydatetime()
        anchor = as_of.astimezone(timezone.utc) if as_of else last_time.astimezone(timezone.utc)
        # Session day boundary uses the anchor's UTC date.
        day_start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=timezone.utc)

        for session_name, window in (
            ("asia", self.asia_window),
            ("london", self.london_window),
            ("new_york", self.new_york_window),
            ("overlap", self.overlap_window),
        ):
            (sh, sm), (eh, em) = window
            start = day_start.replace(hour=sh, minute=sm)
            end = day_start.replace(hour=eh, minute=em)
            if end <= start:
                end = end + timedelta(days=1)
            mask = (df["time"] >= start) & (df["time"] < end)
            window_df = df.loc[mask]
            sr = out[session_name]
            sr.start = start
            sr.end = end
            if window_df.empty:
                continue
            sr.high = float(window_df["high"].max())
            sr.low = float(window_df["low"].min())
            sr.midpoint = round((sr.high + sr.low) / 2.0, 2) if sr.high and sr.low else 0.0
            sr.open = float(window_df["open"].iloc[0])
            sr.close = float(window_df["close"].iloc[-1])
            sr.range_size_pips = round(sr.high - sr.low, 2)
            sr.direction = _direction_for_session(sr.open, sr.close)
            sr.candle_count = int(len(window_df))
            sr.finalised = anchor >= end
        return out

    def derive_previous_day_extremes(self, m15: pd.DataFrame | None, *, as_of: datetime | None = None) -> tuple[float, float]:
        if m15 is None or m15.empty or "time" not in m15.columns:
            return 0.0, 0.0
        df = m15.copy()
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.dropna(subset=["time"]).reset_index(drop=True)
        if df.empty:
            return 0.0, 0.0
        anchor = (as_of or df["time"].iloc[-1].to_pydatetime()).astimezone(timezone.utc)
        prev_day_start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=timezone.utc) - timedelta(days=1)
        prev_day_end = prev_day_start + timedelta(days=1)
        mask = (df["time"] >= prev_day_start) & (df["time"] < prev_day_end)
        prev_df = df.loc[mask]
        if prev_df.empty:
            return 0.0, 0.0
        return float(prev_df["high"].max()), float(prev_df["low"].min())

    def derive_current_day_extremes(self, m15: pd.DataFrame | None, *, as_of: datetime | None = None) -> tuple[float, float]:
        if m15 is None or m15.empty or "time" not in m15.columns:
            return 0.0, 0.0
        df = m15.copy()
        df["time"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
        df = df.dropna(subset=["time"]).reset_index(drop=True)
        if df.empty:
            return 0.0, 0.0
        anchor = (as_of or df["time"].iloc[-1].to_pydatetime()).astimezone(timezone.utc)
        day_start = datetime(anchor.year, anchor.month, anchor.day, tzinfo=timezone.utc)
        mask = df["time"] >= day_start
        day_df = df.loc[mask]
        if day_df.empty:
            return 0.0, 0.0
        return float(day_df["high"].max()), float(day_df["low"].min())

    def derive_levels(
        self,
        sessions: dict[str, SessionRange],
        *,
        previous_day_high: float = 0.0,
        previous_day_low: float = 0.0,
        current_day_high: float = 0.0,
        current_day_low: float = 0.0,
        current_price: float = 0.0,
        equal_high_groups: Iterable[float] | None = None,
        equal_low_groups: Iterable[float] | None = None,
        h1_supports: Iterable[float] | None = None,
        h1_resistances: Iterable[float] | None = None,
        psych_levels: Iterable[float] | None = None,
    ) -> list[LiquidityLevel]:
        levels: list[LiquidityLevel] = []
        h1_supports = list(h1_supports or [])
        h1_resistances = list(h1_resistances or [])
        psych_levels = list(psych_levels or [])

        def _score_level(level: float, level_type: str, *, session_importance: float, base: float = 35.0) -> tuple[float, list[str]]:
            score = base + session_importance
            aligned: list[str] = []
            if any(abs(level - r) <= 5.0 for r in h1_resistances):
                score += 12
                aligned.append("H1 resistance")
            if any(abs(level - s) <= 5.0 for s in h1_supports):
                score += 12
                aligned.append("H1 support")
            if any(abs(level - p) <= 5.0 for p in psych_levels):
                score += 8
                aligned.append("psychological level")
            # equal-highs / equal-lows alignment
            if equal_high_groups and any(abs(level - eq) <= SESSION_LIQUIDITY_EQUAL_HIGH_TOLERANCE_PIPS for eq in equal_high_groups):
                score += 10
                aligned.append("equal highs cluster")
            if equal_low_groups and any(abs(level - eq) <= SESSION_LIQUIDITY_EQUAL_HIGH_TOLERANCE_PIPS for eq in equal_low_groups):
                score += 10
                aligned.append("equal lows cluster")
            if current_price:
                distance = abs(level - current_price)
                if distance < 5:
                    score -= 8  # too close to be useful as fresh liquidity
                elif distance > 80:
                    score -= 5  # too far for the session
            score = max(0.0, min(100.0, score))
            return score, aligned

        def _label(score: float, distance: float = 999.0) -> str:
            if score >= 90:
                return "A+ LIQUIDITY"
            if score >= 80:
                return "A LIQUIDITY"
            if score >= 70:
                return "VALID LIQUIDITY"
            if score >= 60 and distance <= 30:
                return "TACTICAL LIQUIDITY"
            if score >= 60:
                return "WEAK LIQUIDITY"
            return "IGNORE"

        # Session-specific levels.
        importance_by_session = {"asia": 25.0, "london": 20.0, "new_york": 20.0, "overlap": 18.0}
        for name, sr in sessions.items():
            if not sr or not sr.high or not sr.low:
                continue
            for kind, value, relevance in (("high", sr.high, "SELL"), ("low", sr.low, "BUY")):
                level_type = f"{name}_{kind}" if name != "asia" else f"asian_{kind}"
                if name == "new_york" and kind == "high":
                    level_type = "new_york_high"
                if name == "new_york" and kind == "low":
                    level_type = "new_york_low"
                if name == "overlap":
                    level_type = "range_high" if kind == "high" else "range_low"
                score, aligned = _score_level(
                    value,
                    level_type,
                    session_importance=importance_by_session.get(name, 15.0),
                )
                evidence = aligned + [f"{name} session {kind}"]
                levels.append(
                    LiquidityLevel(
                        level=_round2(value),
                        level_type=level_type,
                        session_name=name,
                        created_at=sr.start.isoformat() if sr.start else "",
                        strength_score=round(score, 1),
                        liquidity_score=round(score, 1),
                        distance_from_current_price=round(abs(value - current_price), 2) if current_price else 0.0,
                        direction_relevance=relevance,
                        quality_label=_label(score, abs(value - current_price) if current_price else 999.0),
                        evidence_summary=" + ".join(evidence) if evidence else "",
                        aligned_with=aligned,
                    )
                )

        # Previous-day / current-day extremes.
        for level_type, value, relevance in (
            ("previous_day_high", previous_day_high, "SELL"),
            ("previous_day_low", previous_day_low, "BUY"),
            ("current_day_high", current_day_high, "SELL"),
            ("current_day_low", current_day_low, "BUY"),
        ):
            if not value:
                continue
            score, aligned = _score_level(value, level_type, session_importance=22.0)
            label = _label(score, abs(value - current_price) if current_price else 999.0)
            evidence = aligned + [level_type.replace("_", " ")]
            levels.append(
                LiquidityLevel(
                    level=_round2(value),
                    level_type=level_type,
                    session_name="day",
                    created_at="",
                    strength_score=round(score, 1),
                    liquidity_score=round(score, 1),
                    distance_from_current_price=round(abs(value - current_price), 2) if current_price else 0.0,
                    direction_relevance=relevance,
                    quality_label=label,
                    evidence_summary=" + ".join(evidence),
                    aligned_with=aligned,
                )
            )

        # Equal highs / equal lows clusters as standalone liquidity if any.
        for cluster_value, level_type, relevance in (
            *(((float(v), "equal_highs", "SELL") for v in (equal_high_groups or [])) ),
            *(((float(v), "equal_lows", "BUY") for v in (equal_low_groups or []))),
        ):
            score, aligned = _score_level(cluster_value, level_type, session_importance=15.0, base=45.0)
            levels.append(
                LiquidityLevel(
                    level=_round2(cluster_value),
                    level_type=level_type,
                    session_name="cluster",
                    created_at="",
                    strength_score=round(score, 1),
                    liquidity_score=round(score, 1),
                    distance_from_current_price=round(abs(cluster_value - current_price), 2) if current_price else 0.0,
                    direction_relevance=relevance,
                    quality_label=_label(score),
                    evidence_summary=" + ".join(aligned + [level_type.replace("_", " ")]),
                    aligned_with=aligned,
                )
            )

        # Sort by score, level grouping deduped.
        levels.sort(key=lambda lvl: (-lvl.liquidity_score, lvl.distance_from_current_price))
        return levels

    def detect_sweeps(
        self,
        m15: pd.DataFrame | None,
        levels: list[LiquidityLevel],
        *,
        max_lookback: int = 24,
    ) -> list[LiquiditySweepEvent]:
        if m15 is None or m15.empty or not levels:
            return []
        df = m15.tail(max_lookback).copy()
        events: list[LiquiditySweepEvent] = []
        for level in levels:
            for _, candle in df.iterrows():
                high = float(candle.get("high", 0.0))
                low = float(candle.get("low", 0.0))
                close = float(candle.get("close", 0.0))
                open_ = float(candle.get("open", close))
                ts = str(candle.get("time", ""))
                wick_above = high - level.level
                wick_below = level.level - low

                if level.direction_relevance == "SELL" and wick_above >= SESSION_LIQUIDITY_SWEEP_MIN_WICK_PIPS:
                    close_back_inside = close < (level.level - SESSION_LIQUIDITY_RECLAIM_BUFFER_PIPS)
                    displacement = (open_ - close) >= SESSION_LIQUIDITY_DISPLACEMENT_MIN_PIPS
                    if not close_back_inside:
                        continue
                    trap = self._trap_quality(level, wick_above, displacement, close_back_inside)
                    notes = []
                    if displacement:
                        notes.append("bearish displacement after sweep")
                    notes.append("close back below swept level")
                    events.append(
                        LiquiditySweepEvent(
                            swept_level=level.level,
                            swept_level_type=level.level_type,
                            sweep_direction="buy_side",
                            sweep_candle_time=ts,
                            wick_distance_pips=round(wick_above, 2),
                            close_back_inside=close_back_inside,
                            displacement_after_sweep=displacement,
                            reclaim_confirmed=close_back_inside and displacement,
                            trap_quality_score=trap,
                            expected_direction="SELL",
                            invalidation_level=high,
                            notes=notes,
                        )
                    )
                    level.swept = True
                    level.sweep_direction = "above"
                    if close_back_inside:
                        level.reclaimed = True
                    break  # one event per level
                if level.direction_relevance == "BUY" and wick_below >= SESSION_LIQUIDITY_SWEEP_MIN_WICK_PIPS:
                    close_back_inside = close > (level.level + SESSION_LIQUIDITY_RECLAIM_BUFFER_PIPS)
                    displacement = (close - open_) >= SESSION_LIQUIDITY_DISPLACEMENT_MIN_PIPS
                    if not close_back_inside:
                        continue
                    trap = self._trap_quality(level, wick_below, displacement, close_back_inside)
                    notes = []
                    if displacement:
                        notes.append("bullish displacement after sweep")
                    notes.append("close back above swept level")
                    events.append(
                        LiquiditySweepEvent(
                            swept_level=level.level,
                            swept_level_type=level.level_type,
                            sweep_direction="sell_side",
                            sweep_candle_time=ts,
                            wick_distance_pips=round(wick_below, 2),
                            close_back_inside=close_back_inside,
                            displacement_after_sweep=displacement,
                            reclaim_confirmed=close_back_inside and displacement,
                            trap_quality_score=trap,
                            expected_direction="BUY",
                            invalidation_level=low,
                            notes=notes,
                        )
                    )
                    level.swept = True
                    level.sweep_direction = "below"
                    if close_back_inside:
                        level.reclaimed = True
                    break
        events.sort(key=lambda evt: evt.trap_quality_score, reverse=True)
        return events

    @staticmethod
    def _trap_quality(level: LiquidityLevel, wick: float, displacement: bool, close_back: bool) -> float:
        score = level.liquidity_score * 0.5
        score += min(wick * 3.0, 25.0)
        if close_back:
            score += 12
        if displacement:
            score += 12
        if "equal" in level.level_type:
            score += 6
        return round(min(100.0, max(0.0, score)), 1)

    def classify_setups(
        self,
        sweeps: list[LiquiditySweepEvent],
        sessions: dict[str, SessionRange],
        levels: list[LiquidityLevel],
        *,
        current_price: float = 0.0,
    ) -> list[LiquiditySetup]:
        setups: list[LiquiditySetup] = []
        # Map level_type -> setup_type
        type_to_setup = {
            "asian_high": "asian_high_sweep_sell",
            "asian_low": "asian_low_sweep_buy",
            "london_high": "london_high_sweep_sell",
            "london_low": "london_low_sweep_buy",
            "new_york_high": "new_york_high_sweep_sell",
            "new_york_low": "new_york_low_sweep_buy",
            "previous_day_high": "previous_day_high_sweep_sell",
            "previous_day_low": "previous_day_low_sweep_buy",
            "equal_highs": "equal_highs_sweep_sell",
            "equal_lows": "equal_lows_sweep_buy",
        }
        for sweep in sweeps:
            setup_type = type_to_setup.get(sweep.swept_level_type)
            if not setup_type:
                continue
            direction = "SELL" if "sell" in setup_type else "BUY"
            target_levels = self._target_levels_for_setup(setup_type, sessions, levels, current_price=current_price)
            target_roles = list(_SETUP_TARGET_ROLES.get(setup_type, []))
            invalidation = sweep.invalidation_level
            confirmation_required = ["sweep_close_back_inside"]
            if sweep.displacement_after_sweep:
                confirmation_required.append("displacement_after_sweep")
            confirmation_required.append("break_retest_close_confirmation OR engulfing_at_swept_level OR failed_breakout_retest")
            quality_label = "A+ TRAP" if sweep.trap_quality_score >= 90 else "A TRAP" if sweep.trap_quality_score >= 80 else "VALID TRAP" if sweep.trap_quality_score >= 70 else "WEAK"
            setups.append(
                LiquiditySetup(
                    setup_type=setup_type,
                    direction=direction,
                    swept_level=sweep.swept_level,
                    swept_level_type=sweep.swept_level_type,
                    sweep_event=sweep,
                    target_roles=target_roles,
                    target_levels=target_levels,
                    invalidation_level=round(invalidation, 2),
                    confirmation_required=confirmation_required,
                    advisory_or_blocking="blocking" if self.blocking_mode else "advisory",
                    quality_label=quality_label,
                    notes=", ".join(sweep.notes),
                )
            )
        # Continuation-after-reclaim: detect when current_price is comfortably
        # above a session resistance (or below a session support) and the level
        # was either explicitly swept-and-reclaimed or simply held above on
        # close. Provides the buy-continuation watch the spec asks for.
        continuation_buffer = max(SESSION_LIQUIDITY_RECLAIM_BUFFER_PIPS * 4, 5.0)
        for level in levels:
            if level.level_type not in {"asian_high", "london_high", "previous_day_high"}:
                continue
            if not current_price or current_price <= level.level + continuation_buffer:
                continue
            # Already has a sell sweep event? Prefer that; this is the alternative continuation read.
            already_sell = any(s.setup_type.endswith("sweep_sell") and abs(s.swept_level - level.level) < 0.5 for s in setups)
            if already_sell:
                continue
            # Implicitly mark the level as reclaimed if price holds above it.
            if not level.swept:
                level.swept = True
                level.sweep_direction = "above"
                level.reclaimed = True
            setups.append(
                LiquiditySetup(
                    setup_type="liquidity_continuation_after_reclaim",
                    direction="BUY",
                    swept_level=level.level,
                    swept_level_type=level.level_type,
                    sweep_event=None,
                    target_roles=["liquidity:next_pool"],
                    target_levels=self._target_levels_for_setup(
                        "liquidity_continuation_after_reclaim", sessions, levels, current_price=current_price
                    ),
                    invalidation_level=round(level.level - SESSION_LIQUIDITY_RECLAIM_BUFFER_PIPS * 2, 2),
                    confirmation_required=["retest_as_support", "bullish_displacement_or_engulfing"],
                    advisory_or_blocking="blocking" if self.blocking_mode else "advisory",
                    quality_label="WATCH",
                    notes=f"Price holding above reclaimed {level.level_type} {level.level:.2f}; await retest as support",
                )
            )
        return setups

    def _target_levels_for_setup(
        self,
        setup_type: str,
        sessions: dict[str, SessionRange],
        levels: list[LiquidityLevel],
        *,
        current_price: float,
    ) -> list[float]:
        targets: list[float] = []
        if setup_type == "asian_high_sweep_sell":
            asia = sessions.get("asia") or SessionRange(session_name="asia", start=None, end=None)
            if asia.midpoint:
                targets.append(asia.midpoint)
            if asia.low:
                targets.append(asia.low)
        if setup_type == "asian_low_sweep_buy":
            asia = sessions.get("asia") or SessionRange(session_name="asia", start=None, end=None)
            if asia.midpoint:
                targets.append(asia.midpoint)
            if asia.high:
                targets.append(asia.high)
        if setup_type == "london_high_sweep_sell":
            lon = sessions.get("london") or SessionRange(session_name="london", start=None, end=None)
            if lon.midpoint:
                targets.append(lon.midpoint)
            if lon.low:
                targets.append(lon.low)
        if setup_type == "london_low_sweep_buy":
            lon = sessions.get("london") or SessionRange(session_name="london", start=None, end=None)
            if lon.midpoint:
                targets.append(lon.midpoint)
            if lon.high:
                targets.append(lon.high)
        if setup_type == "new_york_high_sweep_sell":
            ny = sessions.get("new_york") or SessionRange(session_name="new_york", start=None, end=None)
            if ny.midpoint:
                targets.append(ny.midpoint)
            if ny.low:
                targets.append(ny.low)
        if setup_type == "new_york_low_sweep_buy":
            ny = sessions.get("new_york") or SessionRange(session_name="new_york", start=None, end=None)
            if ny.midpoint:
                targets.append(ny.midpoint)
            if ny.high:
                targets.append(ny.high)
        if setup_type == "previous_day_high_sweep_sell":
            for lvl in levels:
                if lvl.level_type == "previous_day_low":
                    targets.append(lvl.level)
                    break
        if setup_type == "previous_day_low_sweep_buy":
            for lvl in levels:
                if lvl.level_type == "previous_day_high":
                    targets.append(lvl.level)
                    break
        # Append next downstream/upstream pool from levels by direction.
        next_pool = self._next_pool(levels, current_price=current_price, want_below="sell" in setup_type)
        if next_pool and next_pool not in targets:
            targets.append(next_pool)
        return [round(t, 2) for t in targets if t]

    @staticmethod
    def _next_pool(levels: list[LiquidityLevel], *, current_price: float, want_below: bool) -> float:
        candidates = [
            lvl.level
            for lvl in levels
            if lvl.liquidity_score >= SESSION_LIQUIDITY_MIN_LEVEL_SCORE
            and not lvl.swept
            and (lvl.level < current_price if want_below else lvl.level > current_price)
        ]
        if not candidates:
            return 0.0
        return min(candidates, key=lambda v: abs(v - current_price))

    # ── Aggregated outputs ─────────────────────────────────────────────────

    def build_market_plan_summary(
        self,
        m15: pd.DataFrame | None,
        *,
        current_price: float = 0.0,
        h1_supports: Iterable[float] | None = None,
        h1_resistances: Iterable[float] | None = None,
        psych_levels: Iterable[float] | None = None,
        equal_highs: Iterable[float] | None = None,
        equal_lows: Iterable[float] | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        self.log_config()
        if not self.enabled:
            return {"enabled": False, "advisory_only": True, "blocking_mode": False}
        sessions = self.compute_session_ranges(m15, as_of=as_of)
        prev_high, prev_low = self.derive_previous_day_extremes(m15, as_of=as_of)
        cur_high, cur_low = self.derive_current_day_extremes(m15, as_of=as_of)
        levels = self.derive_levels(
            sessions,
            previous_day_high=prev_high,
            previous_day_low=prev_low,
            current_day_high=cur_high,
            current_day_low=cur_low,
            current_price=current_price,
            equal_high_groups=equal_highs or [],
            equal_low_groups=equal_lows or [],
            h1_supports=h1_supports,
            h1_resistances=h1_resistances,
            psych_levels=psych_levels,
        )
        sweeps = self.detect_sweeps(m15, levels)
        setups = self.classify_setups(sweeps, sessions, levels, current_price=current_price)
        active_setups, context_setups = self._split_setups_by_quality(setups, levels)

        nearest_buy_side = self._nearest_level(levels, current_price=current_price, want_above=True)
        nearest_sell_side = self._nearest_level(levels, current_price=current_price, want_above=False)
        liquidity_bias = self._liquidity_bias(nearest_buy_side, nearest_sell_side, current_price)

        active_levels = [lvl for lvl in levels if self._is_level_active(lvl)]
        inactive_levels = [lvl for lvl in levels if not self._is_level_active(lvl)]

        return {
            "enabled": True,
            "advisory_only": self.advisory_mode,
            "blocking_mode": self.blocking_mode,
            "sessions": {name: sr.to_dict() for name, sr in sessions.items()},
            "previous_day_high": _round2(prev_high),
            "previous_day_low": _round2(prev_low),
            "current_day_high": _round2(cur_high),
            "current_day_low": _round2(cur_low),
            # Back-compat: full level/setup lists for existing consumers.
            "levels": [lvl.to_dict() for lvl in levels],
            "sweeps": [s.to_dict() for s in sweeps],
            "setups": [s.to_dict() for s in setups],
            # New: active vs context split. The dashboard / Telegram should
            # render these instead of "levels" / "setups".
            "active_levels": [lvl.to_dict() for lvl in active_levels],
            "inactive_levels": [lvl.to_dict() for lvl in inactive_levels],
            "active_setups": [s.to_dict() for s in active_setups],
            "context_setups": [s.to_dict() for s in context_setups],
            "nearest_buy_side_liquidity": _round2(nearest_buy_side.level) if nearest_buy_side else 0.0,
            "nearest_sell_side_liquidity": _round2(nearest_sell_side.level) if nearest_sell_side else 0.0,
            "liquidity_bias": liquidity_bias,
            "expected_play": self._expected_play(active_setups, nearest_buy_side, nearest_sell_side, current_price, liquidity_bias),
        }

    @staticmethod
    def _nearest_level(levels: list[LiquidityLevel], *, current_price: float, want_above: bool) -> LiquidityLevel | None:
        candidates = [
            lvl for lvl in levels
            if lvl.liquidity_score >= SESSION_LIQUIDITY_MIN_LEVEL_SCORE
            and not lvl.swept
            and ((lvl.level > current_price) if want_above else (lvl.level < current_price))
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda lvl: abs(lvl.level - current_price))

    @staticmethod
    def _liquidity_bias(
        buy_side: LiquidityLevel | None,
        sell_side: LiquidityLevel | None,
        current_price: float,
    ) -> str:
        if not current_price:
            return "balanced"
        if buy_side and sell_side:
            d_up = abs(buy_side.level - current_price)
            d_down = abs(sell_side.level - current_price)
            if d_up < d_down * 0.7:
                return "buy-side sweep likely"
            if d_down < d_up * 0.7:
                return "sell-side sweep likely"
            return "balanced"
        if buy_side and not sell_side:
            return "buy-side sweep likely"
        if sell_side and not buy_side:
            return "sell-side sweep likely"
        return "balanced"

    _ACTIVE_LABELS: frozenset[str] = frozenset({"A+ LIQUIDITY", "A LIQUIDITY", "VALID LIQUIDITY", "TACTICAL LIQUIDITY"})
    _INACTIVE_LABELS: frozenset[str] = frozenset({"IGNORE", "WEAK LIQUIDITY"})

    @classmethod
    def _is_level_active(cls, level: LiquidityLevel | None) -> bool:
        if not level:
            return False
        if level.liquidity_score < SESSION_LIQUIDITY_MIN_LEVEL_SCORE:
            return False
        if str(level.quality_label or "").upper() in cls._INACTIVE_LABELS:
            return False
        return True

    @classmethod
    def _split_setups_by_quality(
        cls,
        setups: list[LiquiditySetup],
        levels: list[LiquidityLevel],
    ) -> tuple[list[LiquiditySetup], list[LiquiditySetup]]:
        level_by_type: dict[str, LiquidityLevel] = {lvl.level_type: lvl for lvl in levels}
        active: list[LiquiditySetup] = []
        context: list[LiquiditySetup] = []
        for setup in setups:
            level = level_by_type.get(setup.swept_level_type)
            if cls._is_level_active(level):
                active.append(setup)
            else:
                context.append(setup)
                logger.info(
                    "SESSION LIQUIDITY PLAY SKIPPED: level=%s reason=ignore_or_weak_liquidity score=%.1f label=%s",
                    f"{setup.swept_level_type} {setup.swept_level:.2f}",
                    level.liquidity_score if level else 0.0,
                    level.quality_label if level else "n/a",
                )
        return active, context

    @classmethod
    def _expected_play(
        cls,
        active_setups: list[LiquiditySetup],
        buy_side: LiquidityLevel | None,
        sell_side: LiquidityLevel | None,
        current_price: float,
        liquidity_bias: str = "balanced",
    ) -> str:
        # 1. Highest-quality active sweep setup wins.
        if active_setups:
            top = active_setups[0]
            return (
                f"{top.setup_type.replace('_', ' ').title()} "
                f"({top.direction}) — swept {top.swept_level_type.replace('_', ' ')} "
                f"{top.swept_level:.2f}; targets {', '.join(f'{t:.2f}' for t in top.target_levels) or 'next pool'}."
            )
        def _continuation_targets(level: float, direction: str) -> list[float]:
            if not level:
                return []
            step = 20.0
            targets: list[float] = []
            if direction.upper() == "BUY":
                base = ((int((level + 20.0) // step) + 1) * step)
                if base - step >= level + 20.0:
                    base -= step
                targets = [base, base + step]
                if any(target <= level for target in targets):
                    logger.info(
                        "SESSION LIQUIDITY TARGET DIRECTION FIXED: reason=target_was_opposite_direction direction=BUY level=%.2f targets=%s",
                        level,
                        targets,
                    )
                    targets = [target for target in targets if target > level]
            else:
                base = int((level - 20.0) // step) * step
                if base + step <= level - 20.0:
                    base += step
                targets = [base, base - step]
                if any(target >= level for target in targets):
                    logger.info(
                        "SESSION LIQUIDITY TARGET DIRECTION FIXED: reason=target_was_opposite_direction direction=SELL level=%.2f targets=%s",
                        level,
                        targets,
                    )
                    targets = [target for target in targets if target < level]
            return [round(float(target), 2) for target in targets[:2]]

        def _target_text(level: float, direction: str) -> str:
            targets = _continuation_targets(level, direction)
            return " / ".join(f"{target:.2f}" for target in targets) if targets else "next liquidity"

        def _buy_side_play(prefix: str) -> str:
            if not cls._is_level_active(buy_side):
                return ""
            continuation = _target_text(buy_side.level, "BUY")
            return (
                f"{prefix}: Watch {buy_side.level:.2f} buy-side liquidity for sweep/rejection SELL "
                f"or reclaim continuation toward {continuation}."
            )

        def _sell_side_play(prefix: str) -> str:
            if not cls._is_level_active(sell_side):
                return ""
            continuation = _target_text(sell_side.level, "SELL")
            return (
                f"{prefix}: Watch {sell_side.level:.2f} sell-side liquidity for sweep/reclaim BUY "
                f"or breakdown continuation toward {continuation}."
            )

        # 2. No active sweep yet — nominate the nearest active buy-side or
        #    sell-side pool (already filtered by quality in _nearest_level).
        if cls._is_level_active(buy_side) and cls._is_level_active(sell_side):
            if liquidity_bias == "sell-side sweep likely":
                return " ".join(filter(None, [_sell_side_play("Primary"), _buy_side_play("Secondary")]))
            if liquidity_bias == "buy-side sweep likely":
                return " ".join(filter(None, [_buy_side_play("Primary"), _sell_side_play("Secondary")]))
            return (
                f"Buy-side liquidity: {buy_side.level:.2f}; reclaim continuation targets {_target_text(buy_side.level, 'BUY')}. "
                f"Sell-side liquidity: {sell_side.level:.2f}; breakdown continuation targets {_target_text(sell_side.level, 'SELL')}."
            )
        if cls._is_level_active(buy_side):
            return (
                f"Watch buy-side sweep into {buy_side.level:.2f} "
                f"({buy_side.level_type.replace('_', ' ')}), then rejection for sell; reclaim continuation targets {_target_text(buy_side.level, 'BUY')}."
            )
        if cls._is_level_active(sell_side):
            return (
                f"Watch sell-side sweep into {sell_side.level:.2f} "
                f"({sell_side.level_type.replace('_', ' ')}), then rejection for buy; breakdown continuation targets {_target_text(sell_side.level, 'SELL')}."
            )
        # 3. Nothing actionable.
        return "No high-quality liquidity play yet. Waiting for sweep/reclaim confirmation."

    # ── Manual-setup advisory ──────────────────────────────────────────────

    def evaluate_manual_setup(
        self,
        *,
        level: float,
        direction: str,
        current_price: float,
        plan_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plan = plan_summary or {}
        levels = [LiquidityLevel(**{k: v for k, v in lvl.items() if k in LiquidityLevel.__dataclass_fields__})
                  for lvl in plan.get("levels", []) if isinstance(lvl, dict)]
        # Manual-setup advisory considers ALL session-aligned levels (not just
        # A+/A) because the user is asking us to assess *their* chosen entry.
        nearest = min(
            (l for l in levels),
            key=lambda l: abs(l.level - level),
            default=None,
        )
        advisory = {
            "level": level,
            "direction": direction.upper(),
            "current_price": current_price,
            "near_session_liquidity": False,
            "matched_level_type": None,
            "matched_level": None,
            "advisory_text": "",
        }
        if not nearest:
            advisory["advisory_text"] = "No nearby session liquidity. Manual setup proceeds without liquidity caveat."
            return advisory
        if abs(nearest.level - level) > 8.0:
            advisory["advisory_text"] = (
                f"Manual {direction.upper()} {level:.2f} is not aligned with session liquidity (nearest {nearest.level_type} "
                f"{nearest.level:.2f}). Treat as standalone."
            )
            return advisory
        advisory["near_session_liquidity"] = True
        advisory["matched_level"] = nearest.level
        advisory["matched_level_type"] = nearest.level_type
        if direction.upper() == "SELL" and nearest.direction_relevance == "SELL":
            advisory["advisory_text"] = (
                f"{level:.2f} is near {nearest.level_type.replace('_', ' ')} "
                f"({'swept' if nearest.swept else 'unswept'} buy-side liquidity). "
                f"Do not sell blindly. Wait for sweep above {nearest.level:.2f} and close back below, "
                f"or failed retest, before SELL."
            )
        elif direction.upper() == "BUY" and nearest.direction_relevance == "BUY":
            advisory["advisory_text"] = (
                f"{level:.2f} is near {nearest.level_type.replace('_', ' ')} "
                f"({'swept' if nearest.swept else 'unswept'} sell-side liquidity). "
                f"Do not buy blindly. Wait for sweep below {nearest.level:.2f} and close back above, "
                f"or failed retest, before BUY."
            )
        else:
            advisory["advisory_text"] = (
                f"{level:.2f} is near {nearest.level_type.replace('_', ' ')} but the manual direction "
                f"opposes session-liquidity bias. Verify sweep evidence before committing."
            )
        return advisory


# ─────────────────────────────────────────────────────────────────────────────
# Telegram formatting helper
# ─────────────────────────────────────────────────────────────────────────────


_LIQUIDITY_LABEL_BY_TYPE: dict[str, str] = {
    "asian_high": "Asian High",
    "asian_low": "Asian Low",
    "london_high": "London High",
    "london_low": "London Low",
    "new_york_high": "New York High",
    "new_york_low": "New York Low",
    "previous_day_high": "Previous Day High",
    "previous_day_low": "Previous Day Low",
    "current_day_high": "Current Day High",
    "current_day_low": "Current Day Low",
    "equal_highs": "Equal Highs",
    "equal_lows": "Equal Lows",
    "range_high": "Range High",
    "range_low": "Range Low",
}


def _liquidity_state(lvl: dict[str, Any]) -> str:
    if lvl.get("swept") and lvl.get("reclaimed"):
        return "swept/reclaimed"
    if lvl.get("swept"):
        return "swept"
    return "unswept"


def _liquidity_line(lvl: dict[str, Any]) -> str:
    level_type = str(lvl.get("level_type") or "")
    label = _LIQUIDITY_LABEL_BY_TYPE.get(level_type, level_type.replace("_", " ").title() or "Level")
    return (
        f"{label}: {float(lvl.get('level') or 0.0):.2f} - "
        f"{_liquidity_state(lvl)} - {lvl.get('quality_label') or 'n/a'}"
    )


def format_session_liquidity_section(summary: dict[str, Any] | None) -> str:
    if not summary or not summary.get("enabled"):
        return ""
    active_levels = list(summary.get("active_levels") or [])
    inactive_levels = list(summary.get("inactive_levels") or [])
    # Back-compat: if the engine hasn't populated split lists yet, derive
    # them client-side from the legacy "levels" array.
    if not active_levels and not inactive_levels:
        for lvl in summary.get("levels", []) or []:
            label = str(lvl.get("quality_label") or "").upper()
            score = float(lvl.get("liquidity_score") or 0.0)
            if score >= 70 and label not in {"IGNORE", "WEAK LIQUIDITY"}:
                active_levels.append(lvl)
            else:
                inactive_levels.append(lvl)

    lines = ["Session Liquidity:"]
    if active_levels:
        lines.append("Active Liquidity:")
        for lvl in active_levels[:8]:
            lines.append(f"  {_liquidity_line(lvl)}")
    else:
        lines.append("Active Liquidity: none — all session pools currently context-only.")
    if inactive_levels:
        lines.append("Inactive / Context Liquidity:")
        for lvl in inactive_levels[:8]:
            lines.append(f"  {_liquidity_line(lvl)}")
    if summary.get("nearest_buy_side_liquidity"):
        lines.append(f"Nearest Buy-side Liquidity: {float(summary['nearest_buy_side_liquidity']):.2f}")
    if summary.get("nearest_sell_side_liquidity"):
        lines.append(f"Nearest Sell-side Liquidity: {float(summary['nearest_sell_side_liquidity']):.2f}")
    if summary.get("liquidity_bias"):
        lines.append(f"Liquidity Bias: {summary['liquidity_bias']}")
    expected = str(summary.get("expected_play") or "").strip()
    if expected:
        lines.append(f"Expected Play: {expected}")
    return "\n".join(lines)


def format_session_liquidity_entry_section(setup: dict[str, Any] | None) -> str:
    if not setup:
        return ""
    name = str(setup.get("setup_type", "")).replace("_", " ").title()
    swept = setup.get("swept_level") or 0.0
    swept_type = str(setup.get("swept_level_type", "")).replace("_", " ").title()
    quality = setup.get("quality_label") or "WATCH"
    sweep_event = setup.get("sweep_event") or {}
    notes = setup.get("notes") or ""
    targets = setup.get("target_levels") or []
    confirmations = setup.get("confirmation_required") or []
    lines = [
        "Session Liquidity:",
        f"Setup: {name}",
        f"Swept Level: {swept_type} {float(swept):.2f}",
        f"Trap Quality: {quality}",
    ]
    if sweep_event.get("trap_quality_score") is not None:
        lines.append(f"Trap Score: {float(sweep_event['trap_quality_score']):.0f}")
    if notes:
        lines.append(f"Trap Evidence: {notes}")
    if targets:
        lines.append(f"Liquidity Targets: {' / '.join(f'{float(t):.2f}' for t in targets)}")
    if confirmations:
        lines.append(f"Confirmations Allowed: {', '.join(confirmations)}")
    return "\n".join(lines)
