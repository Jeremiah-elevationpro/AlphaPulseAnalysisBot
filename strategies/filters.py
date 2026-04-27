"""
AlphaPulse - Market Context Filters
=====================================
Pure-function helpers used by MultiTimeframeAnalyzer to evaluate
whether a given scan cycle is a high-quality trading environment.

Filters:
  SessionFilter        — London / New York session detection
  TrendFilter          — H4 EMA bias (bullish / bearish / neutral)
  VolatilityFilter     — minimum average candle body check
  LiquiditySweepFilter — detect sweep of recent swing high/low
  NewsFilter           — avoid ± window around major USD news times
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

from config.settings import (
    SESSION_ASIA_UTC, SESSION_LONDON_UTC, SESSION_NEW_YORK_UTC,
    BOT_ACTIVE_START_HOUR, BOT_ACTIVE_END_HOUR, BOT_TIMEZONE,
    ALLOWED_SESSIONS, BLOCK_OFF_SESSION, OPERATING_MODE,
    H4_EMA_PERIOD, VOLATILITY_MIN_BODY,
    USD_NEWS_TIMES, NEWS_FILTER_MINUTES,
    PIP_SIZE, LEVEL_TOLERANCE_PIPS,
    MAX_LEVEL_TOUCHES, MAX_LEVEL_BREAKS,
    MIN_APPROACH_DISTANCE_PIPS,
    IMPULSE_LOOKBACK, IMPULSE_MIN_CANDLES, IMPULSE_BODY_RATIO,
    LEVEL_MIN_QUALITY_MAJOR,
    BIAS_EMA_PERIOD, BIAS_RECENT_LOOKBACK,
)
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────
# RESULT DATACLASS
# ─────────────────────────────────────────────────────────

@dataclass
class MarketContext:
    """Snapshot of all market-context information for a single scan cycle."""
    utc_time: datetime

    # Session
    session_name: str = "off_session"  # "asia" | "london" | "new_york" | "overlap" | "off_session"
    is_high_conf_session: bool = False
    session_allowed: bool = True
    session_block_reason: str = ""
    bot_window_active: bool = True
    local_time: str = ""
    active_until: str = ""

    # Trend
    h4_bias: str = "neutral"         # "bullish" | "bearish" | "neutral"
    d1_bias: str = "neutral"
    h1_bias: str = "neutral"
    dominant_bias: str = "neutral"   # "bullish" | "bearish" | "mixed" | "neutral"
    bias_strength: str = "weak"      # "strong" | "moderate" | "weak"
    h1_state: str = "range"          # "impulsive" | "pullback" | "range" | "reversal_attempt"
    bias_note: str = ""

    # Volatility
    is_volatile: bool = True         # False → skip trades this cycle

    # Liquidity sweep on M15 (most granular execution TF)
    sweep_direction: str = ""        # "BUY" | "SELL" | ""
    sweep_price: float = 0.0

    # News
    is_news_window: bool = False     # True → avoid all trades

    def allows_trade(self) -> bool:
        return self.is_volatile and not self.is_news_window and self.session_allowed

    def allows_counter_trend(self, is_qm: bool, is_psych: bool) -> bool:
        """Counter-trend only allowed when backed by QM or psychological level."""
        return is_qm or is_psych


# ─────────────────────────────────────────────────────────
# SESSION FILTER
# ─────────────────────────────────────────────────────────

class SessionFilter:
    """Identifies whether the current UTC time falls in a high-probability session."""

    def __init__(self):
        try:
            self._local_tz = ZoneInfo(BOT_TIMEZONE)
        except Exception:
            self._local_tz = datetime.now().astimezone().tzinfo or timezone.utc

    def get_session(self, utc_dt: datetime) -> str:
        h = utc_dt.hour
        in_asia = SESSION_ASIA_UTC[0] <= h < SESSION_ASIA_UTC[1]
        in_london = SESSION_LONDON_UTC[0] <= h < SESSION_LONDON_UTC[1]
        in_new_york = SESSION_NEW_YORK_UTC[0] <= h < SESSION_NEW_YORK_UTC[1]
        if in_asia and (in_london or in_new_york):
            return "overlap"
        if in_london and in_new_york:
            return "overlap"
        if in_asia:
            return "asia"
        if in_london:
            return "london"
        if in_new_york:
            return "new_york"
        return "off_session"

    def is_high_confidence(self, utc_dt: datetime) -> bool:
        return self.get_session(utc_dt) in ALLOWED_SESSIONS

    def local_time(self, utc_dt: datetime) -> datetime:
        return utc_dt.astimezone(self._local_tz)

    def is_bot_window_active(self, utc_dt: datetime) -> tuple[bool, str, str]:
        local_dt = self.local_time(utc_dt)
        local_time_str = local_dt.strftime("%H:%M")
        # 24/7 mode — always active regardless of local hour
        return True, local_time_str, "24/7"

    def is_allowed(self, utc_dt: datetime, session_name: str) -> tuple[bool, str, str, str]:
        _, local_time, active_until = self.is_bot_window_active(utc_dt)
        return True, "", local_time, active_until


# ─────────────────────────────────────────────────────────
# TREND FILTER (H4 EMA bias)
# ─────────────────────────────────────────────────────────

class TrendFilter:
    """
    Determines the H4 trend bias using an EMA comparison.

    Bullish  : last close > EMA(H4_EMA_PERIOD) and recent highs/lows rising
    Bearish  : last close < EMA(H4_EMA_PERIOD) and recent highs/lows falling
    Neutral  : otherwise
    """

    def get_bias(self, df_h4: Optional[pd.DataFrame]) -> str:
        if df_h4 is None or len(df_h4) < H4_EMA_PERIOD + 5:
            return "neutral"

        closes = df_h4["close"].values
        ema = self._ema(closes, H4_EMA_PERIOD)
        last_close = closes[-1]
        last_ema   = ema[-1]

        # Simple swing structure confirmation: compare last two swing pairs
        recent = closes[-10:]
        if last_close > last_ema and recent[-1] > recent[0]:
            return "bullish"
        if last_close < last_ema and recent[-1] < recent[0]:
            return "bearish"
        return "neutral"

    def is_aligned(self, bias: str, direction: str) -> bool:
        """Return True if the trade direction aligns with the H4 bias."""
        if bias == "bullish" and direction == "BUY":
            return True
        if bias == "bearish" and direction == "SELL":
            return True
        if bias == "neutral":
            return True   # neutral allows both
        return False

    @staticmethod
    def _ema(closes: np.ndarray, period: int) -> np.ndarray:
        k = 2 / (period + 1)
        ema = np.zeros_like(closes, dtype=float)
        ema[0] = closes[0]
        for i in range(1, len(closes)):
            ema[i] = closes[i] * k + ema[i - 1] * (1 - k)
        return ema


class DirectionalBiasEngine:
    """
    Builds a D1/H4/H1 directional map for continuation bias.

    D1 is macro context, H4 is dominant swing bias, and H1 describes the
    local execution state. Pullbacks against aligned D1/H4 do not flip the
    dominant bias; they are treated as continuation opportunities.
    """

    def __init__(self):
        self._trend = TrendFilter()

    def analyze(self, data: Dict[str, pd.DataFrame]) -> dict:
        d1_bias = self._tf_bias(data.get("D1"))
        h4_bias = self._tf_bias(data.get("H4"))
        h1_bias = self._tf_bias(data.get("H1"))

        dominant = self._dominant_bias(d1_bias, h4_bias)
        strength = self._strength(d1_bias, h4_bias, h1_bias, dominant)
        h1_state = self._h1_state(data.get("H1"), dominant, h1_bias, d1_bias, h4_bias)
        note = (
            f"D1={d1_bias} H4={h4_bias} H1={h1_bias} "
            f"=> dominant={dominant}/{strength}, H1={h1_state}"
        )

        return {
            "d1_bias": d1_bias,
            "h4_bias": h4_bias,
            "h1_bias": h1_bias,
            "dominant_bias": dominant,
            "bias_strength": strength,
            "h1_state": h1_state,
            "bias_note": note,
        }

    def _tf_bias(self, df: Optional[pd.DataFrame]) -> str:
        if df is None or len(df) < BIAS_EMA_PERIOD + BIAS_RECENT_LOOKBACK:
            return "neutral"

        closes = df["close"].values.astype(float)
        ema = self._trend._ema(closes, BIAS_EMA_PERIOD)
        last_close = float(closes[-1])
        last_ema = float(ema[-1])
        recent = closes[-BIAS_RECENT_LOOKBACK:]
        net = float(recent[-1] - recent[0])

        if last_close > last_ema and net > 0:
            return "bullish"
        if last_close < last_ema and net < 0:
            return "bearish"
        return "neutral"

    @staticmethod
    def _dominant_bias(d1_bias: str, h4_bias: str) -> str:
        if d1_bias in ("bullish", "bearish") and h4_bias in ("bullish", "bearish"):
            if d1_bias == h4_bias:
                return h4_bias
            return "mixed"
        if h4_bias in ("bullish", "bearish"):
            return h4_bias
        if d1_bias in ("bullish", "bearish"):
            return d1_bias
        return "neutral"

    @staticmethod
    def _strength(d1_bias: str, h4_bias: str, h1_bias: str, dominant: str) -> str:
        if dominant not in ("bullish", "bearish"):
            return "weak"
        votes = sum(1 for bias in (d1_bias, h4_bias, h1_bias) if bias == dominant)
        if d1_bias == dominant and h4_bias == dominant and votes >= 2:
            return "strong" if h1_bias in (dominant, "neutral") else "moderate"
        if h4_bias == dominant and votes >= 2:
            return "moderate"
        return "weak"

    @staticmethod
    def _h1_state(
        df_h1: Optional[pd.DataFrame],
        dominant: str,
        h1_bias: str,
        d1_bias: str,
        h4_bias: str,
    ) -> str:
        if dominant not in ("bullish", "bearish"):
            return "range"
        if df_h1 is None or len(df_h1) < BIAS_RECENT_LOOKBACK + 2:
            return "range"

        closes = df_h1["close"].values.astype(float)
        recent = closes[-BIAS_RECENT_LOOKBACK:]
        net = float(recent[-1] - recent[0])
        aligned_htf = d1_bias == h4_bias == dominant

        if h1_bias == dominant:
            return "impulsive"
        if aligned_htf:
            if dominant == "bullish" and net < 0:
                return "pullback"
            if dominant == "bearish" and net > 0:
                return "pullback"
        if h1_bias in ("bullish", "bearish") and h1_bias != dominant:
            return "reversal_attempt"
        return "range"


# ─────────────────────────────────────────────────────────
# VOLATILITY FILTER
# ─────────────────────────────────────────────────────────

class VolatilityFilter:
    """
    Skips the scan cycle when recent candles are too quiet.
    Uses average body size of last N candles vs VOLATILITY_MIN_BODY threshold.
    """

    def is_sufficient(self, df: Optional[pd.DataFrame], n: int = 5) -> bool:
        if df is None or len(df) < n + 1:
            return True   # no data → don't block

        recent = df.iloc[-(n + 1):-1]   # last n closed candles
        bodies = (recent["close"] - recent["open"]).abs()
        avg_body = bodies.mean()

        if avg_body < VOLATILITY_MIN_BODY:
            logger.debug("Low volatility: avg body=%.4f < %.4f — skipping",
                         avg_body, VOLATILITY_MIN_BODY)
            return False
        return True


# ─────────────────────────────────────────────────────────
# LIQUIDITY SWEEP DETECTOR
# ─────────────────────────────────────────────────────────

class LiquiditySweepFilter:
    """
    Detects when the last completed candle swept a recent swing high or low
    and then closed back inside — classic stop-hunt / liquidity grab pattern.

    BUY sweep  : candle.low < swing_low  AND candle.close > swing_low
    SELL sweep : candle.high > swing_high AND candle.close < swing_high
    """

    def detect(
        self, df: Optional[pd.DataFrame], lookback: int = 20
    ) -> tuple[str, float]:
        """
        Returns (direction, sweep_price) or ("", 0.0) if no sweep found.
        direction: "BUY" | "SELL" | ""
        """
        if df is None or len(df) < lookback + 2:
            return "", 0.0

        # Last closed candle
        c = df.iloc[-2]
        h, l, close = float(c["high"]), float(c["low"]), float(c["close"])

        # Reference window (exclude the last candle itself)
        ref = df.iloc[-(lookback + 2):-2]
        swing_high = float(ref["high"].max())
        swing_low  = float(ref["low"].min())

        if l < swing_low and close > swing_low:
            logger.debug("Liquidity sweep: BUY (swept low %.2f, closed %.2f)",
                         swing_low, close)
            return "BUY", round(swing_low, 2)

        if h > swing_high and close < swing_high:
            logger.debug("Liquidity sweep: SELL (swept high %.2f, closed %.2f)",
                         swing_high, close)
            return "SELL", round(swing_high, 2)

        return "", 0.0


# ─────────────────────────────────────────────────────────
# NEWS FILTER
# ─────────────────────────────────────────────────────────

class NewsFilter:
    """
    Blocks trading within ±NEWS_FILTER_MINUTES of any major USD news event.

    USD_NEWS_TIMES in settings.py holds a list of UTC time strings ("HH:MM").
    To add upcoming events, update that list before running the bot:

        USD_NEWS_TIMES = ["13:30", "18:00"]   # NFP, FOMC

    The filter matches against today's date and the configured times.
    """

    def _get_news_datetimes(self, utc_dt: datetime) -> List[datetime]:
        """Convert HH:MM strings into full datetime objects for today."""
        events = []
        for t in USD_NEWS_TIMES:
            try:
                h, m = map(int, t.split(":"))
                ev = utc_dt.replace(hour=h, minute=m, second=0, microsecond=0,
                                    tzinfo=timezone.utc)
                events.append(ev)
            except (ValueError, AttributeError):
                logger.warning("Invalid news time format: %s (expected HH:MM)", t)
        return events

    def is_safe(self, utc_dt: datetime) -> bool:
        """Return True if it is SAFE to trade (no news within the window)."""
        if not USD_NEWS_TIMES:
            return True   # no news schedule configured → always safe

        window = timedelta(minutes=NEWS_FILTER_MINUTES)
        for ev in self._get_news_datetimes(utc_dt):
            if abs(utc_dt - ev) <= window:
                logger.info("News filter: blocking trade (within %d min of %s UTC)",
                            NEWS_FILTER_MINUTES, ev.strftime("%H:%M"))
                return False
        return True


# ─────────────────────────────────────────────────────────
# STRUCTURE QUALITY FILTER
# ─────────────────────────────────────────────────────────

class StructureQualityFilter:
    """
    Enforces five structural quality rules on every candidate level and setup.

    Filter 1 — Clean Level:
      Reject levels tested more than MAX_LEVEL_TOUCHES times or broken
      more than MAX_LEVEL_BREAKS times (unless the level is a QM).

    Filter 2 — Approach Distance:
      The candle BEFORE the confirmation candle must have been at least
      MIN_APPROACH_DISTANCE_PIPS from the level.  Prevents grinding/sideways
      entries where price was already sitting on the level.

    Filter 3 — Impulse:
      At least IMPULSE_MIN_CANDLES out of the last IMPULSE_LOOKBACK candles
      before the confirmation must be directional toward the level,
      OR any one of those candles must have a body ≥ IMPULSE_BODY_RATIO × average.

    Filter 4 — Structure Alignment:
      Only allow levels with scope "recent" or "previous", OR any level
      marked is_qm=True.  Major scope (non-QM) and pure psych levels rejected.
    """

    # ── Filter 1: Clean Level ────────────────────────────────────────────────

    def is_level_clean(self, level) -> bool:
        """
        Return True if the level passes all structural quality checks.
        QM levels bypass touch/break limits (already heavily tested by definition).
        Psych-only levels (scope="psych") skip the quality score gate.
        """
        if level.is_qm:
            return True  # QM: break history already baked in — high priority

        if level.touch_count > MAX_LEVEL_TOUCHES:
            logger.info(
                "LEVEL REJECTED: %s %.2f | Reason: overworked (%d touches > %d max)",
                level.level_type, level.price, level.touch_count, MAX_LEVEL_TOUCHES,
            )
            return False

        if level.break_count > MAX_LEVEL_BREAKS:
            logger.info(
                "LEVEL REJECTED: %s %.2f | Reason: dead level (%d breaks > %d max)",
                level.level_type, level.price, level.break_count, MAX_LEVEL_BREAKS,
            )
            return False

        # Quality score gate — psych-only levels are exempt (they have no impulse score)
        if level.scope != "psych":
            min_q = LEVEL_MIN_QUALITY_MAJOR
            if getattr(level, "quality_score", 0.0) < min_q:
                logger.info(
                    "LEVEL REJECTED: %s %.2f | Reason: low quality score "
                    "(Q=%.0f < %.0f min)",
                    level.level_type, level.price,
                    level.quality_score, min_q,
                )
                return False

        return True

    # ── Filter 2 + 3: Approach Distance + Impulse ───────────────────────────

    def is_approach_valid(
        self,
        df: pd.DataFrame,
        level,
        conf_candle_idx: int = -2,   # position of confirmation candle in df
        lookback: int = IMPULSE_LOOKBACK,
    ) -> bool:
        """
        Check that the candles immediately BEFORE the confirmation candle:
          (a) started at least MIN_APPROACH_DISTANCE_PIPS from the level, and
          (b) showed directional momentum toward the level.

        conf_candle_idx: iloc position of the confirmation candle (default -2
        because lookback=1 makes the confirmation candle second-to-last).
        """
        if df is None or len(df) < lookback + 3:
            return True  # insufficient data — do not block

        tol      = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        min_dist = MIN_APPROACH_DISTANCE_PIPS * PIP_SIZE
        lp       = level.price

        # Approach slice: the `lookback` candles ending just before the confirmation
        end_pos   = len(df) + conf_candle_idx   # absolute position of conf candle
        start_pos = max(0, end_pos - lookback)
        approach = df.iloc[start_pos:end_pos]

        if len(approach) == 0:
            return True

        opens  = approach["open"].values
        closes = approach["close"].values
        bodies = np.abs(closes - opens)

        # ── Filter 2: distance ──────────────────────────────────────────────
        # The FIRST candle in the approach window must have been far enough away.
        first_close = closes[0]
        if abs(first_close - lp) < min_dist:
            logger.info(
                "SETUP REJECTED: XAUUSD | Reason: too close to price "
                "(approach candle %.2f only %.1fp from level %.2f, min %dp) | Level %s",
                first_close,
                abs(first_close - lp) / PIP_SIZE,
                lp,
                MIN_APPROACH_DISTANCE_PIPS,
                level.level_type,
            )
            return False

        # ── Filter 3: impulse ───────────────────────────────────────────────
        if level.level_type == "A":
            # Approaching resistance → bullish approach (closes rising)
            directional = int(np.sum(closes > opens))
        else:
            # Approaching support (V / Gap) → bearish approach (closes falling)
            directional = int(np.sum(closes < opens))

        # Condition A: 3+ directional candles
        if directional >= IMPULSE_MIN_CANDLES:
            return True

        # Condition B: at least one candle with body ≥ 1.5× average
        avg_body = bodies.mean() if len(bodies) > 0 else 0.0
        if avg_body > 0 and bodies.max() >= avg_body * IMPULSE_BODY_RATIO:
            return True

        logger.info(
            "SETUP REJECTED: XAUUSD | Reason: no impulse approach "
            "(%d/%d directional candles, largest body=%.1fx avg) | Level %s %.2f",
            directional, len(closes),
            bodies.max() / avg_body if avg_body > 0 else 0.0,
            level.level_type, lp,
        )
        return False

    # ── Filter 4: Structure Alignment ───────────────────────────────────────

    def is_structure_aligned(self, level) -> bool:
        """
        Return True only for levels from clear structural context:
          - recent/previous scope → always allowed
          - is_qm=True → always allowed (regardless of scope)
          - displacement_pips >= 50 → allowed (Reversal Origin Level with validated impulse)
          - major scope, non-QM, low displacement → rejected
          - psych scope → rejected (standalone round numbers not tradeable alone)
        """
        if level.is_qm:
            return True
        if level.scope in ("recent", "previous"):
            return True
        # Reversal Origin Levels carry displacement_pips from their validated impulse move.
        # A 50+ pip institutional displacement is equivalent structural evidence to QM/recent.
        if getattr(level, "displacement_pips", 0.0) >= 50.0:
            return True
        # major or psych without QM qualification or validated impulse
        logger.info(
            "LEVEL REJECTED: %s %.2f | Reason: poor structure "
            "(scope=%s, disp=%.0fp — need recent/previous/QM or 50+ pip displacement)",
            level.level_type, level.price, level.scope,
            getattr(level, "displacement_pips", 0.0),
        )
        return False


# ─────────────────────────────────────────────────────────
# MARKET CONTEXT ENGINE
# ─────────────────────────────────────────────────────────

class MarketContextEngine:
    """
    Runs all filters and returns a single MarketContext object
    that the multi-timeframe analyzer uses to gate and adjust setups.
    """

    def __init__(self):
        self._session    = SessionFilter()
        self._trend      = TrendFilter()
        self._bias       = DirectionalBiasEngine()
        self._volatility = VolatilityFilter()
        self._sweep      = LiquiditySweepFilter()
        self._news       = NewsFilter()
        logger.info(
            "Session filter configured: operating_mode=%s | allowed=%s | block_off_session=%s",
            OPERATING_MODE,
            ",".join(ALLOWED_SESSIONS),
            BLOCK_OFF_SESSION,
        )
        logger.info("SPENCER 24/7 MODE: session filters do not block alerts")

    def analyze(
        self,
        data: Dict[str, pd.DataFrame],
        utc_dt: Optional[datetime] = None,
    ) -> MarketContext:
        if utc_dt is None:
            utc_dt = datetime.now(timezone.utc)

        ctx = MarketContext(utc_time=utc_dt)

        # Session
        ctx.session_name        = self._session.get_session(utc_dt)
        ctx.is_high_conf_session = self._session.is_high_confidence(utc_dt)
        (
            ctx.session_allowed,
            ctx.session_block_reason,
            ctx.local_time,
            ctx.active_until,
        ) = self._session.is_allowed(utc_dt, ctx.session_name)
        ctx.bot_window_active = ctx.session_allowed

        # Multi-timeframe directional bias
        bias = self._bias.analyze(data)
        ctx.d1_bias = bias["d1_bias"]
        ctx.h4_bias = bias["h4_bias"]
        ctx.h1_bias = bias["h1_bias"]
        ctx.dominant_bias = bias["dominant_bias"]
        ctx.bias_strength = bias["bias_strength"]
        ctx.h1_state = bias["h1_state"]
        ctx.bias_note = bias["bias_note"]

        # Volatility (use M15 — execution timeframe)
        ctx.is_volatile = self._volatility.is_sufficient(data.get("M15"))

        # Liquidity sweep (M15)
        ctx.sweep_direction, ctx.sweep_price = self._sweep.detect(data.get("M15"))

        # News filter
        ctx.is_news_window = not self._news.is_safe(utc_dt)

        logger.info(
            "SESSION CONTEXT: label=%s | scan_allowed=true | mode=24_7 | local_time=%s",
            ctx.session_name, ctx.local_time,
        )
        logger.debug(
            "MarketContext | session=%s | %s | volatile=%s | "
            "sweep=%s | news_block=%s | session_allowed=%s | local_time=%s",
            ctx.session_name, ctx.bias_note,
            ctx.is_volatile, ctx.sweep_direction or "none",
            ctx.is_news_window, ctx.session_allowed, ctx.local_time,
        )
        return ctx

    def is_direction_trend_aligned(self, ctx: MarketContext, direction: str) -> bool:
        dominant = getattr(ctx, "dominant_bias", ctx.h4_bias)
        strength = getattr(ctx, "bias_strength", "weak")
        if strength == "weak":
            return True
        if dominant == "bullish":
            return direction == "BUY"
        if dominant == "bearish":
            return direction == "SELL"
        return True
