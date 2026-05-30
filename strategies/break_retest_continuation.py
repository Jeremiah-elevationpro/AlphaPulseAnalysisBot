"""Break & Retest Continuation Strategy.

BUY:
  1. Strong resistance breaks (M15 close > resistance + buffer)
  2. Broken resistance flips to support on retest
  3. Retest holds (does not deeply violate)
  4. Bullish confirmation forms
  5. SL below retest low; TP path = next resistance / liquidity

SELL: mirror.

Pulls resistance / support candidates from market_plan (key_supports,
key_resistances) and falls back to swing highs/lows in the M15 data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from analysis.sl_engine import validate_sl_direction
from analysis.tp_engine import check_risk_reward
from strategies.core_strategy_engine import (
    ALLOWED_BUY_CONFIRMATIONS,
    ALLOWED_SELL_CONFIRMATIONS,
    RejectedCandidate,
    StrategySetup,
    attach_level_intel_evidence,
)

logger = logging.getLogger(__name__)

PIP = 0.1
BREAK_BUFFER_PIPS = 5.0
RETEST_TOLERANCE_PIPS = 8.0
DEEP_VIOLATION_PIPS = 15.0       # retest closing > this far through level = invalid
BREAK_DISPLACEMENT_MIN_PIPS = 20.0
SL_BUFFER_PIPS = 3.0
LOOKBACK_BARS = 60


def _pips(a: float, b: float) -> float:
    return abs(a - b) / PIP


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _resistance_pool(plan: Any, m15: pd.DataFrame) -> list[float]:
    """Combine plan-provided resistances with simple swing-high detection."""
    pool: list[float] = []
    if isinstance(plan, dict):
        for v in (plan.get("key_resistances") or []):
            try:
                pool.append(float(v))
            except Exception:
                continue
    pool.extend(_swing_highs(m15))
    return sorted(set(round(p, 2) for p in pool), reverse=True)


def _support_pool(plan: Any, m15: pd.DataFrame) -> list[float]:
    pool: list[float] = []
    if isinstance(plan, dict):
        for v in (plan.get("key_supports") or []):
            try:
                pool.append(float(v))
            except Exception:
                continue
    pool.extend(_swing_lows(m15))
    return sorted(set(round(p, 2) for p in pool))


def _swing_highs(df: pd.DataFrame, window: int = 3) -> list[float]:
    """Quick swing-high finder over the last LOOKBACK_BARS."""
    if df is None or len(df) < 2 * window + 1:
        return []
    last = df.tail(LOOKBACK_BARS).reset_index(drop=True)
    out: list[float] = []
    highs = last["high"].astype(float).tolist()
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            out.append(float(highs[i]))
    return out


def _swing_lows(df: pd.DataFrame, window: int = 3) -> list[float]:
    if df is None or len(df) < 2 * window + 1:
        return []
    last = df.tail(LOOKBACK_BARS).reset_index(drop=True)
    out: list[float] = []
    lows = last["low"].astype(float).tolist()
    for i in range(window, len(lows) - window):
        if lows[i] == min(lows[i - window : i + window + 1]):
            out.append(float(lows[i]))
    return out


def _find_break_retest_buy(m15: pd.DataFrame, resistance: float) -> tuple[bool, str | None, float]:
    """Was `resistance` broken to the upside and now being retested as support?

    Returns (qualifies, confirmation_type, retest_low).
    """
    if m15 is None or len(m15) < 10:
        return False, None, 0.0
    bars = m15.tail(LOOKBACK_BARS).reset_index(drop=True)
    # find break index: M15 close > resistance + buffer
    break_thresh = resistance + BREAK_BUFFER_PIPS * PIP
    break_mask = bars["close"].astype(float) > break_thresh
    if not break_mask.any():
        return False, None, 0.0
    break_idx = int(break_mask[break_mask].index[0])  # first break
    break_bar = bars.iloc[break_idx]
    # break must have meaningful displacement
    if _body(break_bar) / PIP < BREAK_DISPLACEMENT_MIN_PIPS:
        return False, None, 0.0
    after_break = bars.iloc[break_idx + 1 :]
    if len(after_break) < 1:
        return False, None, 0.0
    # find retest = at least one bar low <= resistance + tolerance
    retest_mask = after_break["low"].astype(float) <= resistance + RETEST_TOLERANCE_PIPS * PIP
    if not retest_mask.any():
        return False, None, 0.0
    retest_idx = int(retest_mask[retest_mask].index[0])
    retest_bar = bars.iloc[retest_idx]
    retest_low = float(retest_bar["low"])
    # no deep violation: any close < resistance - DEEP_VIOLATION_PIPS = invalid
    deep_violation = (after_break["close"].astype(float) < resistance - DEEP_VIOLATION_PIPS * PIP).any()
    if deep_violation:
        return False, None, 0.0
    # confirmation = bullish on last bar AND last close > resistance
    last = bars.iloc[-1]
    prev = bars.iloc[-2]
    if _is_bullish(last) and float(last["close"]) > resistance + BREAK_BUFFER_PIPS * PIP:
        # Choose the most specific confirmation type that applies
        if (
            _is_bearish(prev)
            and float(last["close"]) > float(prev["open"])
            and float(last["open"]) < float(prev["close"])
        ):
            return True, "bullish_engulfing", retest_low
        if _body(last) / PIP >= BREAK_DISPLACEMENT_MIN_PIPS:
            return True, "break_retest_close_above", retest_low
        return True, "bullish_rejection", retest_low
    return False, None, 0.0


def _find_break_retest_sell(m15: pd.DataFrame, support: float) -> tuple[bool, str | None, float]:
    if m15 is None or len(m15) < 10:
        return False, None, 0.0
    bars = m15.tail(LOOKBACK_BARS).reset_index(drop=True)
    break_thresh = support - BREAK_BUFFER_PIPS * PIP
    break_mask = bars["close"].astype(float) < break_thresh
    if not break_mask.any():
        return False, None, 0.0
    break_idx = int(break_mask[break_mask].index[0])
    break_bar = bars.iloc[break_idx]
    if _body(break_bar) / PIP < BREAK_DISPLACEMENT_MIN_PIPS:
        return False, None, 0.0
    after_break = bars.iloc[break_idx + 1 :]
    if len(after_break) < 1:
        return False, None, 0.0
    retest_mask = after_break["high"].astype(float) >= support - RETEST_TOLERANCE_PIPS * PIP
    if not retest_mask.any():
        return False, None, 0.0
    retest_idx = int(retest_mask[retest_mask].index[0])
    retest_bar = bars.iloc[retest_idx]
    retest_high = float(retest_bar["high"])
    deep_violation = (after_break["close"].astype(float) > support + DEEP_VIOLATION_PIPS * PIP).any()
    if deep_violation:
        return False, None, 0.0
    last = bars.iloc[-1]
    prev = bars.iloc[-2]
    if _is_bearish(last) and float(last["close"]) < support - BREAK_BUFFER_PIPS * PIP:
        if (
            _is_bullish(prev)
            and float(last["close"]) < float(prev["open"])
            and float(last["open"]) > float(prev["close"])
        ):
            return True, "bearish_engulfing", retest_high
        if _body(last) / PIP >= BREAK_DISPLACEMENT_MIN_PIPS:
            return True, "break_retest_close_below", retest_high
        return True, "bearish_rejection", retest_high
    return False, None, 0.0


def _tp_ladder(direction: str, entry: float, sl: float, pool: list[float]) -> tuple[float, float, float]:
    risk = abs(entry - sl)
    if direction.upper() == "BUY":
        above = [p for p in pool if p > entry + 5 * PIP]
        if len(above) >= 3:
            above_sorted = sorted(above)[:3]
            return round(above_sorted[0], 2), round(above_sorted[1], 2), round(above_sorted[2], 2)
        if len(above) >= 1:
            above_sorted = sorted(above)
            tp1 = above_sorted[0]
            tp2 = above_sorted[1] if len(above_sorted) > 1 else round(entry + 2 * risk, 2)
            tp3 = round(entry + 3 * risk, 2)
            return round(tp1, 2), round(tp2, 2), round(tp3, 2)
        return round(entry + risk, 2), round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)
    # SELL
    below = [p for p in pool if p < entry - 5 * PIP]
    if len(below) >= 3:
        below_sorted = sorted(below, reverse=True)[:3]
        return round(below_sorted[0], 2), round(below_sorted[1], 2), round(below_sorted[2], 2)
    if len(below) >= 1:
        below_sorted = sorted(below, reverse=True)
        tp1 = below_sorted[0]
        tp2 = below_sorted[1] if len(below_sorted) > 1 else round(entry - 2 * risk, 2)
        tp3 = round(entry - 3 * risk, 2)
        return round(tp1, 2), round(tp2, 2), round(tp3, 2)
    return round(entry - risk, 2), round(entry - 2 * risk, 2), round(entry - 3 * risk, 2)


class BreakRetestContinuationStrategy:
    def __init__(self, level_intel_engine: Any = None) -> None:
        self.level_intel = level_intel_engine

    def scan(
        self,
        data: dict[str, pd.DataFrame],
        *,
        current_price: float | None,
        ctx: Any = None,
        plan: Any = None,
        symbol: str = "XAUUSD",
    ) -> tuple[list[StrategySetup], list[RejectedCandidate]]:
        candidates: list[StrategySetup] = []
        rejected: list[RejectedCandidate] = []
        if current_price is None:
            return candidates, rejected
        m15 = data.get("M15")
        if m15 is None or len(m15) < 20:
            return candidates, rejected

        resistances = _resistance_pool(plan, m15)
        supports = _support_pool(plan, m15)

        for r in resistances:
            # only worth checking levels reasonably close to price
            if _pips(current_price, r) > 200:
                continue
            qualifies, conf, retest_low = _find_break_retest_buy(m15, r)
            if not qualifies:
                rejected.append(self._reject("BUY", r, "no_break_retest"))
                continue
            if conf not in ALLOWED_BUY_CONFIRMATIONS:
                rejected.append(self._reject("BUY", r, f"confirmation_disabled:{conf}"))
                continue
            entry = float(m15.iloc[-1]["close"])
            sl = round(retest_low - SL_BUFFER_PIPS * PIP, 2)
            ok, why = validate_sl_direction("BUY", entry, sl)
            if not ok:
                rejected.append(self._reject("BUY", r, f"sl_invalid:{why}"))
                continue
            tp1, tp2, tp3 = _tp_ladder("BUY", entry, sl, resistances)
            rr_check = check_risk_reward("BUY", entry, sl, [tp1, tp2, tp3])
            if not rr_check.get("tp1_ok", False):
                rejected.append(self._reject("BUY", r, "rr_invalid"))
                continue
            setup = StrategySetup(
                strategy_type="break_retest_continuation",
                symbol=symbol,
                direction="BUY",
                entry_zone_low=retest_low,
                entry_zone_high=r + RETEST_TOLERANCE_PIPS * PIP,
                trigger_level=r,
                confirmation_required=[conf],
                entry=round(entry, 2),
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                invalidation=retest_low,
                reason=f"Break above resistance {r:.2f} held on retest; "
                       f"{conf} confirmation",
                confidence_internal=0.0,
                session_name=getattr(ctx, "session_name", "") if ctx else "",
                higher_tf="M15",
                lower_tf="M5",
                confirmation_candle_time=self._candle_time(m15.iloc[-1]),
            )
            attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
            candidates.append(setup)

        for s in supports:
            if _pips(current_price, s) > 200:
                continue
            qualifies, conf, retest_high = _find_break_retest_sell(m15, s)
            if not qualifies:
                rejected.append(self._reject("SELL", s, "no_break_retest"))
                continue
            if conf not in ALLOWED_SELL_CONFIRMATIONS:
                rejected.append(self._reject("SELL", s, f"confirmation_disabled:{conf}"))
                continue
            entry = float(m15.iloc[-1]["close"])
            sl = round(retest_high + SL_BUFFER_PIPS * PIP, 2)
            ok, why = validate_sl_direction("SELL", entry, sl)
            if not ok:
                rejected.append(self._reject("SELL", s, f"sl_invalid:{why}"))
                continue
            tp1, tp2, tp3 = _tp_ladder("SELL", entry, sl, supports)
            rr_check = check_risk_reward("SELL", entry, sl, [tp1, tp2, tp3])
            if not rr_check.get("tp1_ok", False):
                rejected.append(self._reject("SELL", s, "rr_invalid"))
                continue
            setup = StrategySetup(
                strategy_type="break_retest_continuation",
                symbol=symbol,
                direction="SELL",
                entry_zone_low=s - RETEST_TOLERANCE_PIPS * PIP,
                entry_zone_high=retest_high,
                trigger_level=s,
                confirmation_required=[conf],
                entry=round(entry, 2),
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                invalidation=retest_high,
                reason=f"Break below support {s:.2f} held on retest; "
                       f"{conf} confirmation",
                confidence_internal=0.0,
                session_name=getattr(ctx, "session_name", "") if ctx else "",
                higher_tf="M15",
                lower_tf="M5",
                confirmation_candle_time=self._candle_time(m15.iloc[-1]),
            )
            attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
            candidates.append(setup)

        return candidates, rejected

    @staticmethod
    def _reject(direction: str, level: float, reason: str) -> RejectedCandidate:
        return RejectedCandidate(
            strategy_type="break_retest_continuation",
            direction=direction,
            reason=reason,
            level=float(level),
        )

    @staticmethod
    def _candle_time(row: pd.Series) -> datetime:
        try:
            ts = pd.Timestamp(row["time"])
            if ts.tz is None:
                ts = ts.tz_localize("UTC")
            else:
                ts = ts.tz_convert("UTC")
            return ts.to_pydatetime()
        except Exception:
            return datetime.now(timezone.utc)
