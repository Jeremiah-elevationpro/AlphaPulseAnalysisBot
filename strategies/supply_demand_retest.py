"""Supply & Demand Retest Strategy.

Trade reactions from clear supply/demand zones identified by bullish/bearish
displacement origins on H1/M30. Confirmation on M15/M5.

BUY (demand):
  1. Find strong demand origin on H1/M30 (bullish displacement leaving a base).
  2. Zone fresh or low-touch.
  3. Price returns to demand.
  4. Bullish rejection / engulfing / displacement-close at zone.
  5. SL below demand low; TP ladder up through structure/liquidity.

SELL (supply): mirror.

This module is intentionally conservative. Edge cases the user should tune
in backtesting are marked with `# TUNE:` comments.
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

# TUNE: zone detection parameters
DISPLACEMENT_MIN_BODY_PIPS = 25.0      # bar body size that counts as "displacement"
ZONE_FRESHNESS_MAX_TOUCHES = 3         # >3 touches = no longer fresh
ZONE_LOOKBACK_BARS = 80                # how far back to scan for origin
ZONE_TOLERANCE_PIPS = 8.0              # how close price must be for "return to zone"
MAX_DISTANCE_TO_ZONE_PIPS = 60.0       # if entry is too far, defer as watch
WICK_REJECTION_RATIO = 1.5             # wick > 1.5x body counts as rejection


def _pips(a: float, b: float) -> float:
    return abs(a - b) / 0.1  # XAUUSD


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _detect_demand_zones(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Find demand zone origins — strong bullish displacement leaving a base.

    Returns list of {low, high, origin_index, touches, fresh}.
    """
    if df is None or len(df) < 4:
        return []
    zones: list[dict[str, Any]] = []
    bars = df.tail(ZONE_LOOKBACK_BARS).reset_index(drop=True)
    for i in range(1, len(bars) - 1):
        bar = bars.iloc[i]
        if not _is_bullish(bar):
            continue
        if _body(bar) / 0.1 < DISPLACEMENT_MIN_BODY_PIPS:
            continue
        # base = the bar just before the displacement (origin)
        base = bars.iloc[i - 1]
        zone_low = float(min(base["low"], base["open"], base["close"]))
        zone_high = float(max(base["high"], base["open"], base["close"]))
        if zone_high <= zone_low:
            continue
        touches = _count_touches(bars.iloc[i + 1 :], zone_low, zone_high)
        zones.append(
            {
                "low":          zone_low,
                "high":         zone_high,
                "origin_index": i,
                "origin_time":  pd.Timestamp(bar["time"]),
                "touches":      touches,
                "fresh":        touches <= ZONE_FRESHNESS_MAX_TOUCHES,
            }
        )
    return zones


