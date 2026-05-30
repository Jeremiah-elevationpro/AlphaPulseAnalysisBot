"""Supply & Demand Retest Strategy.

Identifies demand zones (bullish displacement origin) and supply zones (bearish
displacement origin) on H1/M30 and confirms reactions on M15.

Calibration is driven by config flags:
  SD_MIN_IMPULSE_PIPS, SD_MAX_ZONE_TOUCHES, SD_MAX_DISTANCE_PIPS,
  SD_ALLOW_BODY_ZONE, SD_ALLOW_WICK_ZONE, SD_ZONE_LOOKBACK_BARS

Plus the active profile (strict/balanced/research) widens or narrows the
effective impulse + distance thresholds.

Every rejection is emitted with a strategy-specific reason code defined in
core_strategy_engine.SDRejection so the replay can identify *why* candidates
didn't make it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from analysis.sl_engine import validate_sl_direction
from analysis.tp_engine import check_risk_reward
from config.settings import (
    SD_ALLOW_BODY_ZONE,
    SD_ALLOW_WICK_ZONE,
    SD_MAX_DISTANCE_PIPS,
    SD_MAX_ZONE_TOUCHES,
    SD_MIN_IMPULSE_PIPS,
    SD_ZONE_LOOKBACK_BARS,
)
from strategies.core_strategy_engine import (
    ALLOWED_BUY_CONFIRMATIONS,
    ALLOWED_SELL_CONFIRMATIONS,
    FunnelMetrics,
    RejectedCandidate,
    SDRejection,
    StrategySetup,
    attach_level_intel_evidence,
    profile_multiplier,
)

logger = logging.getLogger(__name__)

PIP = 0.1  # XAUUSD
ZONE_TOLERANCE_PIPS = 8.0
WICK_REJECTION_RATIO = 1.5
SL_BUFFER_PIPS = 3.0


def _pips(a: float, b: float) -> float:
    return abs(a - b) / PIP


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _effective_impulse_min_pips() -> float:
    return SD_MIN_IMPULSE_PIPS * profile_multiplier("impulse")


def _effective_max_distance_pips() -> float:
    return SD_MAX_DISTANCE_PIPS * profile_multiplier("distance")


# ─────────────────────────────────────────────────────────────────────────────
# Zone detection (body and/or wick-to-body)
# ─────────────────────────────────────────────────────────────────────────────


def _zone_bounds(base: pd.Series, *, side: str) -> tuple[float, float]:
    """Compute zone low/high for the base candle of a demand/supply origin.

    side="demand": below the impulse start; side="supply": above the impulse start.
    Honors SD_ALLOW_BODY_ZONE / SD_ALLOW_WICK_ZONE — if both are True, returns
    the wider (wick-to-body) range. If only body is allowed, uses min(open,close)
    to max(open,close). If only wick is allowed, uses low to high.
    """
    o, c = float(base["open"]), float(base["close"])
    lo, hi = float(base["low"]), float(base["high"])
    body_lo, body_hi = min(o, c), max(o, c)
    if SD_ALLOW_WICK_ZONE and SD_ALLOW_BODY_ZONE:
        if side == "demand":
            return lo, body_hi
        return body_lo, hi
    if SD_ALLOW_WICK_ZONE:
        return lo, hi
    if SD_ALLOW_BODY_ZONE:
        return body_lo, body_hi
    # Both disabled — degenerate to body
    return body_lo, body_hi


def _detect_zones(df: pd.DataFrame, *, side: str) -> list[dict[str, Any]]:
    """Find demand zones (side='demand') or supply zones (side='supply').

    Demand: bullish impulse leaving the base; base = the most-recent bearish
    candle immediately before the impulse start.
    Supply: bearish impulse; base = last bullish candle before the impulse.
    """
    if df is None or len(df) < 4:
        return []
    zones: list[dict[str, Any]] = []
    bars = df.tail(SD_ZONE_LOOKBACK_BARS).reset_index(drop=True)
    impulse_min_pips = _effective_impulse_min_pips()
    for i in range(1, len(bars) - 1):
        bar = bars.iloc[i]
        if side == "demand":
            if not _is_bullish(bar):
                continue
        else:
            if not _is_bearish(bar):
                continue
        if _body(bar) / PIP < impulse_min_pips:
            continue
        # Walk back to last opposite-bias candle to define base. Use immediate
        # prior bar; if it's same-direction, accept it anyway (origin can be a
        # consolidation rather than a single opposite bar).
        base = bars.iloc[i - 1]
        z_lo, z_hi = _zone_bounds(base, side=side)
        if z_hi <= z_lo:
            continue
        touches = _count_touches(bars.iloc[i + 1 :], z_lo, z_hi)
        zones.append(
            {
                "low":          z_lo,
                "high":         z_hi,
                "origin_index": i,
                "origin_time":  pd.Timestamp(bar["time"]),
                "touches":      touches,
                "fresh":        touches <= SD_MAX_ZONE_TOUCHES,
                "impulse_body_pips": _body(bar) / PIP,
            }
        )
    return zones


def _count_touches(later: pd.DataFrame, lo: float, hi: float) -> int:
    if later is None or len(later) == 0:
        return 0
    hits = (later["low"].astype(float) <= hi) & (later["high"].astype(float) >= lo)
    return int(hits.sum())


# ─────────────────────────────────────────────────────────────────────────────
# Confirmation detection on M15
# ─────────────────────────────────────────────────────────────────────────────


def _confirmation_at_demand(m15_recent: pd.DataFrame, zone: dict[str, Any]) -> str | None:
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
        _is_bullish(last)
        and _is_bearish(prev)
        and float(last["close"]) > float(prev["open"])
        and float(last["open"]) < float(prev["close"])
        and float(last["low"]) <= hi + ZONE_TOLERANCE_PIPS * PIP
    ):
        return "bullish_engulfing"
    if (
        _is_bullish(last)
        and _body(last) / PIP >= _effective_impulse_min_pips()
        and float(last["close"]) > hi
    ):
        return "bullish_displacement_close"
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
        and float(last["high"]) >= lo - ZONE_TOLERANCE_PIPS * PIP
    ):
        return "bearish_engulfing"
    if (
        _is_bearish(last)
        and _body(last) / PIP >= _effective_impulse_min_pips()
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


def _retest_present(m15_recent: pd.DataFrame, lo: float, hi: float) -> bool:
    if m15_recent is None or len(m15_recent) == 0:
        return False
    w = m15_recent.tail(6)
    return bool(((w["low"].astype(float) <= hi) & (w["high"].astype(float) >= lo)).any())


def _build_tp_ladder(direction: str, entry: float, sl: float) -> tuple[float, float, float]:
    risk = abs(entry - sl)
    if direction.upper() == "BUY":
        return round(entry + risk, 2), round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)
    return round(entry - risk, 2), round(entry - 2 * risk, 2), round(entry - 3 * risk, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy
# ─────────────────────────────────────────────────────────────────────────────


class SupplyDemandRetestStrategy:
    strategy_type = "supply_demand_retest"

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
    ) -> tuple[list[StrategySetup], list[RejectedCandidate], FunnelMetrics]:
        candidates: list[StrategySetup] = []
        rejected: list[RejectedCandidate] = []
        funnel = FunnelMetrics(strategy_type=self.strategy_type)

        if current_price is None:
            return candidates, rejected, funnel

        # Higher-timeframe zone identification: H1 preferred, M30 fallback.
        htf_df = data.get("H1")
        if htf_df is None or (hasattr(htf_df, "empty") and htf_df.empty):
            htf_df = data.get("M30")
        m15 = data.get("M15")
        if htf_df is None or m15 is None or len(htf_df) == 0 or len(m15) == 0:
            return candidates, rejected, funnel

        demand_zones = _detect_zones(htf_df, side="demand")
        supply_zones = _detect_zones(htf_df, side="supply")
        funnel.inc("zones_found", len(demand_zones) + len(supply_zones))

        if not demand_zones and not supply_zones:
            rejected.append(
                RejectedCandidate(
                    strategy_type=self.strategy_type,
                    direction="?",
                    reason=SDRejection.NO_ZONE_FOUND,
                    level=current_price,
                )
            )

        max_distance = _effective_max_distance_pips()
        m15_tail = m15.tail(6)

        for direction, side, zones, allowed_confs, confirm_fn in (
            ("BUY",  "demand", demand_zones, ALLOWED_BUY_CONFIRMATIONS,  _confirmation_at_demand),
            ("SELL", "supply", supply_zones, ALLOWED_SELL_CONFIRMATIONS, _confirmation_at_supply),
        ):
            for zone in zones:
                if zone["fresh"]:
                    funnel.inc("fresh_zones")
                else:
                    funnel.inc("rejected_not_fresh")
                    rejected.append(self._reject(direction, zone, SDRejection.ZONE_NOT_FRESH))
                    continue
                # distance to zone (BUY -> zone above; SELL -> zone below)
                ref = zone["high"] if direction == "BUY" else zone["low"]
                dist_pips = _pips(current_price, ref)
                if dist_pips <= max_distance:
                    funnel.inc("zones_near_price")
                else:
                    funnel.inc("rejected_too_far")
                    rejected.append(
                        self._reject(direction, zone, SDRejection.ZONE_TOO_FAR,
                                     extra=f"{dist_pips:.0f}p")
                    )
                    continue
                if not _retest_present(m15_tail, zone["low"], zone["high"]):
                    funnel.inc("rejected_no_retest")
                    rejected.append(self._reject(direction, zone, SDRejection.NO_RETEST))
                    continue
                funnel.inc("retests_detected")
                conf = confirm_fn(m15_tail, zone)
                if not conf:
                    funnel.inc("rejected_no_rejection_candle")
                    rejected.append(self._reject(direction, zone, SDRejection.NO_REJECTION_CANDLE))
                    continue
                if conf not in allowed_confs:
                    funnel.inc("rejected_no_rejection_candle")
                    rejected.append(
                        self._reject(direction, zone, SDRejection.NO_REJECTION_CANDLE,
                                     extra=f"confirmation_disabled:{conf}")
                    )
                    continue
                funnel.inc("rejection_confirmations")
                entry = float(m15.iloc[-1]["close"])
                if direction == "BUY":
                    sl = round(zone["low"] - SL_BUFFER_PIPS * PIP, 2)
                else:
                    sl = round(zone["high"] + SL_BUFFER_PIPS * PIP, 2)
                ok, why = validate_sl_direction(direction, entry, sl)
                if not ok:
                    funnel.inc("rejected_sl_invalid")
                    rejected.append(self._reject(direction, zone, SDRejection.SL_INVALID,
                                                 extra=why))
                    continue
                tp1, tp2, tp3 = _build_tp_ladder(direction, entry, sl)
                rr_check = check_risk_reward(direction, entry, sl, [tp1, tp2, tp3])
                if not rr_check.get("tp1_ok", False):
                    funnel.inc("rejected_rr_invalid")
                    rejected.append(
                        self._reject(direction, zone, SDRejection.RR_INVALID,
                                     extra=f"tp1_rr={rr_check.get('rr_values', [0])[0]:.2f}")
                    )
                    continue
                funnel.inc("risk_valid")
                setup = StrategySetup(
                    strategy_type=self.strategy_type,
                    symbol=symbol,
                    direction=direction,
                    entry_zone_low=zone["low"],
                    entry_zone_high=zone["high"],
                    trigger_level=zone["high"] if direction == "BUY" else zone["low"],
                    confirmation_required=[conf],
                    entry=round(entry, 2),
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    invalidation=zone["low"] if direction == "BUY" else zone["high"],
                    reason=(
                        f"{'Bullish' if direction == 'BUY' else 'Bearish'} reaction from "
                        f"{'H1' if 'H1' in data else 'M30'} "
                        f"{'demand' if direction == 'BUY' else 'supply'} zone "
                        f"({zone['low']:.2f}-{zone['high']:.2f}) — {conf}"
                    ),
                    confidence_internal=0.0,
                    session_name=getattr(ctx, "session_name", "") if ctx else "",
                    higher_tf="H1" if "H1" in data else "M30",
                    lower_tf="M15",
                    confirmation_candle_time=self._candle_time(m15.iloc[-1]),
                )
                attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
                candidates.append(setup)

        return candidates, rejected, funnel

    def _reject(
        self, direction: str, zone: dict[str, Any], reason: str, *, extra: str = ""
    ) -> RejectedCandidate:
        return RejectedCandidate(
            strategy_type=self.strategy_type,
            direction=direction,
            reason=reason,
            level=float(zone.get("low", 0.0)) if direction == "BUY" else float(zone.get("high", 0.0)),
            detail=(
                f"zone={zone.get('low', 0):.2f}-{zone.get('high', 0):.2f} "
                f"impulse={zone.get('impulse_body_pips', 0):.0f}p touches={zone.get('touches', 0)} {extra}"
            ).strip(),
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
