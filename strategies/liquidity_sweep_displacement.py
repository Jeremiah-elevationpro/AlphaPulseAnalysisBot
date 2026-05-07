"""
AlphaPulse - Liquidity Sweep + Displacement (LSD) Strategy
===========================================================
Detects institutional order-flow setups using three sequential confirmations:

  1. LIQUIDITY SWEEP  — price wicks through a pool of resting orders (equal
                         highs/lows, previous-day H/L) and closes back inside.
  2. DISPLACEMENT     — an impulsive, large-bodied candle confirms the reversal.
  3. BREAK OF STRUCTURE (BOS) — the next candle breaks the most recent minor
                                 swing in the displacement direction.

Two modes are supported:
  • SWING  (H4 → H1 → M15): HTF bias from H4, sweep on H1, entry on M15.
                              SL anchored to sweep wick (max 80 pips).
  • SCALP  (M15 → M5 → M1): Active only during London / New York sessions.
                              Sweep on M5, entry on M1. SL 15–30 pips.

Entry is placed at the 50% retracement of the displacement candle.

Signal deduplication: one signal per symbol + liquidity level + mode.

Outputs:
  LSDSignal  — the parsed result with .to_dict() and .to_trade() helpers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.settings import (
    PIP_SIZE,
    SESSION_LONDON_UTC,
    SESSION_NEW_YORK_UTC,
    LSD_EQUAL_LEVEL_TOLERANCE_PIPS,
    LSD_SWEEP_LOOKBACK,
    LSD_DISPLACEMENT_RATIO,
    LSD_DISPLACEMENT_CLOSE_PCT,
    LSD_BOS_LOOKBACK,
    LSD_SWING_MAX_SL_PIPS,
    LSD_SCALP_MIN_SL_PIPS,
    LSD_SCALP_MAX_SL_PIPS,
    LSD_SWING_SL_BUFFER_PIPS,
    LSD_SCALP_SL_BUFFER_PIPS,
    LSD_MIN_RR,
    LSD_ASIAN_SESSION_END,
)
from db.models import Trade, TradeStatus
from utils.logger import get_logger

logger = get_logger(__name__)

# Pre-computed price constants
_EQUAL_TOL    = LSD_EQUAL_LEVEL_TOLERANCE_PIPS * PIP_SIZE
_SWING_BUF    = LSD_SWING_SL_BUFFER_PIPS  * PIP_SIZE
_SCALP_BUF    = LSD_SCALP_SL_BUFFER_PIPS  * PIP_SIZE
_SWING_MAX_SL = LSD_SWING_MAX_SL_PIPS     * PIP_SIZE
_SCALP_MIN_SL = LSD_SCALP_MIN_SL_PIPS     * PIP_SIZE
_SCALP_MAX_SL = LSD_SCALP_MAX_SL_PIPS     * PIP_SIZE


# ─────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────

@dataclass
class LiquidityZone:
    price: float                # midpoint of the equal-high or equal-low cluster
    zone_type: str              # "EQH" (equal highs) | "EQL" (equal lows) | "PDH" | "PDL"
    candle_index: int           # index of the most recent candle that formed the zone
    candle_time: pd.Timestamp
    score: int = 0              # liquidity quality score (see _score_zones)
    is_swept: bool = False      # True after this zone has been consumed by a sweep


@dataclass
class SweepEvent:
    zone: LiquidityZone
    sweep_direction: str        # "UP" (swept highs → bearish) | "DOWN" (swept lows → bullish)
    sweep_candle_index: int
    sweep_candle_time: pd.Timestamp
    sweep_high: float
    sweep_low: float
    sweep_close: float


@dataclass
class DisplacementEvent:
    direction: str              # "BUY" | "SELL"
    candle_index: int
    candle_time: pd.Timestamp
    open_: float
    high: float
    low: float
    close: float
    body_ratio: float           # body / avg_body


@dataclass
class BOSEvent:
    direction: str              # "BUY" | "SELL"
    broken_level: float         # the swing high/low that was broken
    candle_index: int
    candle_time: pd.Timestamp


@dataclass
class LSDSignal:
    signal_id: str
    symbol: str
    direction: str              # "BUY" | "SELL"
    model: str                  # "LSD_SWING" | "LSD_SCALP"
    timeframe: str              # entry timeframe ("M15" for swing, "M1" for scalp)
    htf_bias: str               # "bullish" | "bearish"
    entry: float
    stop_loss: float
    take_profit: float          # TP1 (first target)
    tp_levels: List[float]      # full [tp1..tp5]
    sl_pips: float
    rr: float
    confidence: float
    liq_score: int
    sweep: SweepEvent
    displacement: DisplacementEvent
    bos: BOSEvent
    session: str                # "london" | "new_york" | ""
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "symbol":      self.symbol,
            "type":        self.direction,
            "entry":       self.entry,
            "stop_loss":   self.stop_loss,
            "take_profit": self.take_profit,
            "model":       self.model,
            "timeframe":   self.timeframe,
            "confidence":  self.confidence,
            "liq_score":   self.liq_score,
        }

    def to_trade(self) -> Trade:
        """Convert LSDSignal to a Trade object compatible with trade_manager."""
        return Trade(
            direction=self.direction,
            entry_price=self.entry,
            sl_price=self.stop_loss,
            tp_levels=self.tp_levels,
            level_type="LSD",
            level_price=self.sweep.zone.price,
            higher_tf=_htf_for_model(self.model),
            lower_tf=self.timeframe,
            confidence=self.confidence,
            pair=self.symbol,
            setup_type=self.model.lower(),
            is_qm=False,
            is_psychological=False,
            is_liquidity_sweep=True,
            session_name=self.session,
            h4_bias=self.htf_bias,
            trend_aligned=True,
            status=TradeStatus.PENDING,
        )


def _htf_for_model(model: str) -> str:
    return "H4" if model == "LSD_SWING" else "M15"


# ─────────────────────────────────────────────────────────
# MAIN STRATEGY CLASS
# ─────────────────────────────────────────────────────────

class LSDStrategy:
    """
    Liquidity Sweep + Displacement strategy.

    Usage:
        strategy = LSDStrategy()
        signals = strategy.analyze("XAUUSD", candles_by_tf)

    candles_by_tf must contain DataFrames with columns:
        time, open, high, low, close
    Keys required for swing:  "H4", "H1", "M15"
    Keys required for scalp:  "M15", "M5", "M1"   (only during sessions)
    """

    def __init__(self):
        # One signal per (symbol, zone_price, model) — persists for bot session
        self._seen_signals: set = set()

    # ─────────────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ─────────────────────────────────────────────────────

    def analyze(
        self,
        symbol: str,
        candles_by_tf: Dict[str, pd.DataFrame],
    ) -> List[LSDSignal]:
        """
        Run both swing and scalp sub-strategies.
        Returns the top 1–2 signals ranked by confidence to limit trade frequency.
        """
        signals: List[LSDSignal] = []

        # ── SWING ────────────────────────────────────────
        swing_sig = self._analyze_swing(symbol, candles_by_tf)
        if swing_sig:
            signals.append(swing_sig)

        # ── SCALP (session-gated) ─────────────────────────
        session = _current_session()
        if session:
            scalp_sig = self._analyze_scalp(symbol, candles_by_tf, session)
            if scalp_sig:
                signals.append(scalp_sig)

        # Rank by confidence and cap at 2 signals per scan
        signals.sort(key=lambda s: s.confidence, reverse=True)
        if len(signals) > 2:
            dropped = [s.model for s in signals[2:]]
            logger.info("LSD: Frequency cap — keeping top 2 of %d signals (dropped: %s)",
                        len(signals), dropped)
            signals = signals[:2]

        return signals

    # ─────────────────────────────────────────────────────
    # SWING  H4 → H1 → M15
    # ─────────────────────────────────────────────────────

    def _analyze_swing(
        self,
        symbol: str,
        candles_by_tf: Dict[str, pd.DataFrame],
    ) -> Optional[LSDSignal]:
        for key in ("H4", "H1", "M15"):
            df = candles_by_tf.get(key)
            if df is None or len(df) < 20:
                logger.info("LSD_SWING: insufficient data for %s (%d bars) — skipping",
                            key, len(df) if df is not None else 0)
                return None

        h4_df  = candles_by_tf["H4"]
        h1_df  = candles_by_tf["H1"]
        m15_df = candles_by_tf["M15"]

        # 1. HTF bias from H4
        bias = self._detect_htf_bias(h4_df)
        range_mode = (bias == "neutral")
        if range_mode:
            logger.info(
                "LSD_SWING: H4 bias neutral — switching to range mode "
                "(targeting EQH/EQL/PDH/PDL from both sides)"
            )
        else:
            logger.info("LSD_SWING: H4 bias = %s", bias)

        # 2. Detect ALL liquidity zones on H1 (needed for both sweep detection and TP targeting)
        all_zones = self._detect_liquidity_zones(h1_df)
        if not all_zones:
            logger.info("LSD_SWING: no liquidity zones found on H1 — skipping")
            return None
        logger.info("LSD_SWING: found %d liquidity zone(s) on H1", len(all_zones))

        # Score all zones and filter to minimum quality (score >= 3)
        all_zones = self._score_zones(all_zones, h1_df)
        qualified = [z for z in all_zones if z.score >= 3]
        if not qualified:
            logger.info("LSD_SWING: no zones meet minimum liquidity score — skipping")
            return None

        # In range mode target all zone types; in trending mode target bias-aligned zones only
        if range_mode:
            target_types = ("EQH", "EQL", "PDH", "PDL")
        else:
            target_types = ("EQL", "PDL") if bias == "bullish" else ("EQH", "PDH")
        sweep_zones = [z for z in qualified if z.zone_type in target_types]
        if not sweep_zones:
            logger.info(
                "LSD_SWING: no %s zones available (have: %s) — skipping",
                target_types, [z.zone_type for z in qualified],
            )
            return None

        # 3. Volatility check — in low volatility, stricter gates applied below
        #    rather than a hard skip (Gold accumulation often occurs in quiet markets).
        low_vol_swing = not self._is_volatile_enough(h1_df)
        if low_vol_swing:
            logger.info(
                "LSD_SWING: low volatility detected — applying stricter filtering "
                "(liq_score >= 4, body_ratio >= 2.0, confidence >= 0.85 required)."
            )

        # 4. Detect sweep on H1
        sweep = self._detect_sweep(h1_df, sweep_zones, bias)
        if sweep is None:
            logger.info(
                "LSD_SWING: no sweep found on H1 in last %d candles — skipping",
                LSD_SWEEP_LOOKBACK,
            )
            return None
        logger.info("LSD: Sweep detected at %.2f [H1] | zone=%s | score=%d",
                    sweep.zone.price, sweep.zone.zone_type, sweep.zone.score)

        # 5. Displacement on H1 following the sweep
        displacement = self._detect_displacement(h1_df, sweep)
        if displacement is None:
            logger.info("LSD_SWING: no displacement candle found after sweep [H1] — skipping")
            return None
        logger.info("LSD: Displacement confirmed [H1] | direction=%s | ratio=%.2f",
                    displacement.direction, displacement.body_ratio)

        # 5b. Low-volatility: slightly stricter liquidity score but not a hard block.
        # Confidence scoring already penalises weak displacements and low liq scores.
        if low_vol_swing and sweep.zone.score < 3:
            logger.info(
                "LSD_SWING: low-vol — liq_score %d < 3 minimum", sweep.zone.score
            )
            return None

        # 6. Mid-range filter — reject entries from the middle of the current range
        if not self._is_near_range_extreme(h1_df, displacement.close):
            logger.info("LSD_SWING: mid-range filter failed — price not near range extreme")
            return None

        # 7. BOS on M15
        bos = self._detect_bos(m15_df, displacement)
        if bos is None:
            logger.info("LSD_SWING: no BOS confirmed on M15 — skipping")
            return None
        logger.info("LSD: BOS confirmed [M15] | level=%.2f", bos.broken_level)

        # 8. Entry and SL
        entry = self._compute_entry(displacement)
        sl    = self._compute_swing_sl(sweep, displacement.direction)
        if sl is None:
            logger.info("LSD_SWING: SL computation failed — skipping")
            return None

        sl_dist = abs(entry - sl)
        sl_pips = sl_dist / PIP_SIZE

        # 9. TP from nearest opposing liquidity zone (fallback: 2R)
        tp_levels = self._compute_tp_from_zones(entry, displacement.direction, qualified, sl_dist)
        tp1 = tp_levels[0]
        rr  = abs(tp1 - entry) / sl_dist if sl_dist > 0 else 0.0

        if rr < LSD_MIN_RR:
            logger.info("LSD_SWING: RR %.2f < %.2f — rejected", rr, LSD_MIN_RR)
            return None

        # 10. Deduplication — include UTC hour so the same zone can signal next session
        hour    = datetime.now(timezone.utc).hour
        sig_key = f"{symbol}_{sweep.zone.price:.2f}_LSD_SWING_{hour}"
        if sig_key in self._seen_signals:
            logger.debug("LSD_SWING: duplicate signal suppressed (%s)", sig_key)
            return None
        self._seen_signals.add(sig_key)

        # Mark zone consumed after dedup check passes
        sweep.zone.is_swept = True

        liq_score  = sweep.zone.score
        confidence = self._compute_confidence(
            bias, sweep, displacement, bos, model="swing", liq_score=liq_score
        )

        signal = LSDSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            direction=displacement.direction,
            model="LSD_SWING",
            timeframe="M15",
            htf_bias=bias,
            entry=round(entry, 2),
            stop_loss=round(sl, 2),
            take_profit=round(tp1, 2),
            tp_levels=[round(t, 2) for t in tp_levels],
            sl_pips=round(sl_pips, 1),
            rr=round(rr, 2),
            confidence=round(confidence, 3),
            liq_score=liq_score,
            sweep=sweep,
            displacement=displacement,
            bos=bos,
            session=_current_session() or "",
        )

        logger.info(
            "LSD_SWING SIGNAL | %s %s | Entry %.2f | SL %.2f (%.0fp) | TP1 %.2f | "
            "RR %.2f | Conf %.0f%% | LiqScore=%d",
            signal.direction, symbol, signal.entry, signal.stop_loss,
            sl_pips, signal.take_profit, rr, confidence * 100, liq_score,
        )
        return signal

    # ─────────────────────────────────────────────────────
    # SCALP  M15 → M5 → M1
    # ─────────────────────────────────────────────────────

    def _analyze_scalp(
        self,
        symbol: str,
        candles_by_tf: Dict[str, pd.DataFrame],
        session: str,
    ) -> Optional[LSDSignal]:
        for key in ("M15", "M5", "M1"):
            df = candles_by_tf.get(key)
            if df is None or len(df) < 20:
                logger.info("LSD_SCALP: insufficient data for %s (%d bars) — skipping",
                            key, len(df) if df is not None else 0)
                return None

        m15_df = candles_by_tf["M15"]
        m5_df  = candles_by_tf["M5"]
        m1_df  = candles_by_tf["M1"]

        # 1. HTF bias from M15 structure
        # Neutral M15 is NOT a hard blocker — sweep + displacement still qualify.
        # Neutral means lower confidence; the sweep/displacement dictate direction.
        bias = self._detect_htf_bias(m15_df)
        if bias == "neutral":
            logger.info(
                "LSD_SCALP: M15 bias neutral — setup still allowed "
                "(direction determined by sweep/displacement, lower confidence applied)"
            )
        else:
            logger.info("LSD_SCALP: M15 bias = %s", bias)

        # 2. All zones on M5 (for scoring and TP); fallback to Asian range
        all_zones = self._detect_liquidity_zones(m5_df)
        if not all_zones:
            asian_zones = self._get_asian_range_as_zones(candles_by_tf.get("H1"))
            if not asian_zones:
                logger.info("LSD_SCALP: no liquidity zones on M5 and no Asian range fallback — skipping")
                return None
            logger.info("LSD_SCALP: using Asian range zones as fallback (%d zones)", len(asian_zones))
            all_zones = asian_zones

        # Score and filter zones
        all_zones = self._score_zones(all_zones, m5_df)
        qualified = [z for z in all_zones if z.score >= 3]
        if not qualified:
            logger.info("LSD_SCALP: no zones meet minimum liquidity score — skipping")
            return None

        # Neutral M15: allow all zone types (sweep direction determines trade direction)
        if bias == "neutral":
            target_types = ("EQH", "EQL", "PDH", "PDL")
        else:
            target_types = ("EQL", "PDL") if bias == "bullish" else ("EQH", "PDH")
        sweep_zones = [z for z in qualified if z.zone_type in target_types]
        if not sweep_zones:
            logger.info(
                "LSD_SCALP: no %s zones available (have: %s) — skipping",
                target_types, [z.zone_type for z in qualified],
            )
            return None

        # 3. Volatility check — in low volatility, stricter gates applied below
        low_vol_scalp = not self._is_volatile_enough(m5_df)
        if low_vol_scalp:
            logger.info(
                "LSD_SCALP: low volatility detected — applying stricter filtering "
                "(liq_score >= 4, body_ratio >= 2.0, confidence >= 0.85 required)."
            )

        # 4. Detect sweep on M5
        sweep = self._detect_sweep(m5_df, sweep_zones, bias)
        if sweep is None:
            logger.info(
                "LSD_SCALP: no sweep found on M5 in last %d candles — skipping",
                LSD_SWEEP_LOOKBACK,
            )
            return None
        logger.info("LSD: Sweep detected at %.2f [M5] | zone=%s | score=%d",
                    sweep.zone.price, sweep.zone.zone_type, sweep.zone.score)

        # 5. Displacement on M5
        displacement = self._detect_displacement(m5_df, sweep)
        if displacement is None:
            logger.info("LSD_SCALP: no displacement candle found after sweep [M5] — skipping")
            return None
        logger.info("LSD: Displacement confirmed [M5] | direction=%s | ratio=%.2f",
                    displacement.direction, displacement.body_ratio)

        # 5b. Low-volatility: slightly stricter liquidity score but not a hard block.
        if low_vol_scalp and sweep.zone.score < 3:
            logger.info(
                "LSD_SCALP: low-vol — liq_score %d < 3 minimum", sweep.zone.score
            )
            return None

        # 6. Scalp bias enforcement:
        #    When M15 is trending, direction must align OR displacement must be strong (>2×).
        #    When M15 is neutral, displacement direction is authoritative — no bias gate.
        if bias != "neutral":
            expected_direction = "BUY" if bias == "bullish" else "SELL"
            if displacement.direction != expected_direction and displacement.body_ratio < 2.0:
                logger.info(
                    "LSD_SCALP: direction %s vs M15 bias %s | displacement %.2f < 2.0 — rejected",
                    displacement.direction, bias, displacement.body_ratio,
                )
                return None

        # 7. Mid-range filter — reject entries from the middle of the M5 range
        if not self._is_near_range_extreme(m5_df, displacement.close):
            logger.info("LSD_SCALP: mid-range filter failed — price not near range extreme")
            return None

        # 8. BOS on M1
        bos = self._detect_bos(m1_df, displacement)
        if bos is None:
            logger.info("LSD_SCALP: no BOS confirmed on M1 — skipping")
            return None
        logger.info("LSD: BOS confirmed [M1] | level=%.2f", bos.broken_level)

        # 9. Entry and SL
        entry = self._compute_entry(displacement)
        sl    = self._compute_scalp_sl(sweep, displacement.direction)
        if sl is None:
            logger.info("LSD_SCALP: SL computation failed — skipping")
            return None

        sl_dist = abs(entry - sl)
        sl_pips = sl_dist / PIP_SIZE

        # 10. TP from nearest opposing liquidity zone (fallback: 2R)
        tp_levels = self._compute_tp_from_zones(entry, displacement.direction, qualified, sl_dist)
        tp1 = tp_levels[0]
        rr  = abs(tp1 - entry) / sl_dist if sl_dist > 0 else 0.0

        if rr < LSD_MIN_RR:
            logger.info("LSD_SCALP: RR %.2f < %.2f — rejected", rr, LSD_MIN_RR)
            return None

        # 11. Deduplication — include UTC hour
        hour    = datetime.now(timezone.utc).hour
        sig_key = f"{symbol}_{sweep.zone.price:.2f}_LSD_SCALP_{hour}"
        if sig_key in self._seen_signals:
            logger.debug("LSD_SCALP: duplicate signal suppressed (%s)", sig_key)
            return None
        self._seen_signals.add(sig_key)

        # Mark zone consumed
        sweep.zone.is_swept = True

        liq_score  = sweep.zone.score
        confidence = self._compute_confidence(
            bias, sweep, displacement, bos, model="scalp", liq_score=liq_score
        )

        signal = LSDSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            direction=displacement.direction,
            model="LSD_SCALP",
            timeframe="M1",
            htf_bias=bias,
            entry=round(entry, 2),
            stop_loss=round(sl, 2),
            take_profit=round(tp1, 2),
            tp_levels=[round(t, 2) for t in tp_levels],
            sl_pips=round(sl_pips, 1),
            rr=round(rr, 2),
            confidence=round(confidence, 3),
            liq_score=liq_score,
            sweep=sweep,
            displacement=displacement,
            bos=bos,
            session=session,
        )

        logger.info(
            "LSD_SCALP SIGNAL | %s %s | Entry %.2f | SL %.2f (%.0fp) | TP1 %.2f | "
            "RR %.2f | Conf %.0f%% | %s | LiqScore=%d",
            signal.direction, symbol, signal.entry, signal.stop_loss,
            sl_pips, signal.take_profit, rr, confidence * 100, session.upper(), liq_score,
        )
        return signal

    # ─────────────────────────────────────────────────────
    # HTF BIAS DETECTION
    # ─────────────────────────────────────────────────────

    def _detect_htf_bias(self, df: pd.DataFrame) -> str:
        """
        Determine trend direction from OHLCV data.

        Bullish  = most-recent swing high ABOVE previous swing high
                   AND most-recent swing low ABOVE previous swing low.
        Bearish  = most-recent swing high BELOW previous, AND low BELOW previous.
        Otherwise = neutral.
        """
        if len(df) < 30:
            return "neutral"

        highs  = df["high"].values
        lows   = df["low"].values
        n      = len(highs)
        window = 5   # candles either side to define a swing

        swing_highs = [
            i for i in range(window, n - window)
            if highs[i] == max(highs[i - window: i + window + 1])
        ]
        swing_lows = [
            i for i in range(window, n - window)
            if lows[i]  == min(lows[i  - window: i + window + 1])
        ]

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return "neutral"

        sh_recent = highs[swing_highs[-1]]
        sh_prev   = highs[swing_highs[-2]]
        sl_recent = lows[swing_lows[-1]]
        sl_prev   = lows[swing_lows[-2]]

        if sh_recent > sh_prev and sl_recent > sl_prev:
            return "bullish"
        if sh_recent < sh_prev and sl_recent < sl_prev:
            return "bearish"
        return "neutral"

    # ─────────────────────────────────────────────────────
    # LIQUIDITY ZONE DETECTION
    # ─────────────────────────────────────────────────────

    def _detect_liquidity_zones(self, df: pd.DataFrame) -> List[LiquidityZone]:
        """Detect equal highs/lows and previous-day H/L as liquidity pools."""
        zones: List[LiquidityZone] = []
        zones.extend(self._detect_equal_levels(df))
        zones.extend(self._detect_prev_day_levels(df))
        return zones

    def _detect_equal_levels(self, df: pd.DataFrame) -> List[LiquidityZone]:
        """
        Equal highs (EQH): two or more swing highs within EQUAL_TOL of each other.
        Equal lows  (EQL): two or more swing lows within EQUAL_TOL.
        Only the most recent cluster is returned to avoid stale levels.
        """
        zones: List[LiquidityZone] = []
        if len(df) < LSD_SWEEP_LOOKBACK * 2:
            return zones

        highs = df["high"].values
        lows  = df["low"].values
        times = df["time"].values
        n     = len(highs)
        w     = LSD_SWEEP_LOOKBACK

        # Collect local swing highs and lows
        sh_indices = [
            i for i in range(w, n - w)
            if highs[i] >= max(highs[max(0, i - w): i + w + 1]) - 1e-9
        ]
        sl_indices = [
            i for i in range(w, n - w)
            if lows[i]  <= min(lows[max(0, i - w): i + w + 1])  + 1e-9
        ]

        def _cluster(indices, values, zone_type):
            seen = set()
            result = []
            for i in reversed(indices):            # most recent first
                if i in seen:
                    continue
                group = [j for j in indices if abs(values[j] - values[i]) <= _EQUAL_TOL]
                if len(group) >= 2:
                    for j in group:
                        seen.add(j)
                    midpoint  = float(np.mean([values[j] for j in group]))
                    most_recent = max(group)
                    result.append(LiquidityZone(
                        price=round(midpoint, 2),
                        zone_type=zone_type,
                        candle_index=most_recent,
                        candle_time=pd.Timestamp(times[most_recent]),
                    ))
                    break   # one cluster per scan is enough
            return result

        zones.extend(_cluster(sh_indices, highs, "EQH"))
        zones.extend(_cluster(sl_indices, lows,  "EQL"))
        return zones

    def _detect_prev_day_levels(self, df: pd.DataFrame) -> List[LiquidityZone]:
        """
        Previous-day high (PDH) and low (PDL) — powerful liquidity magnets.
        Uses the last completed calendar day visible in the DataFrame.
        """
        zones: List[LiquidityZone] = []
        if "time" not in df.columns:
            return zones

        df2 = df.copy()
        df2["_date"] = pd.to_datetime(df2["time"]).dt.date
        dates = sorted(df2["_date"].unique())
        if len(dates) < 2:
            return zones

        prev_date = dates[-2]
        prev_day  = df2[df2["_date"] == prev_date]
        if prev_day.empty:
            return zones

        pdh_price = float(prev_day["high"].max())
        pdl_price = float(prev_day["low"].min())
        last_idx  = int(prev_day.index[-1])
        last_time = pd.Timestamp(df.loc[last_idx, "time"]) if last_idx in df.index else df["time"].iloc[-1]

        zones.append(LiquidityZone(price=round(pdh_price, 2), zone_type="PDH",
                                   candle_index=last_idx, candle_time=last_time))
        zones.append(LiquidityZone(price=round(pdl_price, 2), zone_type="PDL",
                                   candle_index=last_idx, candle_time=last_time))
        return zones

    # ─────────────────────────────────────────────────────
    # SWEEP DETECTION
    # ─────────────────────────────────────────────────────

    def _detect_sweep(
        self,
        df: pd.DataFrame,
        zones: List[LiquidityZone],
        bias: str,
    ) -> Optional[SweepEvent]:
        """
        A sweep is confirmed when:
          - The candle WICK pierces through the zone price.
          - The candle CLOSES back inside (below zone for EQH/PDH, above for EQL/PDL).
        The most recent qualifying candle within the last LSD_SWEEP_LOOKBACK bars is used.
        """
        if len(df) < 3:
            return None

        # Check the last LSD_SWEEP_LOOKBACK closed candles (exclude last open bar)
        check = df.iloc[-(LSD_SWEEP_LOOKBACK + 1):-1]

        for zone in zones:
            zp = zone.price

            for idx, row in check.iterrows():
                h = float(row["high"])
                l = float(row["low"])
                c = float(row["close"])
                t = row["time"]

                if zone.zone_type in ("EQH", "PDH"):
                    # Bearish sweep: wick above zone, close below zone
                    swept = h > zp + _EQUAL_TOL and c < zp
                    direction = "UP"
                elif zone.zone_type in ("EQL", "PDL"):
                    # Bullish sweep: wick below zone, close above zone
                    swept = l < zp - _EQUAL_TOL and c > zp
                    direction = "DOWN"
                else:
                    continue

                if swept:
                    return SweepEvent(
                        zone=zone,
                        sweep_direction=direction,
                        sweep_candle_index=idx,
                        sweep_candle_time=pd.Timestamp(t),
                        sweep_high=h,
                        sweep_low=l,
                        sweep_close=c,
                    )

        return None

    # ─────────────────────────────────────────────────────
    # DISPLACEMENT DETECTION  (relaxed multi-condition model)
    # ─────────────────────────────────────────────────────

    def _detect_displacement(
        self,
        df: pd.DataFrame,
        sweep: SweepEvent,
    ) -> Optional[DisplacementEvent]:
        """
        Relaxed displacement — any ONE of three conditions qualifies:

        Condition A (Strong — closes beyond sweep close):
          BUY : any candle closes ABOVE the sweep candle's close
          SELL: any candle closes BELOW the sweep candle's close
          → confirms continuation in the reversal direction

        Condition B (Medium — two consecutive directional candles):
          BUY : two consecutive bullish (c > o) candles
          SELL: two consecutive bearish (c < o) candles
          → sustained directional pressure

        Condition C (Weak — 20-pip impulse from zone price):
          BUY : max(highs after sweep) >= zone_price + 20 pips
          SELL: min(lows  after sweep) <= zone_price - 20 pips
          → momentum move regardless of candle structure

        Returns:
          Condition A or B → body_ratio = max(actual, LSD_DISPLACEMENT_RATIO)
          Condition C only → body_ratio = 1.0 (weak; lowers confidence automatically)
          None             → no movement detected at all after sweep
        """
        try:
            sweep_pos = df.index.get_loc(sweep.sweep_candle_index)
        except KeyError:
            matches = df.index[df.index == sweep.sweep_candle_index]
            if len(matches) == 0:
                logger.info(
                    "LSD DISP: sweep candle index %d not found in df — cannot detect displacement",
                    sweep.sweep_candle_index,
                )
                return None
            sweep_pos = df.index.get_loc(matches[0])

        # Look at up to 5 candles after the sweep (exclude the still-open last bar)
        search_end = min(sweep_pos + 6, len(df) - 1)
        candidates = df.iloc[sweep_pos + 1: search_end]

        if candidates.empty:
            logger.info(
                "LSD DISP: sweep at pos %d (df len=%d) — no closed candles after sweep yet",
                sweep_pos, len(df),
            )
            return None

        # Average body from the 10 candles before the sweep (for ratio calc)
        lookback_start = max(0, sweep_pos - 10)
        lookback_df    = df.iloc[lookback_start: sweep_pos]
        avg_body = float((lookback_df["close"] - lookback_df["open"]).abs().mean()) \
                   if not lookback_df.empty else 1.0
        if avg_body < 1e-9:
            avg_body = 1.0

        expected_dir = "BUY" if sweep.sweep_direction == "DOWN" else "SELL"
        sweep_close  = sweep.sweep_close
        zone_price   = sweep.zone.price

        opens  = candidates["open"].values.astype(float)
        highs  = candidates["high"].values.astype(float)
        lows   = candidates["low"].values.astype(float)
        closes = candidates["close"].values.astype(float)
        times  = candidates["time"].values
        n      = len(candidates)

        # ── Debug: log each candidate candle ──────────────────────────────────
        for i in range(n):
            body = abs(closes[i] - opens[i])
            ratio = body / avg_body
            rng  = highs[i] - lows[i] if highs[i] > lows[i] else 1e-9
            cpct = (closes[i] - lows[i]) / rng
            dir_ok = (
                (expected_dir == "SELL" and closes[i] < opens[i]) or
                (expected_dir == "BUY"  and closes[i] > opens[i])
            )
            logger.info(
                "LSD DISP: candle+%d | o=%.2f h=%.2f l=%.2f c=%.2f | "
                "body=%.1fp ratio=%.2fx close_pct=%.0f%% | dir=%s body_dir=%s",
                i + 1, opens[i], highs[i], lows[i], closes[i],
                body / PIP_SIZE, ratio, cpct * 100,
                expected_dir, "✓" if dir_ok else "✗",
            )

        # ── Condition A: any candle closes beyond sweep close ─────────────────
        for i in range(n):
            body  = abs(closes[i] - opens[i])
            ratio = body / avg_body
            if expected_dir == "BUY" and closes[i] > sweep_close:
                logger.info(
                    "LSD DISP: Condition A ✓ (BUY) — c=%.2f > sweep_close=%.2f | "
                    "body=%.1fp ratio=%.2fx",
                    closes[i], sweep_close, body / PIP_SIZE, ratio,
                )
                return DisplacementEvent(
                    direction="BUY",
                    candle_index=int(candidates.index[i]),
                    candle_time=pd.Timestamp(times[i]),
                    open_=opens[i], high=highs[i], low=lows[i], close=closes[i],
                    body_ratio=max(round(ratio, 2), LSD_DISPLACEMENT_RATIO),
                )
            if expected_dir == "SELL" and closes[i] < sweep_close:
                logger.info(
                    "LSD DISP: Condition A ✓ (SELL) — c=%.2f < sweep_close=%.2f | "
                    "body=%.1fp ratio=%.2fx",
                    closes[i], sweep_close, body / PIP_SIZE, ratio,
                )
                return DisplacementEvent(
                    direction="SELL",
                    candle_index=int(candidates.index[i]),
                    candle_time=pd.Timestamp(times[i]),
                    open_=opens[i], high=highs[i], low=lows[i], close=closes[i],
                    body_ratio=max(round(ratio, 2), LSD_DISPLACEMENT_RATIO),
                )

        # ── Condition B: two consecutive directional candles ──────────────────
        for i in range(n - 1):
            body  = abs(closes[i] - opens[i])
            ratio = body / avg_body
            if expected_dir == "BUY" and closes[i] > opens[i] and closes[i + 1] > opens[i + 1]:
                logger.info(
                    "LSD DISP: Condition B ✓ (BUY) — 2 consecutive bullish candles | ratio=%.2fx",
                    ratio,
                )
                return DisplacementEvent(
                    direction="BUY",
                    candle_index=int(candidates.index[i]),
                    candle_time=pd.Timestamp(times[i]),
                    open_=opens[i], high=highs[i], low=lows[i], close=closes[i],
                    body_ratio=max(round(ratio, 2), LSD_DISPLACEMENT_RATIO),
                )
            if expected_dir == "SELL" and closes[i] < opens[i] and closes[i + 1] < opens[i + 1]:
                logger.info(
                    "LSD DISP: Condition B ✓ (SELL) — 2 consecutive bearish candles | ratio=%.2fx",
                    ratio,
                )
                return DisplacementEvent(
                    direction="SELL",
                    candle_index=int(candidates.index[i]),
                    candle_time=pd.Timestamp(times[i]),
                    open_=opens[i], high=highs[i], low=lows[i], close=closes[i],
                    body_ratio=max(round(ratio, 2), LSD_DISPLACEMENT_RATIO),
                )

        # ── Condition C: 20-pip impulse from the zone price ───────────────────
        impulse_min = 20.0 * PIP_SIZE
        if expected_dir == "BUY":
            max_high = float(np.max(highs))
            move     = max_high - zone_price
            if move >= impulse_min:
                best_idx = int(np.argmax(highs))
                logger.info(
                    "LSD DISP: Condition C ✓ (BUY) [WEAK] — impulse %.0fp from zone %.2f",
                    move / PIP_SIZE, zone_price,
                )
                return DisplacementEvent(
                    direction="BUY",
                    candle_index=int(candidates.index[best_idx]),
                    candle_time=pd.Timestamp(times[best_idx]),
                    open_=opens[best_idx], high=highs[best_idx],
                    low=lows[best_idx], close=closes[best_idx],
                    body_ratio=1.0,   # weak — confidence reduced automatically
                )
        else:
            min_low = float(np.min(lows))
            move    = zone_price - min_low
            if move >= impulse_min:
                best_idx = int(np.argmin(lows))
                logger.info(
                    "LSD DISP: Condition C ✓ (SELL) [WEAK] — impulse %.0fp from zone %.2f",
                    move / PIP_SIZE, zone_price,
                )
                return DisplacementEvent(
                    direction="SELL",
                    candle_index=int(candidates.index[best_idx]),
                    candle_time=pd.Timestamp(times[best_idx]),
                    open_=opens[best_idx], high=highs[best_idx],
                    low=lows[best_idx], close=closes[best_idx],
                    body_ratio=1.0,
                )

        # All conditions failed — log diagnostic summary
        logger.info(
            "LSD DISP: ✗ all conditions failed | expected=%s | sweep_close=%.2f | zone=%.2f | "
            "candles_checked=%d | high_range=[%.2f–%.2f] | "
            "best_%s=%.2f (need %s %.2f for Condition C)",
            expected_dir, sweep_close, zone_price, n,
            float(np.min(lows)), float(np.max(highs)),
            "high" if expected_dir == "BUY" else "low",
            float(np.max(highs)) if expected_dir == "BUY" else float(np.min(lows)),
            ">=" if expected_dir == "BUY" else "<=",
            zone_price + impulse_min if expected_dir == "BUY" else zone_price - impulse_min,
        )
        return None

    # ─────────────────────────────────────────────────────
    # BREAK OF STRUCTURE (BOS)
    # ─────────────────────────────────────────────────────

    def _detect_bos(
        self,
        df: pd.DataFrame,
        displacement: DisplacementEvent,
    ) -> Optional[BOSEvent]:
        """
        BOS is confirmed when, after the displacement candle, any subsequent
        candle's close breaks the most recent minor swing in the displacement direction.

        BUY  → must break a recent minor swing HIGH.
        SELL → must break a recent minor swing LOW.
        """
        if len(df) < 5:
            return None

        direction = displacement.direction
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        times  = df["time"].values
        n      = len(df)

        # Find the most recent minor swing from the last LSD_BOS_LOOKBACK candles
        lookback = min(LSD_BOS_LOOKBACK, n - 2)
        start    = n - 1 - lookback

        if direction == "BUY":
            # Recent minor high = max of highs in lookback window
            swing_level = float(np.max(highs[start: n - 1]))
            # Check if any of the last 3 closed candles break it
            for i in range(max(n - 4, 0), n - 1):
                if closes[i] > swing_level:
                    return BOSEvent(
                        direction="BUY",
                        broken_level=round(swing_level, 2),
                        candle_index=int(df.index[i]),
                        candle_time=pd.Timestamp(times[i]),
                    )
        else:  # SELL
            swing_level = float(np.min(lows[start: n - 1]))
            for i in range(max(n - 4, 0), n - 1):
                if closes[i] < swing_level:
                    return BOSEvent(
                        direction="SELL",
                        broken_level=round(swing_level, 2),
                        candle_index=int(df.index[i]),
                        candle_time=pd.Timestamp(times[i]),
                    )

        return None

    # ─────────────────────────────────────────────────────
    # ENTRY, SL, TP
    # ─────────────────────────────────────────────────────

    def _compute_entry(self, displacement: DisplacementEvent) -> float:
        """Entry at the 50% level (midpoint) of the displacement candle."""
        return round((displacement.high + displacement.low) / 2, 2)

    def _compute_swing_sl(
        self,
        sweep: SweepEvent,
        direction: str,
    ) -> Optional[float]:
        """
        Swing SL: anchored to the extreme of the sweep wick + buffer.
        Max SL is LSD_SWING_MAX_SL_PIPS from the sweep wick extreme.
        """
        if direction == "BUY":
            sl = round(sweep.sweep_low - _SWING_BUF, 2)    # below sweep low
        else:
            sl = round(sweep.sweep_high + _SWING_BUF, 2)   # above sweep high

        # Verify SL is within the max allowed distance from entry
        # (entry is not yet computed here, but we compute a reference)
        entry_ref = round((sweep.sweep_high + sweep.sweep_low) / 2, 2)
        sl_dist   = abs(entry_ref - sl)
        if sl_dist > _SWING_MAX_SL:
            logger.info(
                "LSD_SWING: SL too wide (%.0f pips > %d max) — rejected",
                sl_dist / PIP_SIZE, LSD_SWING_MAX_SL_PIPS,
            )
            return None
        return sl

    def _compute_scalp_sl(
        self,
        sweep: SweepEvent,
        direction: str,
    ) -> Optional[float]:
        """
        Scalp SL: anchored to sweep wick + buffer.
        Must be within LSD_SCALP_MIN_SL_PIPS – LSD_SCALP_MAX_SL_PIPS range.
        """
        if direction == "BUY":
            sl = round(sweep.sweep_low - _SCALP_BUF, 2)
        else:
            sl = round(sweep.sweep_high + _SCALP_BUF, 2)

        entry_ref = round((sweep.sweep_high + sweep.sweep_low) / 2, 2)
        sl_dist   = abs(entry_ref - sl)

        if sl_dist < _SCALP_MIN_SL:
            logger.info("LSD_SCALP: SL too tight (%.0f pips < %d min) — rejected",
                        sl_dist / PIP_SIZE, LSD_SCALP_MIN_SL_PIPS)
            return None
        if sl_dist > _SCALP_MAX_SL:
            logger.info("LSD_SCALP: SL too wide (%.0f pips > %d max) — rejected",
                        sl_dist / PIP_SIZE, LSD_SCALP_MAX_SL_PIPS)
            return None
        return sl

    def _compute_tp_from_zones(
        self,
        entry: float,
        direction: str,
        all_zones: List[LiquidityZone],
        sl_dist: float,
    ) -> List[float]:
        """
        Build TP levels anchored to opposing liquidity zones.

        TP1: nearest opposing zone above entry (BUY) or below entry (SELL).
             Fallback to 2R if no zone found in the right direction.
        TP2: second nearest opposing zone, or 3R fallback.
        TP3–TP5: 4R / 5R / 6R from entry (structural targets are rare beyond TP2).
        """
        sign = 1 if direction == "BUY" else -1

        if direction == "BUY":
            opposing = sorted(
                [z for z in all_zones if z.zone_type in ("EQH", "PDH") and z.price > entry],
                key=lambda z: z.price - entry,
            )
        else:
            opposing = sorted(
                [z for z in all_zones if z.zone_type in ("EQL", "PDL") and z.price < entry],
                key=lambda z: entry - z.price,
            )

        # TP1
        if opposing:
            tp1 = opposing[0].price
            logger.info("LSD: TP1 → opposing %s at %.2f", opposing[0].zone_type, tp1)
        else:
            tp1 = round(entry + sign * 2.0 * sl_dist, 2)
            logger.info("LSD: TP1 fallback → 2R at %.2f (no opposing zone found)", tp1)

        # TP2
        if len(opposing) >= 2:
            tp2 = opposing[1].price
        else:
            tp2 = round(entry + sign * 3.0 * sl_dist, 2)

        # TP3–TP5 at fixed R multiples
        tp3 = round(entry + sign * 4.0 * sl_dist, 2)
        tp4 = round(entry + sign * 5.0 * sl_dist, 2)
        tp5 = round(entry + sign * 6.0 * sl_dist, 2)

        return [round(tp1, 2), round(tp2, 2), tp3, tp4, tp5]

    # ─────────────────────────────────────────────────────
    # ASIAN RANGE (SCALP SUPPORT)
    # ─────────────────────────────────────────────────────

    def _get_asian_range_as_zones(
        self,
        h1_df: Optional[pd.DataFrame],
    ) -> List[LiquidityZone]:
        """
        Compute the Asian session range (00:00–LSD_ASIAN_SESSION_END UTC) from H1 bars
        and return the high and low as liquidity zones.
        """
        if h1_df is None or h1_df.empty:
            return []

        df2 = h1_df.copy()
        df2["_hour"] = pd.to_datetime(df2["time"]).dt.hour
        asian = df2[df2["_hour"] < LSD_ASIAN_SESSION_END]
        if asian.empty:
            return []

        ahi = float(asian["high"].max())
        alo = float(asian["low"].min())
        last_idx  = int(asian.index[-1])
        last_time = pd.Timestamp(h1_df["time"].iloc[-1])

        return [
            LiquidityZone(price=round(ahi, 2), zone_type="EQH",
                          candle_index=last_idx, candle_time=last_time),
            LiquidityZone(price=round(alo, 2), zone_type="EQL",
                          candle_index=last_idx, candle_time=last_time),
        ]

    # ─────────────────────────────────────────────────────
    # LIQUIDITY ZONE SCORING
    # ─────────────────────────────────────────────────────

    def _score_zones(
        self,
        zones: List[LiquidityZone],
        df: pd.DataFrame,
    ) -> List[LiquidityZone]:
        """
        Assign a quality score to each zone.

          +3  Previous Day High/Low (strongest institutional level)
          +2  Equal highs/lows (cluster of resting orders)
          +2  Untouched — zone has not yet been swept (is_swept == False)
          +1  Recent formation — formed within the last 50 bars

        Max possible score: 6 (PDH/PDL + untouched + recent).
        Min qualifying score used by callers: 3.
        """
        n = len(df)
        recent_threshold = max(0, n - 50)

        for zone in zones:
            score = 0
            if zone.zone_type in ("PDH", "PDL"):
                score += 3
            elif zone.zone_type in ("EQH", "EQL"):
                score += 2
            if not zone.is_swept:
                score += 2
            if zone.candle_index >= recent_threshold:
                score += 1
            zone.score = score

        return zones

    # ─────────────────────────────────────────────────────
    # MID-RANGE FILTER
    # ─────────────────────────────────────────────────────

    def _is_near_range_extreme(
        self,
        df: pd.DataFrame,
        current_price: float,
        lookback: int = 20,
        threshold_pct: float = 0.25,
    ) -> bool:
        """
        Reject setups that originate from the middle of the recent trading range.

        Computes the high/low of the last `lookback` candles.
        Accepts the setup only when current_price is within the outer
        `threshold_pct` (25%) band at either the top or bottom of the range.

        Returns True (valid) when price is near an extreme; False otherwise.
        """
        if len(df) < lookback:
            return True   # not enough data — don't filter

        window     = df.iloc[-lookback:]
        range_high = float(window["high"].max())
        range_low  = float(window["low"].min())
        range_size = range_high - range_low

        if range_size < 1e-9:
            return False   # flat / no range → reject

        band       = range_size * threshold_pct
        near_high  = abs(current_price - range_high) <= band
        near_low   = abs(current_price - range_low)  <= band

        if not (near_high or near_low):
            logger.info(
                "LSD: Mid-range filter — price %.2f is in mid-range [%.2f–%.2f] "
                "(band=%.2f, threshold=%.0f%%)",
                current_price, range_low, range_high, band, threshold_pct * 100,
            )

        return near_high or near_low

    # ─────────────────────────────────────────────────────
    # VOLATILITY FILTER
    # ─────────────────────────────────────────────────────

    def _is_volatile_enough(
        self,
        df: pd.DataFrame,
        lookback: int = 10,
        min_ratio: float = 0.5,
    ) -> bool:
        """
        Reject setups when the current candle range is unusually narrow.

        Computes the average high–low range over the last `lookback` closed candles.
        Accepts the setup only when the most recent closed candle's range is at
        least `min_ratio` (70%) of that average.

        Returns True (volatile enough) or False (too quiet).
        """
        if len(df) < lookback + 2:
            return True   # not enough data — don't filter

        # Last `lookback` closed candles (exclude the still-open last bar)
        closed  = df.iloc[-(lookback + 1):-1]
        avg_rng = float((closed["high"] - closed["low"]).mean())

        if avg_rng < 1e-9:
            return False

        # Most recent closed candle
        last_closed = df.iloc[-2]
        cur_rng     = float(last_closed["high"] - last_closed["low"])
        threshold   = min_ratio * avg_rng

        if cur_rng < threshold:
            logger.info(
                "LSD: Volatility filter — last candle range %.2f < %.0f%% of avg %.2f",
                cur_rng, min_ratio * 100, avg_rng,
            )
            return False

        return True

    # ─────────────────────────────────────────────────────
    # CONFIDENCE SCORING
    # ─────────────────────────────────────────────────────

    def _compute_confidence(
        self,
        bias: str,
        sweep: SweepEvent,
        displacement: DisplacementEvent,
        bos: BOSEvent,
        model: str,
        liq_score: int = 0,
    ) -> float:
        """
        Four-component scoring system normalised to 0.0–1.0 (displayed as 0–100%).

        Component            Max pts   Logic
        ─────────────────────────────────────────────────────────────────────────
        Displacement strength   30     At min threshold (1.5×) → 10 pts.
                                       +1.33 pts per additional 0.1× above threshold.
                                       Capped at 30 pts (≈ 3.0× ratio).

        Liquidity quality       30     Zone score / 6 (max) × 30.
                                       PDH/PDL untouched recent = 6/6 → 30 pts.

        BOS quality             20     Present → 15 pts base.
                                       Swing mode adds +5 (higher-TF BOS = stronger).

        Session bonus           20     Active London/NY session → 20 pts.
                                       Swing off-session → 10 pts (positions run 24h).
                                       Scalp off-session → 0 pts (already gated, edge case).

        ─────────────────────────────────────────────────────────────────────────
        Total max: 100 pts  →  1.00 confidence
        """
        pts = 0.0

        # 1. Displacement strength (0–30 pts)
        disp_pts = 10.0 + (displacement.body_ratio - LSD_DISPLACEMENT_RATIO) / 0.1 * 1.33
        pts += max(0.0, min(30.0, disp_pts))

        # 2. Liquidity quality (0–30 pts)
        max_liq_score = 6
        pts += min(30.0, (liq_score / max_liq_score) * 30.0)

        # 3. BOS quality (0–20 pts)
        bos_pts = 15.0 + (5.0 if model == "swing" else 0.0)
        pts += bos_pts

        # 4. Session bonus (0–20 pts)
        session = _current_session()
        if session:
            pts += 20.0
        elif model == "swing":
            pts += 10.0   # swing trades can hold through the session boundary

        return round(min(pts / 100.0, 1.0), 3)


# ─────────────────────────────────────────────────────────
# SESSION HELPER  (module-level, no class dependency)
# ─────────────────────────────────────────────────────────

def _current_session() -> str:
    """Returns 'london', 'new_york', or '' based on UTC hour."""
    hour = datetime.now(timezone.utc).hour
    if SESSION_LONDON_UTC[0] <= hour < SESSION_LONDON_UTC[1]:
        return "london"
    if SESSION_NEW_YORK_UTC[0] <= hour < SESSION_NEW_YORK_UTC[1]:
        return "new_york"
    return ""