def _detect_supply_zones(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df is None or len(df) < 4:
        return []
    zones: list[dict[str, Any]] = []
    bars = df.tail(ZONE_LOOKBACK_BARS).reset_index(drop=True)
    for i in range(1, len(bars) - 1):
        bar = bars.iloc[i]
        if not _is_bearish(bar):
            continue
        if _body(bar) / 0.1 < DISPLACEMENT_MIN_BODY_PIPS:
            continue
        base = bars.iloc[i - 1]
        zone_low = float(min(base["low"], base["open"], base["close"]))
        zone_high = float(max(base["high"], base["open"], base["close"]))
        if zone_high <= zone_low:
            continue
        touches = _count_touches(bars.iloc[i + 1 :], zone_low, zone_high)
        zones.append(
            {
                "low":          zone_low,
                "high":         zone_high,
                "origin_index": i,
                "origin_time":  pd.Timestamp(bar["time"]),
                "touches":      touches,
                "fresh":        touches <= ZONE_FRESHNESS_MAX_TOUCHES,
            }
        )
    return zones


def _count_touches(later: pd.DataFrame, lo: float, hi: float) -> int:
    if later is None or len(later) == 0:
        return 0
    hits = (later["low"].astype(float) <= hi) & (later["high"].astype(float) >= lo)
    return int(hits.sum())


def _confirmation_at_demand(m15_recent: pd.DataFrame, zone: dict[str, Any]) -> str | None:
    """Return confirmation type name if the last few M15 bars show a bullish
    reaction at the demand zone, else None.
    """
    if m15_recent is None or len(m15_recent) < 3:
        return None
    last = m15_recent.iloc[-1]
    prev = m15_recent.iloc[-2]
    lo, hi = zone["low"], zone["high"]

    # touched the zone in the last 3 bars?
    window = m15_recent.tail(3)
    touched = bool(((window["low"].astype(float) <= hi) & (window["high"].astype(float) >= lo)).any())
    if not touched:
        return None

    # 1. bullish engulfing inside/near zone
    if (
        _is_bullish(last)
        and _is_bearish(prev)
        and float(last["close"]) > float(prev["open"])
        and float(last["open"]) < float(prev["close"])
        and float(last["low"]) <= hi + ZONE_TOLERANCE_PIPS * 0.1
    ):
        return "bullish_engulfing"
    # 2. bullish displacement close (strong bullish away from zone)
    if (
        _is_bullish(last)
        and _body(last) / 0.1 >= DISPLACEMENT_MIN_BODY_PIPS
        and float(last["close"]) > hi
    ):
        return "bullish_displacement_close"
    # 3. bullish rejection (wick tags zone, body closes back above)
    wick_bottom = float(last["open"] if _is_bullish(last) else last["close"]) - float(last["low"])
    body_size = max(_body(last), 0.01)
    if (
        _is_bullish(last)
        and float(last["low"]) <= hi
        and float(last["close"]) > hi
        and wick_bottom / body_size >= WICK_REJECTION_RATIO
    ):
        return "bullish_rejection"
    return None


def _confirmation_at_supply(m15_recent: pd.DataFrame, zone: dict[str, Any]) -> str | None:
    if m15_recent is None or len(m15_recent) < 3:
        return None
    last = m15_recent.iloc[-1]
    prev = m15_recent.iloc[-2]
    lo, hi = zone["low"], zone["high"]
    window = m15_recent.tail(3)
    touched = bool(((window["low"].astype(float) <= hi) & (window["high"].astype(float) >= lo)).any())
    if not touched:
        return None
    if (
        _is_bearish(last)
        and _is_bullish(prev)
        and float(last["close"]) < float(prev["open"])
        and float(last["open"]) > float(prev["close"])
        and float(last["high"]) >= lo - ZONE_TOLERANCE_PIPS * 0.1
    ):
        return "bearish_engulfing"
    if (
        _is_bearish(last)
        and _body(last) / 0.1 >= DISPLACEMENT_MIN_BODY_PIPS
        and float(last["close"]) < lo
    ):
        return "bearish_displacement_close"
    wick_top = float(last["high"]) - float(last["open"] if _is_bearish(last) else last["close"])
    body_size = max(_body(last), 0.01)
    if (
        _is_bearish(last)
        and float(last["high"]) >= lo
        and float(last["close"]) < lo
        and wick_top / body_size >= WICK_REJECTION_RATIO
    ):
        return "bearish_rejection"
    return None


def _build_tp_ladder(direction: str, entry: float, sl: float) -> tuple[float, float, float]:
    """Conservative TP ladder when no Level Intelligence target is available.
    TP1 = 1R, TP2 = 2R, TP3 = 3R. Replaced by structural targets if the engine
    overrides them after candidate is emitted.
    """
    risk = abs(entry - sl)
    if direction.upper() == "BUY":
        return round(entry + risk, 2), round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)
    return round(entry - risk, 2), round(entry - 2 * risk, 2), round(entry - 3 * risk, 2)


