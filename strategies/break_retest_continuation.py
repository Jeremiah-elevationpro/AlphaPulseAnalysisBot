"""Break & Retest Continuation Strategy.

Detects a strong level break, a subsequent retest that doesn't deeply violate
the level, and a bullish/bearish confirmation candle. Each stage of the funnel
emits a specific rejection code so the replay tells you whether candidates are
failing at break, at retest, or at confirmation.

Calibration knobs (config.settings):
  BR_MIN_BREAK_PIPS, BR_MAX_RETEST_DISTANCE_PIPS, BR_RETEST_TOLERANCE_PIPS,
  BR_CONFIRMATION_REQUIRED, BR_ALLOW_ONE_CANDLE_RETEST,
  BR_ALLOW_MULTI_CANDLE_RETEST, BR_DEEP_VIOLATION_PIPS, BR_LOOKBACK_BARS

Active profile (strict/balanced/research) scales BR_MIN_BREAK_PIPS by
profile_multiplier("break_min") so research mode discovers more candidates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from analysis.sl_engine import validate_sl_direction
from analysis.tp_engine import check_risk_reward
from config.settings import (
    BR_ALLOW_MULTI_CANDLE_RETEST,
    BR_ALLOW_ONE_CANDLE_RETEST,
    BR_CONFIRMATION_REQUIRED,
    BR_DEEP_VIOLATION_PIPS,
    BR_LOOKBACK_BARS,
    BR_MAX_RETEST_DISTANCE_PIPS,
    BR_MIN_BREAK_PIPS,
    BR_RETEST_TOLERANCE_PIPS,
)
from strategies.core_strategy_engine import (
    ALLOWED_BUY_CONFIRMATIONS,
    ALLOWED_SELL_CONFIRMATIONS,
    BRRejection,
    FunnelMetrics,
    RejectedCandidate,
    StrategySetup,
    attach_level_intel_evidence,
    profile_multiplier,
)

logger = logging.getLogger(__name__)

PIP = 0.1
BREAK_BUFFER_PIPS = 2.0  # tolerance for "close above" comparison
BREAK_DISPLACEMENT_MIN_PIPS = 20.0
SL_BUFFER_PIPS = 3.0


def _pips(a: float, b: float) -> float:
    return abs(a - b) / PIP


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _effective_break_min_pips() -> float:
    return BR_MIN_BREAK_PIPS * profile_multiplier("break_min")


# ─────────────────────────────────────────────────────────────────────────────
# Level pool — plan-provided plus swing detection
# ─────────────────────────────────────────────────────────────────────────────


def _resistance_pool(plan: Any, m15: pd.DataFrame) -> list[float]:
    pool: list[float] = []
    if isinstance(plan, dict):
        for v in (plan.get("key_resistances") or []):
            try:
                pool.append(float(v))
            except Exception:
                continue
    pool.extend(_swing_highs(m15))
    return sorted({round(p, 2) for p in pool}, reverse=True)


def _support_pool(plan: Any, m15: pd.DataFrame) -> list[float]:
    pool: list[float] = []
    if isinstance(plan, dict):
        for v in (plan.get("key_supports") or []):
            try:
                pool.append(float(v))
            except Exception:
                continue
    pool.extend(_swing_lows(m15))
    return sorted({round(p, 2) for p in pool})


def _swing_highs(df: pd.DataFrame, window: int = 3) -> list[float]:
    if df is None or len(df) < 2 * window + 1:
        return []
    last = df.tail(BR_LOOKBACK_BARS).reset_index(drop=True)
    out: list[float] = []
    highs = last["high"].astype(float).tolist()
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window : i + window + 1]):
            out.append(float(highs[i]))
    return out


def _swing_lows(df: pd.DataFrame, window: int = 3) -> list[float]:
    if df is None or len(df) < 2 * window + 1:
        return []
    last = df.tail(BR_LOOKBACK_BARS).reset_index(drop=True)
    out: list[float] = []
    lows = last["low"].astype(float).tolist()
    for i in range(window, len(lows) - window):
        if lows[i] == min(lows[i - window : i + window + 1]):
            out.append(float(lows[i]))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Break + retest detection (returns staged outcome)
# ─────────────────────────────────────────────────────────────────────────────


def _check_break_retest_buy(m15: pd.DataFrame, resistance: float) -> dict[str, Any]:
    """Walks the M15 history for: break above + retest hold + confirmation.

    Returns:
        {
            "stage": "no_break" | "break_too_weak" | "no_retest"
                   | "retest_too_deep" | "no_confirmation" | "ok",
            "retest_low": float (if reached retest stage),
            "confirmation_type": str (if ok),
        }
    """
    out: dict[str, Any] = {"stage": "no_break", "retest_low": 0.0, "confirmation_type": None}
    if m15 is None or len(m15) < 10:
        return out
    bars = m15.tail(BR_LOOKBACK_BARS).reset_index(drop=True)
    break_thresh = resistance + BREAK_BUFFER_PIPS * PIP
    break_mask = bars["close"].astype(float) > break_thresh
    if not break_mask.any():
        out["stage"] = "no_break"
        return out
    break_idx = int(break_mask[break_mask].index[0])
    break_bar = bars.iloc[break_idx]
    if _body(break_bar) / PIP < _effective_break_min_pips():
        out["stage"] = "break_too_weak"
        return out

    after = bars.iloc[break_idx + 1 :]
    if len(after) < 1:
        out["stage"] = "no_retest"
        return out
    retest_mask = after["low"].astype(float) <= resistance + BR_RETEST_TOLERANCE_PIPS * PIP
    if not retest_mask.any():
        out["stage"] = "no_retest"
        return out
    # Honor one-vs-multi-candle retest preference
    retest_bars = after[retest_mask]
    if not BR_ALLOW_MULTI_CANDLE_RETEST and len(retest_bars) > 3:
        out["stage"] = "no_retest"
        return out
    if not BR_ALLOW_ONE_CANDLE_RETEST and len(retest_bars) <= 1:
        out["stage"] = "no_retest"
        return out
    retest_idx = int(retest_mask[retest_mask].index[0])
    retest_bar = bars.iloc[retest_idx]
    retest_low = float(retest_bar["low"])
    out["retest_low"] = retest_low
    deep_violation = (after["close"].astype(float) < resistance - BR_DEEP_VIOLATION_PIPS * PIP).any()
    if deep_violation:
        out["stage"] = "retest_too_deep"
        return out

    last = bars.iloc[-1]
    prev = bars.iloc[-2]
    if not BR_CONFIRMATION_REQUIRED:
        out["stage"] = "ok"
        out["confirmation_type"] = "break_retest_close_above"
        return out
    # Last bar must be bullish closing back above resistance
    if _is_bullish(last) and float(last["close"]) > resistance + BREAK_BUFFER_PIPS * PIP:
        if (
            _is_bearish(prev)
            and float(last["close"]) > float(prev["open"])
            and float(last["open"]) < float(prev["close"])
        ):
            out["stage"] = "ok"
            out["confirmation_type"] = "bullish_engulfing"
            return out
        if _body(last) / PIP >= BREAK_DISPLACEMENT_MIN_PIPS:
            out["stage"] = "ok"
            out["confirmation_type"] = "break_retest_close_above"
            return out
        out["stage"] = "ok"
        out["confirmation_type"] = "bullish_rejection"
        return out
    out["stage"] = "no_confirmation"
    return out


def _check_break_retest_sell(m15: pd.DataFrame, support: float) -> dict[str, Any]:
    out: dict[str, Any] = {"stage": "no_break", "retest_high": 0.0, "confirmation_type": None}
    if m15 is None or len(m15) < 10:
        return out
    bars = m15.tail(BR_LOOKBACK_BARS).reset_index(drop=True)
    break_thresh = support - BREAK_BUFFER_PIPS * PIP
    break_mask = bars["close"].astype(float) < break_thresh
    if not break_mask.any():
        out["stage"] = "no_break"
        return out
    break_idx = int(break_mask[break_mask].index[0])
    break_bar = bars.iloc[break_idx]
    if _body(break_bar) / PIP < _effective_break_min_pips():
        out["stage"] = "break_too_weak"
        return out

    after = bars.iloc[break_idx + 1 :]
    if len(after) < 1:
        out["stage"] = "no_retest"
        return out
    retest_mask = after["high"].astype(float) >= support - BR_RETEST_TOLERANCE_PIPS * PIP
    if not retest_mask.any():
        out["stage"] = "no_retest"
        return out
    retest_bars = after[retest_mask]
    if not BR_ALLOW_MULTI_CANDLE_RETEST and len(retest_bars) > 3:
        out["stage"] = "no_retest"
        return out
    if not BR_ALLOW_ONE_CANDLE_RETEST and len(retest_bars) <= 1:
        out["stage"] = "no_retest"
        return out
    retest_idx = int(retest_mask[retest_mask].index[0])
    retest_bar = bars.iloc[retest_idx]
    retest_high = float(retest_bar["high"])
    out["retest_high"] = retest_high
    deep_violation = (after["close"].astype(float) > support + BR_DEEP_VIOLATION_PIPS * PIP).any()
    if deep_violation:
        out["stage"] = "retest_too_deep"
        return out

    last = bars.iloc[-1]
    prev = bars.iloc[-2]
    if not BR_CONFIRMATION_REQUIRED:
        out["stage"] = "ok"
        out["confirmation_type"] = "break_retest_close_below"
        return out
    if _is_bearish(last) and float(last["close"]) < support - BREAK_BUFFER_PIPS * PIP:
        if (
            _is_bullish(prev)
            and float(last["close"]) < float(prev["open"])
            and float(last["open"]) > float(prev["close"])
        ):
            out["stage"] = "ok"
            out["confirmation_type"] = "bearish_engulfing"
            return out
        if _body(last) / PIP >= BREAK_DISPLACEMENT_MIN_PIPS:
            out["stage"] = "ok"
            out["confirmation_type"] = "break_retest_close_below"
            return out
        out["stage"] = "ok"
        out["confirmation_type"] = "bearish_rejection"
        return out
    out["stage"] = "no_confirmation"
    return out


def _tp_ladder(direction: str, entry: float, sl: float, pool: list[float]) -> tuple[float, float, float]:
    risk = abs(entry - sl)
    if direction.upper() == "BUY":
        above = [p for p in pool if p > entry + 5 * PIP]
        if len(above) >= 3:
            sorted_a = sorted(above)[:3]
            return round(sorted_a[0], 2), round(sorted_a[1], 2), round(sorted_a[2], 2)
        if len(above) >= 1:
            sorted_a = sorted(above)
            tp1 = sorted_a[0]
            tp2 = sorted_a[1] if len(sorted_a) > 1 else round(entry + 2 * risk, 2)
            tp3 = round(entry + 3 * risk, 2)
            return round(tp1, 2), round(tp2, 2), round(tp3, 2)
        return round(entry + risk, 2), round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)
    below = [p for p in pool if p < entry - 5 * PIP]
    if len(below) >= 3:
        sorted_b = sorted(below, reverse=True)[:3]
        return round(sorted_b[0], 2), round(sorted_b[1], 2), round(sorted_b[2], 2)
    if len(below) >= 1:
        sorted_b = sorted(below, reverse=True)
        tp1 = sorted_b[0]
        tp2 = sorted_b[1] if len(sorted_b) > 1 else round(entry - 2 * risk, 2)
        tp3 = round(entry - 3 * risk, 2)
        return round(tp1, 2), round(tp2, 2), round(tp3, 2)
    return round(entry - risk, 2), round(entry - 2 * risk, 2), round(entry - 3 * risk, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy
# ─────────────────────────────────────────────────────────────────────────────


_STAGE_TO_REASON = {
    "no_break":         BRRejection.NO_BREAK_CLOSE,
    "break_too_weak":   BRRejection.BREAK_TOO_WEAK,
    "no_retest":        BRRejection.NO_RETEST,
    "retest_too_deep":  BRRejection.RETEST_TOO_DEEP,
    "no_confirmation":  BRRejection.NO_CONFIRMATION,
}


class BreakRetestContinuationStrategy:
    strategy_type = "break_retest_continuation"

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
        m15 = data.get("M15")
        if m15 is None or len(m15) < 20:
            return candidates, rejected, funnel

        resistances = _resistance_pool(plan, m15)
        supports = _support_pool(plan, m15)

        for direction, levels, allowed_confs, check_fn, retest_attr in (
            ("BUY",  resistances, ALLOWED_BUY_CONFIRMATIONS,  _check_break_retest_buy,  "retest_low"),
            ("SELL", supports,    ALLOWED_SELL_CONFIRMATIONS, _check_break_retest_sell, "retest_high"),
        ):
            for level in levels:
                if _pips(current_price, level) > 250:
                    continue
                outcome = check_fn(m15, level)
                stage = outcome["stage"]
                if stage == "no_break":
                    funnel.inc("rejected_no_break")
                    rejected.append(self._reject(direction, level, BRRejection.NO_BREAK_CLOSE))
                    continue
                funnel.inc("breaks_detected")
                if stage == "break_too_weak":
                    funnel.inc("rejected_break_too_weak")
                    rejected.append(self._reject(direction, level, BRRejection.BREAK_TOO_WEAK))
                    continue
                funnel.inc("strong_breaks")
                if stage == "no_retest":
                    funnel.inc("rejected_no_retest")
                    rejected.append(self._reject(direction, level, BRRejection.NO_RETEST))
                    continue
                funnel.inc("retests_detected")
                if stage == "retest_too_deep":
                    funnel.inc("rejected_retest_too_deep")
                    rejected.append(self._reject(direction, level, BRRejection.RETEST_TOO_DEEP))
                    continue
                if stage == "no_confirmation":
                    funnel.inc("rejected_no_confirmation")
                    rejected.append(self._reject(direction, level, BRRejection.NO_CONFIRMATION))
                    continue
                # stage == "ok"
                conf = outcome.get("confirmation_type") or ""
                if conf not in allowed_confs:
                    funnel.inc("rejected_no_confirmation")
                    rejected.append(
                        self._reject(direction, level, BRRejection.NO_CONFIRMATION,
                                     extra=f"confirmation_disabled:{conf}")
                    )
                    continue
                funnel.inc("confirmation_detected")
                retest_pivot = float(outcome.get(retest_attr, 0.0))
                entry = float(m15.iloc[-1]["close"])
                if direction == "BUY":
                    sl = round(retest_pivot - SL_BUFFER_PIPS * PIP, 2)
                else:
                    sl = round(retest_pivot + SL_BUFFER_PIPS * PIP, 2)
                ok, why = validate_sl_direction(direction, entry, sl)
                if not ok:
                    funnel.inc("rejected_sl_invalid")
                    rejected.append(self._reject(direction, level, BRRejection.SL_INVALID, extra=why))
                    continue
                pool = resistances if direction == "BUY" else supports
                tp1, tp2, tp3 = _tp_ladder(direction, entry, sl, pool)
                rr_check = check_risk_reward(direction, entry, sl, [tp1, tp2, tp3])
                if not rr_check.get("tp1_ok", False):
                    funnel.inc("rejected_rr_invalid")
                    rejected.append(
                        self._reject(direction, level, BRRejection.RR_INVALID,
                                     extra=f"tp1_rr={rr_check.get('rr_values', [0])[0]:.2f}")
                    )
                    continue
                funnel.inc("risk_valid")
                setup = StrategySetup(
                    strategy_type=self.strategy_type,
                    symbol=symbol,
                    direction=direction,
                    entry_zone_low=min(level, retest_pivot) - BR_RETEST_TOLERANCE_PIPS * PIP,
                    entry_zone_high=max(level, retest_pivot) + BR_RETEST_TOLERANCE_PIPS * PIP,
                    trigger_level=level,
                    confirmation_required=[conf],
                    entry=round(entry, 2),
                    sl=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    invalidation=retest_pivot,
                    reason=(
                        f"Break {'above' if direction == 'BUY' else 'below'} "
                        f"{'resistance' if direction == 'BUY' else 'support'} {level:.2f} "
                        f"held on retest; {conf} confirmation"
                    ),
                    confidence_internal=0.0,
                    session_name=getattr(ctx, "session_name", "") if ctx else "",
                    higher_tf="M15",
                    lower_tf="M5",
                    confirmation_candle_time=self._candle_time(m15.iloc[-1]),
                )
                attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
                candidates.append(setup)

        return candidates, rejected, funnel

    def _reject(self, direction: str, level: float, reason: str, *, extra: str = "") -> RejectedCandidate:
        return RejectedCandidate(
            strategy_type=self.strategy_type,
            direction=direction,
            reason=reason,
            level=float(level),
            detail=extra,
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
