"""
AlphaPulse - Entry Confirmation Engine
=======================================
Detects high-quality rejection signals at key structural levels.

Confirmation types supported
─────────────────────────────
  rejection              Classic wick-rejection candle (original model)
  liquidity_sweep_reclaim  Prior candle sweeps beyond level, current bar reclaims
  engulfing_reversal     Current bearish/bullish body fully engulfs prior candle body
  double_pattern         Two distinct touches of the same level in the lookback window

For every type the output is the same:
  entry_price = level.price   (limit order at the level for next revisit)
  sl_price    = above/below the pattern high/low + tolerance
  confirmation_type string is stored on ConfirmationResult and flows through to Trade

SELL setup (resistance / A-Level / Gap):
  1. Candle high pierces INTO the level zone
  2. Candle closes below the level (rejection confirmed)
  3. Upper wick ≥ MIN_WICK_BODY_RATIO × body
  4. Body ≥ MIN_CANDLE_BODY_PIPS

BUY setup mirrors the above for support / V-Level / Gap.

Multi-bar patterns require two consecutive closed candles — the lookback window
is extended by one bar automatically in check_confirmations().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import pandas as pd

from strategies.level_detector import LevelInfo
from config.settings import (
    LEVEL_TOLERANCE_PIPS, PIP_SIZE,
    MAX_SL_PIPS, MIN_SL_PIPS,
    MIN_WICK_BODY_RATIO, MIN_CANDLE_BODY_PIPS,
)
from utils.helpers import upper_wick, lower_wick, candle_body
from utils.logger import get_logger

logger = get_logger(__name__)

# Pre-compute price values once
_TOL            = LEVEL_TOLERANCE_PIPS * PIP_SIZE
_MIN_SL_PRICE   = MIN_SL_PIPS * PIP_SIZE
_MAX_SL_PRICE   = MAX_SL_PIPS * PIP_SIZE
_MIN_BODY_PRICE = MIN_CANDLE_BODY_PIPS * PIP_SIZE


@dataclass
class ConfirmationResult:
    confirmed: bool
    direction: str              # "BUY" | "SELL"
    level: Optional[LevelInfo]
    candle_index: int
    candle_time: pd.Timestamp
    entry_price: float          # limit-order entry at the level
    sl_price: float
    candle_high: float
    candle_low: float
    candle_close: float
    wick_ratio: float = 0.0
    sl_pips: float = 0.0
    note: str = ""
    confirmation_type: str = "rejection"
    # "rejection" | "liquidity_sweep_reclaim" | "double_pattern" | "engulfing_reversal"


@dataclass
class ConfirmationRejection:
    direction: str
    level: LevelInfo
    timeframe: str
    reason: str
    candle_time: pd.Timestamp
    candle_index: int
    close_price: float


class ConfirmationEngine:
    """
    Scans the last `lookback` closed candles for confirmation signals at each level.
    Checks four pattern types per (candle, level) pair.
    Logs every rejection that fires after the candle has pierced the level zone.
    """

    def __init__(self):
        self.last_rejections: list[ConfirmationRejection] = []

    def _reject(
        self,
        direction: str,
        level: LevelInfo,
        timeframe: str,
        reason: str,
        candle_time: pd.Timestamp,
        candle_index: int,
        close_price: float,
    ):
        logger.info(
            "SETUP REJECTED: %s XAUUSD | Reason: %s | Level %s %.2f | [%s]",
            direction, reason, level.level_type, level.price, timeframe,
        )
        self.last_rejections.append(
            ConfirmationRejection(
                direction=direction,
                level=level,
                timeframe=timeframe,
                reason=reason,
                candle_time=candle_time,
                candle_index=candle_index,
                close_price=close_price,
            )
        )

    def check_confirmations(
        self,
        df: pd.DataFrame,
        levels: list[LevelInfo],
        timeframe: str,
        lookback: int = 1,
    ) -> list[ConfirmationResult]:
        """
        Check the last `lookback` closed candles for confirmation signals.

        The window is widened by one extra bar so that all multi-bar patterns
        (sweep+reclaim, engulfing) have access to the bar preceding them.

        Returns at most one result per (candle, direction) combination.
        """
        if df is None or len(df) < 3 or not levels:
            return []

        self.last_rejections = []
        results: list[ConfirmationResult] = []
        seen: set = set()

        # One extra bar beyond the lookback window supplies the "previous" candle
        # for two-bar patterns; the final bar is excluded (still open).
        window = df.iloc[-(lookback + 2):-1]

        rows = list(window.iterrows())

        for i, (idx, row) in enumerate(rows):
            o = float(row["open"]); h = float(row["high"])
            l = float(row["low"]);  c = float(row["close"])
            t = row["time"]

            prev = rows[i - 1][1] if i > 0 else None

            for level in levels:

                # ── 1. Standard rejection (single-candle) ─────────────
                result = self._check_sell(o, h, l, c, t, idx, level, timeframe)
                if result and result.confirmed:
                    key = (idx, "SELL")
                    if key not in seen:
                        seen.add(key)
                        results.append(result)
                    continue

                result = self._check_buy(o, h, l, c, t, idx, level, timeframe)
                if result and result.confirmed:
                    key = (idx, "BUY")
                    if key not in seen:
                        seen.add(key)
                        results.append(result)
                    continue

                if prev is None:
                    continue

                # ── 2. Liquidity sweep + reclaim (two-bar) ────────────
                result = self._check_sweep_reclaim(prev, row, idx, level, timeframe)
                if result and result.confirmed:
                    key = (idx, result.direction)
                    if key not in seen:
                        seen.add(key)
                        results.append(result)
                    continue

                # ── 3. Engulfing reversal (two-bar) ───────────────────
                result = self._check_engulfing(prev, row, idx, level, timeframe)
                if result and result.confirmed:
                    key = (idx, result.direction)
                    if key not in seen:
                        seen.add(key)
                        results.append(result)

        # ── 4. Double-pattern upgrade (post-process) ──────────────────
        self._upgrade_double_patterns(results, window)

        return results

    # ─────────────────────────────────────────────────────
    # PATTERN 1: STANDARD REJECTION — SELL
    # ─────────────────────────────────────────────────────

    def _check_sell(
        self,
        o: float, h: float, l: float, c: float,
        t: pd.Timestamp, idx: int,
        level: LevelInfo,
        timeframe: str,
    ) -> Optional[ConfirmationResult]:
        if level.level_type not in ("A", "Gap"):
            return None
        if level.level_type == "Gap" and getattr(level, "trade_direction", "SELL") != "SELL":
            return None

        lp = level.price

        if h < lp - _TOL:
            return None  # candle never reached the level — silent skip

        if c <= o:
            self._reject("SELL", level, timeframe,
                         f"confirmation candle not bullish (open {o:.2f}, close {c:.2f})", t, idx, c)
            return None

        if c >= lp:
            self._reject("SELL", level, timeframe,
                         f"closed inside/above level (close {c:.2f} >= {lp:.2f})", t, idx, c)
            return None

        body = candle_body(o, c)
        if body < _MIN_BODY_PRICE:
            self._reject("SELL", level, timeframe,
                         f"weak body / doji ({body / PIP_SIZE:.1f}p < {MIN_CANDLE_BODY_PIPS}p min)", t, idx, c)
            return None

        uw    = upper_wick(o, h, c)
        ratio = uw / body if body > 0 else 0.0
        if ratio < MIN_WICK_BODY_RATIO:
            self._reject("SELL", level, timeframe,
                         f"weak wick ({ratio:.2f}x < {MIN_WICK_BODY_RATIO}x required)", t, idx, c)
            return None

        raw_sl  = h + _TOL
        sl      = round(max(raw_sl, lp + _MIN_SL_PRICE), 2)
        sl_dist = sl - lp

        if sl_dist > _MAX_SL_PRICE:
            self._reject("SELL", level, timeframe,
                         f"SL too wide ({sl_dist / PIP_SIZE:.1f}p > {MAX_SL_PIPS}p max)", t, idx, c)
            return None

        sl_pips = sl_dist / PIP_SIZE
        logger.info("SELL CONFIRMED | [%s] %s level %.2f | wick %.1fx | SL %.2f (%dp) | %s",
                    timeframe, level.level_type, lp, ratio, sl, int(sl_pips), t)
        level.confirmed = True

        return ConfirmationResult(
            confirmed=True, direction="SELL", level=level,
            candle_index=idx, candle_time=t,
            entry_price=lp, sl_price=sl,
            candle_high=h, candle_low=l, candle_close=c,
            wick_ratio=round(ratio, 2), sl_pips=round(sl_pips, 1),
            note=f"SELL rejection @ {level.level_type} {lp:.2f} | {sl_pips:.0f}p SL | wick {ratio:.1f}x",
            confirmation_type="rejection",
        )

    # ─────────────────────────────────────────────────────
    # PATTERN 1: STANDARD REJECTION — BUY
    # ─────────────────────────────────────────────────────

    def _check_buy(
        self,
        o: float, h: float, l: float, c: float,
        t: pd.Timestamp, idx: int,
        level: LevelInfo,
        timeframe: str,
    ) -> Optional[ConfirmationResult]:
        if level.level_type not in ("V", "Gap"):
            return None
        if level.level_type == "Gap" and getattr(level, "trade_direction", "BUY") != "BUY":
            return None

        lp = level.price

        if l > lp + _TOL:
            return None  # candle never reached the level — silent skip

        if c >= o:
            self._reject("BUY", level, timeframe,
                         f"confirmation candle not bearish (open {o:.2f}, close {c:.2f})", t, idx, c)
            return None

        if c <= lp:
            self._reject("BUY", level, timeframe,
                         f"closed inside/below level (close {c:.2f} <= {lp:.2f})", t, idx, c)
            return None

        body = candle_body(o, c)
        if body < _MIN_BODY_PRICE:
            self._reject("BUY", level, timeframe,
                         f"weak body / doji ({body / PIP_SIZE:.1f}p < {MIN_CANDLE_BODY_PIPS}p min)", t, idx, c)
            return None

        lw    = lower_wick(o, l, c)
        ratio = lw / body if body > 0 else 0.0
        if ratio < MIN_WICK_BODY_RATIO:
            self._reject("BUY", level, timeframe,
                         f"weak wick ({ratio:.2f}x < {MIN_WICK_BODY_RATIO}x required)", t, idx, c)
            return None

        raw_sl  = l - _TOL
        sl      = round(min(raw_sl, lp - _MIN_SL_PRICE), 2)
        sl_dist = lp - sl

        if sl_dist > _MAX_SL_PRICE:
            self._reject("BUY", level, timeframe,
                         f"SL too wide ({sl_dist / PIP_SIZE:.1f}p > {MAX_SL_PIPS}p max)", t, idx, c)
            return None

        sl_pips = sl_dist / PIP_SIZE
        logger.info("BUY CONFIRMED | [%s] %s level %.2f | wick %.1fx | SL %.2f (%dp) | %s",
                    timeframe, level.level_type, lp, ratio, sl, int(sl_pips), t)
        level.confirmed = True

        return ConfirmationResult(
            confirmed=True, direction="BUY", level=level,
            candle_index=idx, candle_time=t,
            entry_price=lp, sl_price=sl,
            candle_high=h, candle_low=l, candle_close=c,
            wick_ratio=round(ratio, 2), sl_pips=round(sl_pips, 1),
            note=f"BUY rejection @ {level.level_type} {lp:.2f} | {sl_pips:.0f}p SL | wick {ratio:.1f}x",
            confirmation_type="rejection",
        )

    # ─────────────────────────────────────────────────────
    # PATTERN 2: LIQUIDITY SWEEP + RECLAIM
    # ─────────────────────────────────────────────────────

    def _check_sweep_reclaim(
        self,
        prev: pd.Series,
        curr: pd.Series,
        idx: int,
        level: LevelInfo,
        timeframe: str,
    ) -> Optional[ConfirmationResult]:
        """
        Two-bar pattern:
          SELL — prev sweeps above level (close >= level), curr reclaims below (close < level)
          BUY  — prev sweeps below level (close <= level), curr reclaims above (close > level)
        Entry: level.price (limit for next revisit)
        SL: above/below the sweep high/low + tolerance
        """
        lp   = level.price
        ph   = float(prev["high"]); pl = float(prev["low"])
        pc   = float(prev["close"]); po = float(prev["open"])
        ch   = float(curr["high"]); cl = float(curr["low"])
        cc   = float(curr["close"]); t  = curr["time"]

        # ── SELL sweep: A-level or Gap-SELL ───────────────────────────
        if level.level_type in ("A", "Gap") and \
                not (level.level_type == "Gap" and getattr(level, "trade_direction", "SELL") != "SELL"):

            # prev swept above level (high reached zone, closed at or above)
            if ph >= lp - _TOL and pc >= lp - _TOL:
                # curr reclaims below level
                if cc < lp and ch >= lp - _TOL:
                    pattern_high = max(ph, ch)
                    raw_sl  = pattern_high + _TOL
                    sl      = round(max(raw_sl, lp + _MIN_SL_PRICE), 2)
                    sl_dist = sl - lp
                    if sl_dist <= _MAX_SL_PRICE:
                        sl_pips = sl_dist / PIP_SIZE
                        logger.info(
                            "SELL SWEEP+RECLAIM | [%s] %s %.2f | SL %.2f (%dp) | %s",
                            timeframe, level.level_type, lp, sl, int(sl_pips), t,
                        )
                        level.confirmed = True
                        return ConfirmationResult(
                            confirmed=True, direction="SELL", level=level,
                            candle_index=idx, candle_time=t,
                            entry_price=lp, sl_price=sl,
                            candle_high=ch, candle_low=cl, candle_close=cc,
                            wick_ratio=0.0, sl_pips=round(sl_pips, 1),
                            note=f"SELL sweep+reclaim @ {lp:.2f} | sweep high {ph:.2f} | {sl_pips:.0f}p SL",
                            confirmation_type="liquidity_sweep_reclaim",
                        )

        # ── BUY sweep: V-level or Gap-BUY ─────────────────────────────
        if level.level_type in ("V", "Gap") and \
                not (level.level_type == "Gap" and getattr(level, "trade_direction", "BUY") != "BUY"):

            # prev swept below level (low reached zone, closed at or below)
            if pl <= lp + _TOL and pc <= lp + _TOL:
                # curr reclaims above level
                if cc > lp and cl <= lp + _TOL:
                    pattern_low = min(pl, cl)
                    raw_sl  = pattern_low - _TOL
                    sl      = round(min(raw_sl, lp - _MIN_SL_PRICE), 2)
                    sl_dist = lp - sl
                    if sl_dist <= _MAX_SL_PRICE:
                        sl_pips = sl_dist / PIP_SIZE
                        logger.info(
                            "BUY SWEEP+RECLAIM | [%s] %s %.2f | SL %.2f (%dp) | %s",
                            timeframe, level.level_type, lp, sl, int(sl_pips), t,
                        )
                        level.confirmed = True
                        return ConfirmationResult(
                            confirmed=True, direction="BUY", level=level,
                            candle_index=idx, candle_time=t,
                            entry_price=lp, sl_price=sl,
                            candle_high=ch, candle_low=cl, candle_close=cc,
                            wick_ratio=0.0, sl_pips=round(sl_pips, 1),
                            note=f"BUY sweep+reclaim @ {lp:.2f} | sweep low {pl:.2f} | {sl_pips:.0f}p SL",
                            confirmation_type="liquidity_sweep_reclaim",
                        )

        return None

    # ─────────────────────────────────────────────────────
    # PATTERN 3: ENGULFING REVERSAL
    # ─────────────────────────────────────────────────────

    def _check_engulfing(
        self,
        prev: pd.Series,
        curr: pd.Series,
        idx: int,
        level: LevelInfo,
        timeframe: str,
    ) -> Optional[ConfirmationResult]:
        """
        Two-bar pattern:
          SELL — prev is bullish & touched level zone but closed below;
                 curr is bearish and fully engulfs prev body (open >= prev.close, close <= prev.open)
                 AND curr touches the level zone.
          BUY  — mirror for V-level.
        """
        lp   = level.price
        ph   = float(prev["high"]); pl = float(prev["low"])
        pc   = float(prev["close"]); po = float(prev["open"])
        ch   = float(curr["high"]); cl = float(curr["low"])
        cc   = float(curr["close"]); co = float(curr["open"]); t = curr["time"]

        # ── SELL engulfing at A-level ──────────────────────────────────
        if level.level_type in ("A", "Gap") and \
                not (level.level_type == "Gap" and getattr(level, "trade_direction", "SELL") != "SELL"):

            prev_bullish  = pc > po
            prev_touched  = ph >= lp - _TOL
            prev_below    = pc < lp           # prev didn't close above level
            curr_bearish  = cc < co
            curr_touched  = ch >= lp - _TOL
            body_engulf   = co >= pc and cc <= po  # curr body wraps prev body

            if prev_bullish and prev_touched and prev_below and curr_bearish and curr_touched and body_engulf:
                pattern_high = max(ph, ch)
                raw_sl  = pattern_high + _TOL
                sl      = round(max(raw_sl, lp + _MIN_SL_PRICE), 2)
                sl_dist = sl - lp
                if sl_dist <= _MAX_SL_PRICE:
                    sl_pips = sl_dist / PIP_SIZE
                    logger.info(
                        "SELL ENGULFING | [%s] %s %.2f | pattern high %.2f | SL %.2f (%dp) | %s",
                        timeframe, level.level_type, lp, pattern_high, sl, int(sl_pips), t,
                    )
                    level.confirmed = True
                    return ConfirmationResult(
                        confirmed=True, direction="SELL", level=level,
                        candle_index=idx, candle_time=t,
                        entry_price=lp, sl_price=sl,
                        candle_high=ch, candle_low=cl, candle_close=cc,
                        wick_ratio=0.0, sl_pips=round(sl_pips, 1),
                        note=f"SELL engulf @ {lp:.2f} | {sl_pips:.0f}p SL",
                        confirmation_type="engulfing_reversal",
                    )

        # ── BUY engulfing at V-level ───────────────────────────────────
        if level.level_type in ("V", "Gap") and \
                not (level.level_type == "Gap" and getattr(level, "trade_direction", "BUY") != "BUY"):

            prev_bearish  = pc < po
            prev_touched  = pl <= lp + _TOL
            prev_above    = pc > lp           # prev didn't close below level
            curr_bullish  = cc > co
            curr_touched  = cl <= lp + _TOL
            body_engulf   = co <= pc and cc >= po  # curr body wraps prev body

            if prev_bearish and prev_touched and prev_above and curr_bullish and curr_touched and body_engulf:
                pattern_low = min(pl, cl)
                raw_sl  = pattern_low - _TOL
                sl      = round(min(raw_sl, lp - _MIN_SL_PRICE), 2)
                sl_dist = lp - sl
                if sl_dist <= _MAX_SL_PRICE:
                    sl_pips = sl_dist / PIP_SIZE
                    logger.info(
                        "BUY ENGULFING | [%s] %s %.2f | pattern low %.2f | SL %.2f (%dp) | %s",
                        timeframe, level.level_type, lp, pattern_low, sl, int(sl_pips), t,
                    )
                    level.confirmed = True
                    return ConfirmationResult(
                        confirmed=True, direction="BUY", level=level,
                        candle_index=idx, candle_time=t,
                        entry_price=lp, sl_price=sl,
                        candle_high=ch, candle_low=cl, candle_close=cc,
                        wick_ratio=0.0, sl_pips=round(sl_pips, 1),
                        note=f"BUY engulf @ {lp:.2f} | {sl_pips:.0f}p SL",
                        confirmation_type="engulfing_reversal",
                    )

        return None

    # ─────────────────────────────────────────────────────
    # PATTERN 4: DOUBLE-PATTERN UPGRADE (post-process)
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _upgrade_double_patterns(
        results: list[ConfirmationResult],
        window: pd.DataFrame,
    ) -> None:
        """
        For each confirmed result, check if the same level was also touched
        in any earlier bar within the lookback window. If yes, upgrade
        confirmation_type to "double_pattern" — the level has been respected twice.

        Mutates results in-place; returns nothing.
        """
        for res in results:
            if res.level is None:
                continue
            lp = res.level.price

            # Bars that closed BEFORE the confirming candle
            prior = window[window.index < res.candle_index]
            if prior.empty:
                continue

            if res.direction == "SELL":
                touched = (prior["high"] >= lp - _TOL).any()
            else:
                touched = (prior["low"] <= lp + _TOL).any()

            if touched:
                old_type = res.confirmation_type
                res.confirmation_type = "double_pattern"
                logger.info(
                    "DOUBLE PATTERN upgrade: %s @ %.2f (%s → double_pattern)",
                    res.direction, lp, old_type,
                )