class SupplyDemandRetestStrategy:
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

        # Higher-timeframe zone identification: H1 preferred, M30 fallback.
        # Cannot use `or` on DataFrames — pandas raises on truthiness ambiguity.
        htf_df = data.get("H1")
        if htf_df is None or (hasattr(htf_df, "empty") and htf_df.empty):
            htf_df = data.get("M30")
        m15 = data.get("M15")
        if htf_df is None or m15 is None or len(htf_df) == 0 or len(m15) == 0:
            return candidates, rejected

        demand = _detect_demand_zones(htf_df)
        supply = _detect_supply_zones(htf_df)

        # BUYS
        for zone in demand:
            if not zone["fresh"]:
                rejected.append(self._reject("BUY", zone, "zone_not_fresh"))
                continue
            dist_pips = _pips(current_price, zone["high"])
            if dist_pips > MAX_DISTANCE_TO_ZONE_PIPS:
                rejected.append(self._reject("BUY", zone, f"distance_{dist_pips:.0f}p"))
                continue
            conf = _confirmation_at_demand(m15.tail(6), zone)
            if not conf:
                rejected.append(self._reject("BUY", zone, "no_confirmation"))
                continue
            if conf not in ALLOWED_BUY_CONFIRMATIONS:
                rejected.append(self._reject("BUY", zone, f"confirmation_disabled:{conf}"))
                continue
            entry = float(m15.iloc[-1]["close"])
            sl = round(zone["low"] - 3 * 0.1, 2)  # 3 pips below demand low
            ok, why = validate_sl_direction("BUY", entry, sl)
            if not ok:
                rejected.append(self._reject("BUY", zone, f"sl_invalid:{why}"))
                continue
            tp1, tp2, tp3 = _build_tp_ladder("BUY", entry, sl)
            rr_check = check_risk_reward("BUY", entry, sl, [tp1, tp2, tp3])
            if not rr_check.get("tp1_ok", False):
                rejected.append(
                    self._reject("BUY", zone, f"rr_invalid:tp1_rr={rr_check.get('rr_values', [0])[0]:.2f}")
                )
                continue
            setup = StrategySetup(
                strategy_type="supply_demand_retest",
                symbol=symbol,
                direction="BUY",
                entry_zone_low=zone["low"],
                entry_zone_high=zone["high"],
                trigger_level=zone["high"],
                confirmation_required=[conf],
                entry=round(entry, 2),
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                invalidation=zone["low"],
                reason=f"Bullish reaction from H1/M30 demand zone "
                       f"({zone['low']:.2f}-{zone['high']:.2f}) — {conf}",
                confidence_internal=0.0,
                session_name=getattr(ctx, "session_name", "") if ctx else "",
                higher_tf="H1" if "H1" in data else "M30",
                lower_tf="M15",
                confirmation_candle_time=self._candle_time(m15.iloc[-1]),
            )
            attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
            candidates.append(setup)

        # SELLS
        for zone in supply:
            if not zone["fresh"]:
                rejected.append(self._reject("SELL", zone, "zone_not_fresh"))
                continue
            dist_pips = _pips(current_price, zone["low"])
            if dist_pips > MAX_DISTANCE_TO_ZONE_PIPS:
                rejected.append(self._reject("SELL", zone, f"distance_{dist_pips:.0f}p"))
                continue
            conf = _confirmation_at_supply(m15.tail(6), zone)
            if not conf:
                rejected.append(self._reject("SELL", zone, "no_confirmation"))
                continue
            if conf not in ALLOWED_SELL_CONFIRMATIONS:
                rejected.append(self._reject("SELL", zone, f"confirmation_disabled:{conf}"))
                continue
            entry = float(m15.iloc[-1]["close"])
            sl = round(zone["high"] + 3 * 0.1, 2)
            ok, why = validate_sl_direction("SELL", entry, sl)
            if not ok:
                rejected.append(self._reject("SELL", zone, f"sl_invalid:{why}"))
                continue
            tp1, tp2, tp3 = _build_tp_ladder("SELL", entry, sl)
            rr_check = check_risk_reward("SELL", entry, sl, [tp1, tp2, tp3])
            if not rr_check.get("tp1_ok", False):
                rejected.append(
                    self._reject("SELL", zone, f"rr_invalid:tp1_rr={rr_check.get('rr_values', [0])[0]:.2f}")
                )
                continue
            setup = StrategySetup(
                strategy_type="supply_demand_retest",
                symbol=symbol,
                direction="SELL",
                entry_zone_low=zone["low"],
                entry_zone_high=zone["high"],
                trigger_level=zone["low"],
                confirmation_required=[conf],
                entry=round(entry, 2),
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                invalidation=zone["high"],
                reason=f"Bearish reaction from H1/M30 supply zone "
                       f"({zone['low']:.2f}-{zone['high']:.2f}) — {conf}",
                confidence_internal=0.0,
                session_name=getattr(ctx, "session_name", "") if ctx else "",
                higher_tf="H1" if "H1" in data else "M30",
                lower_tf="M15",
                confirmation_candle_time=self._candle_time(m15.iloc[-1]),
            )
            attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
            candidates.append(setup)

        return candidates, rejected

    @staticmethod
    def _reject(direction: str, zone: dict[str, Any], reason: str) -> RejectedCandidate:
        return RejectedCandidate(
            strategy_type="supply_demand_retest",
            direction=direction,
            reason=reason,
            level=float(zone.get("low", 0.0)) if direction == "BUY" else float(zone.get("high", 0.0)),
            detail=f"zone={zone.get('low', 0):.2f}-{zone.get('high', 0):.2f}",
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
