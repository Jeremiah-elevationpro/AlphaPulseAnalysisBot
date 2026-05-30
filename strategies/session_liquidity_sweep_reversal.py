"""Session Liquidity Sweep Reversal Strategy.

Trade Gold's common pattern: price wicks through a session high/low (or PDH/PDL,
equal highs/lows), then closes back inside the range, signalling failed
breakout / trap-and-reverse.

SELL (sweep buy-side, close back below):
  1. Wick high > liquidity level (Asian H / London H / NY H / PDH / etc.)
  2. Candle closes back below the level OR strong rejection wick > body
  3. Bearish displacement / failed retest confirms
  4. SL above sweep high; TP1 mid / nearest support; TP2 sell-side liquidity

BUY: mirror.

Leans heavily on analysis.session_liquidity to identify levels + sweeps.
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
SWEEP_LOOKBACK_M15 = 12          # how many recent M15 bars to scan for sweeps
RECLAIM_MIN_BARS = 1             # at least this many bars closing back inside
TRAP_QUALITY_MIN_SCORE = 60.0    # if session_liquidity gives quality, require this
SL_BUFFER_PIPS = 5.0


def _pips(a: float, b: float) -> float:
    return abs(a - b) / PIP


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _level_pool_from_plan(plan: Any) -> list[dict[str, Any]]:
    """Read the session_liquidity block off market_plan into a flat list.

    Each entry: {label, level, kind: "buy_side" | "sell_side"}.
    """
    pool: list[dict[str, Any]] = []
    if not plan:
        return pool

    sl = (plan.get("session_liquidity") if isinstance(plan, dict) else None) or {}
    sessions = sl.get("sessions") or {}
    for sess_key, kind_map in (
        ("asia",     {"high": "buy_side", "low": "sell_side"}),
        ("london",   {"high": "buy_side", "low": "sell_side"}),
        ("new_york", {"high": "buy_side", "low": "sell_side"}),
    ):
        sess = sessions.get(sess_key) or {}
        for tag, kind in kind_map.items():
            level = sess.get(tag)
            if level is None:
                continue
            pool.append({
                "label": f"{sess_key.title()} {tag.title()}",
                "level": float(level),
                "kind":  kind,
                "session": sess_key,
            })
    for key, kind, label in (
        ("previous_day_high", "buy_side",  "Prev Day High"),
        ("previous_day_low",  "sell_side", "Prev Day Low"),
        ("current_day_high",  "buy_side",  "Curr Day High"),
        ("current_day_low",   "sell_side", "Curr Day Low"),
    ):
        if sl.get(key) is not None:
            pool.append({
                "label": label,
                "level": float(sl[key]),
                "kind":  kind,
                "session": "day",
            })
    return pool


def _detect_sweep_sell(
    m15_recent: pd.DataFrame, level: float
) -> tuple[bool, str | None, float]:
    """Returns (sweep_confirmed, confirmation_type, sweep_high)."""
    if m15_recent is None or len(m15_recent) < 3:
        return False, None, 0.0
    last = m15_recent.iloc[-1]
    prev = m15_recent.iloc[-2]

    # The "sweep bar" is one of the last few bars that pierced above `level`
    pierced = m15_recent[m15_recent["high"].astype(float) > level + 0.5 * PIP]
    if len(pierced) == 0:
        return False, None, 0.0
    sweep_bar = pierced.iloc[-1]
    sweep_high = float(sweep_bar["high"])

    close_back = float(last["close"]) < level
    # 1. close back below
    if _is_bearish(last) and close_back:
        return True, "sweep_above_close_back_below", sweep_high
    # 2. strong bearish engulfing after the sweep
    if (
        _is_bearish(last)
        and _is_bullish(prev)
        and float(last["close"]) < float(prev["open"])
        and float(last["open"]) > float(prev["close"])
        and float(prev["high"]) >= level
    ):
        return True, "bearish_engulfing", sweep_high
    # 3. rejection wick: high above level, body small, close inside
    body_size = max(_body(last), 0.01)
    wick_top = float(last["high"]) - max(float(last["close"]), float(last["open"]))
    if wick_top / body_size >= 1.5 and float(last["high"]) > level and float(last["close"]) < level:
        return True, "bearish_rejection", sweep_high
    return False, None, 0.0


def _detect_sweep_buy(
    m15_recent: pd.DataFrame, level: float
) -> tuple[bool, str | None, float]:
    if m15_recent is None or len(m15_recent) < 3:
        return False, None, 0.0
    last = m15_recent.iloc[-1]
    prev = m15_recent.iloc[-2]
    pierced = m15_recent[m15_recent["low"].astype(float) < level - 0.5 * PIP]
    if len(pierced) == 0:
        return False, None, 0.0
    sweep_bar = pierced.iloc[-1]
    sweep_low = float(sweep_bar["low"])

    close_back = float(last["close"]) > level
    if _is_bullish(last) and close_back:
        return True, "sweep_below_close_back_above", sweep_low
    if (
        _is_bullish(last)
        and _is_bearish(prev)
        and float(last["close"]) > float(prev["open"])
        and float(last["open"]) < float(prev["close"])
        and float(prev["low"]) <= level
    ):
        return True, "bullish_engulfing", sweep_low
    body_size = max(_body(last), 0.01)
    wick_bot = min(float(last["close"]), float(last["open"])) - float(last["low"])
    if wick_bot / body_size >= 1.5 and float(last["low"]) < level and float(last["close"]) > level:
        return True, "bullish_rejection", sweep_low
    return False, None, 0.0


def _build_tp_ladder(
    direction: str, entry: float, sl: float, level_pool: list[dict[str, Any]]
) -> tuple[float, float, float]:
    """TP1 = nearest opposite-side level, then progressively further.
    Fallback to fixed-R ladder if no opposite levels available.
    """
    risk = abs(entry - sl)
    if direction.upper() == "SELL":
        below = sorted(
            [p["level"] for p in level_pool if p["level"] < entry - 5 * PIP],
            reverse=True,
        )
        if len(below) >= 3:
            return round(below[0], 2), round(below[1], 2), round(below[2], 2)
        if len(below) >= 1:
            tp1 = below[0]
            tp2 = below[1] if len(below) > 1 else round(entry - 2 * risk, 2)
            tp3 = round(entry - 3 * risk, 2)
            return round(tp1, 2), round(tp2, 2), round(tp3, 2)
        return round(entry - risk, 2), round(entry - 2 * risk, 2), round(entry - 3 * risk, 2)
    # BUY
    above = sorted([p["level"] for p in level_pool if p["level"] > entry + 5 * PIP])
    if len(above) >= 3:
        return round(above[0], 2), round(above[1], 2), round(above[2], 2)
    if len(above) >= 1:
        tp1 = above[0]
        tp2 = above[1] if len(above) > 1 else round(entry + 2 * risk, 2)
        tp3 = round(entry + 3 * risk, 2)
        return round(tp1, 2), round(tp2, 2), round(tp3, 2)
    return round(entry + risk, 2), round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)


class SessionLiquiditySweepReversalStrategy:
    def __init__(self, session_liquidity_engine: Any = None, level_intel_engine: Any = None) -> None:
        self.session_liquidity = session_liquidity_engine
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
        if m15 is None or len(m15) < SWEEP_LOOKBACK_M15:
            return candidates, rejected
        level_pool = _level_pool_from_plan(plan)
        if not level_pool:
            return candidates, rejected

        recent = m15.tail(SWEEP_LOOKBACK_M15)

        for entry_dict in level_pool:
            level = entry_dict["level"]
            label = entry_dict["label"]
            kind = entry_dict["kind"]

            # SELL setup on buy-side sweep
            if kind == "buy_side":
                swept, conf, sweep_high = _detect_sweep_sell(recent, level)
                if not swept:
                    rejected.append(self._reject("SELL", level, "no_sweep", label))
                    continue
                if conf not in ALLOWED_SELL_CONFIRMATIONS:
                    rejected.append(self._reject("SELL", level, f"confirmation_disabled:{conf}", label))
                    continue
                entry = float(recent.iloc[-1]["close"])
                sl = round(sweep_high + SL_BUFFER_PIPS * PIP, 2)
                ok, why = validate_sl_direction("SELL", entry, sl)
                if not ok:
                    rejected.append(self._reject("SELL", level, f"sl_invalid:{why}", label))
                    continue
                tp1, tp2, tp3 = _build_tp_ladder("SELL", entry, sl, level_pool)
                rr_check = check_risk_reward("SELL", entry, sl, [tp1, tp2, tp3])
                if not rr_check.get("tp1_ok", False):
                    rejected.append(self._reject("SELL", level, f"rr_invalid", label))
                    continue
                setup = StrategySetup(
                    strategy_type="session_liquidity_sweep_reversal",
                    symbol=symbol,
                    direction="SELL",
                    entry_zone_low=level - 2 * PIP,
                    entry_zone_high=sweep_high,
                    trigger_level=level,
                    confirmation_required=[conf],
                    entry=round(entry, 2),
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    invalidation=round(sweep_high + 2 * PIP, 2),
                    reason=f"Buy-side liquidity sweep at {label} ({level:.2f}); "
                           f"reversal confirmed by {conf}",
                    confidence_internal=0.0,
                    liquidity_score=80.0,  # baseline; can be refined by session_liquidity engine
                    session_name=getattr(ctx, "session_name", "") if ctx else "",
                    higher_tf="H1",
                    lower_tf="M15",
                    confirmation_candle_time=self._candle_time(recent.iloc[-1]),
                )
                attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
                candidates.append(setup)

            elif kind == "sell_side":
                swept, conf, sweep_low = _detect_sweep_buy(recent, level)
                if not swept:
                    rejected.append(self._reject("BUY", level, "no_sweep", label))
                    continue
                if conf not in ALLOWED_BUY_CONFIRMATIONS:
                    rejected.append(self._reject("BUY", level, f"confirmation_disabled:{conf}", label))
                    continue
                entry = float(recent.iloc[-1]["close"])
                sl = round(sweep_low - SL_BUFFER_PIPS * PIP, 2)
                ok, why = validate_sl_direction("BUY", entry, sl)
                if not ok:
                    rejected.append(self._reject("BUY", level, f"sl_invalid:{why}", label))
                    continue
                tp1, tp2, tp3 = _build_tp_ladder("BUY", entry, sl, level_pool)
                rr_check = check_risk_reward("BUY", entry, sl, [tp1, tp2, tp3])
                if not rr_check.get("tp1_ok", False):
                    rejected.append(self._reject("BUY", level, "rr_invalid", label))
                    continue
                setup = StrategySetup(
                    strategy_type="session_liquidity_sweep_reversal",
                    symbol=symbol,
                    direction="BUY",
                    entry_zone_low=sweep_low,
                    entry_zone_high=level + 2 * PIP,
                    trigger_level=level,
                    confirmation_required=[conf],
                    entry=round(entry, 2),
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    invalidation=round(sweep_low - 2 * PIP, 2),
                    reason=f"Sell-side liquidity sweep at {label} ({level:.2f}); "
                           f"reversal confirmed by {conf}",
                    confidence_internal=0.0,
                    liquidity_score=80.0,
                    session_name=getattr(ctx, "session_name", "") if ctx else "",
                    higher_tf="H1",
                    lower_tf="M15",
                    confirmation_candle_time=self._candle_time(recent.iloc[-1]),
                )
                attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
                candidates.append(setup)

        return candidates, rejected

    @staticmethod
    def _reject(direction: str, level: float, reason: str, label: str = "") -> RejectedCandidate:
        return RejectedCandidate(
            strategy_type="session_liquidity_sweep_reversal",
            direction=direction,
            reason=reason,
            level=float(level),
            detail=label,
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
