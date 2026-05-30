"""Session Liquidity Sweep Reversal Strategy.

Detects buy-side / sell-side liquidity sweeps at session highs/lows and PDH/PDL,
then trades the reversal when the sweep fails (close back inside).

When a market plan with a session_liquidity block is available, levels come
from there. When the plan is None (e.g. historical replay), the strategy
derives session high/low + previous-day high/low from the M15 candle history
itself so it remains useful for tuning.

Calibration knobs (config.settings):
  LIQ_MIN_SWEEP_PIPS, LIQ_MAX_SWEEP_PIPS,
  LIQ_CLOSE_BACK_REQUIRED, LIQ_ALLOW_WICK_REJECTION,
  LIQ_DISPLACEMENT_OPTIONAL_FOR_WATCH, LIQ_DISPLACEMENT_REQUIRED_FOR_ENTRY,
  LIQ_SWEEP_LOOKBACK_M15
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from analysis.sl_engine import validate_sl_direction
from analysis.tp_engine import check_risk_reward
from config.settings import (
    LIQ_ALLOW_WICK_REJECTION,
    LIQ_CLOSE_BACK_REQUIRED,
    LIQ_DISPLACEMENT_OPTIONAL_FOR_WATCH,
    LIQ_DISPLACEMENT_REQUIRED_FOR_ENTRY,
    LIQ_MAX_SWEEP_PIPS,
    LIQ_MIN_SWEEP_PIPS,
    LIQ_SWEEP_LOOKBACK_M15,
)
from strategies.core_strategy_engine import (
    ALLOWED_BUY_CONFIRMATIONS,
    ALLOWED_SELL_CONFIRMATIONS,
    FunnelMetrics,
    LiqRejection,
    RejectedCandidate,
    StrategySetup,
    attach_level_intel_evidence,
    profile_multiplier,
)

logger = logging.getLogger(__name__)

PIP = 0.1
SL_BUFFER_PIPS = 5.0
WICK_REJECTION_BODY_RATIO = 1.5


def _pips(a: float, b: float) -> float:
    return abs(a - b) / PIP


def _body(row: pd.Series) -> float:
    return abs(float(row["close"]) - float(row["open"]))


def _is_bullish(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def _is_bearish(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def _effective_sweep_min_pips() -> float:
    return LIQ_MIN_SWEEP_PIPS * profile_multiplier("sweep_min")


# ─────────────────────────────────────────────────────────────────────────────
# Level pool — plan-provided or derived from candle history
# ─────────────────────────────────────────────────────────────────────────────


def _level_pool_from_plan(plan: Any) -> list[dict[str, Any]]:
    pool: list[dict[str, Any]] = []
    if not plan:
        return pool
    sl = (plan.get("session_liquidity") if isinstance(plan, dict) else None) or {}
    sessions = sl.get("sessions") or {}
    for sess_key in ("asia", "london", "new_york"):
        sess = sessions.get(sess_key) or {}
        if sess.get("high") is not None:
            pool.append({"label": f"{sess_key.title()} High", "level": float(sess["high"]),
                          "kind": "buy_side", "session": sess_key, "source": "plan"})
        if sess.get("low") is not None:
            pool.append({"label": f"{sess_key.title()} Low", "level": float(sess["low"]),
                          "kind": "sell_side", "session": sess_key, "source": "plan"})
    for key, kind, label in (
        ("previous_day_high", "buy_side",  "Prev Day High"),
        ("previous_day_low",  "sell_side", "Prev Day Low"),
        ("current_day_high",  "buy_side",  "Curr Day High"),
        ("current_day_low",   "sell_side", "Curr Day Low"),
    ):
        if sl.get(key) is not None:
            pool.append({"label": label, "level": float(sl[key]),
                          "kind": kind, "session": "day", "source": "plan"})
    return pool


def _level_pool_from_candles(m15: pd.DataFrame) -> list[dict[str, Any]]:
    """Derive previous-day / current-day / session highs and lows directly
    from M15 candle timestamps. Used when no market_plan is available
    (historical replay).
    """
    if m15 is None or len(m15) == 0:
        return []
    try:
        times = pd.to_datetime(m15["time"], utc=True)
    except Exception:
        return []
    end_ts = pd.Timestamp(times.iloc[-1])
    today_utc = end_ts.normalize()
    yesterday_utc = today_utc - pd.Timedelta(days=1)

    pool: list[dict[str, Any]] = []

    # Helper to compute high/low for a date filter.
    def _range_levels(label_high: str, label_low: str, mask, session: str) -> None:
        sub = m15[mask]
        if len(sub) < 2:
            return
        try:
            hi = float(sub["high"].astype(float).max())
            lo = float(sub["low"].astype(float).min())
        except Exception:
            return
        pool.append({"label": label_high, "level": hi, "kind": "buy_side",
                     "session": session, "source": "derived"})
        pool.append({"label": label_low, "level": lo, "kind": "sell_side",
                     "session": session, "source": "derived"})

    # Previous day
    _range_levels(
        "Prev Day High", "Prev Day Low",
        (times >= yesterday_utc) & (times < today_utc),
        "day",
    )
    # Current day
    _range_levels(
        "Curr Day High", "Curr Day Low",
        (times >= today_utc),
        "day",
    )
    # Session ranges (using last 24h sliced by UTC hour-of-day)
    last_24h = m15[times >= end_ts - pd.Timedelta(hours=24)]
    if len(last_24h) > 0:
        last_24h_times = pd.to_datetime(last_24h["time"], utc=True)
        hours = last_24h_times.dt.hour
        asia_mask    = (hours >= 23) | (hours < 7)
        london_mask  = (hours >= 7) & (hours < 14)
        ny_mask      = (hours >= 13) & (hours < 21)
        for label_h, label_l, mask, sess in (
            ("Asian High",    "Asian Low",    asia_mask,   "asia"),
            ("London High",   "London Low",   london_mask, "london"),
            ("New York High", "New York Low", ny_mask,     "new_york"),
        ):
            sub = last_24h[mask.values]
            if len(sub) < 2:
                continue
            try:
                hi = float(sub["high"].astype(float).max())
                lo = float(sub["low"].astype(float).min())
            except Exception:
                continue
            pool.append({"label": label_h, "level": hi, "kind": "buy_side",
                         "session": sess, "source": "derived"})
            pool.append({"label": label_l, "level": lo, "kind": "sell_side",
                         "session": sess, "source": "derived"})

    return pool


# ─────────────────────────────────────────────────────────────────────────────
# Sweep detection
# ─────────────────────────────────────────────────────────────────────────────


def _detect_sweep_sell(
    m15_recent: pd.DataFrame, level: float
) -> tuple[bool, str | None, float, str]:
    """Returns (sweep_present, confirmation_type | None, sweep_high, reason_if_none).

    Even when no confirmation forms, we want the caller to know whether the
    *sweep itself* happened so the funnel separates "no sweep" from
    "no close-back" / "no displacement".
    """
    if m15_recent is None or len(m15_recent) < 3:
        return False, None, 0.0, LiqRejection.NO_SWEEP
    min_pips = _effective_sweep_min_pips()
    pierced = m15_recent[
        (m15_recent["high"].astype(float) - level) >= (min_pips * PIP)
    ]
    if len(pierced) == 0:
        # Maybe the high pierced by less than min — still report no_sweep.
        any_pierced = m15_recent[m15_recent["high"].astype(float) > level + 0.5 * PIP]
        return False, None, 0.0, LiqRejection.NO_SWEEP
    sweep_bar = pierced.iloc[-1]
    sweep_high = float(sweep_bar["high"])
    # also reject sweeps that are absurdly far (false spikes)
    if (sweep_high - level) / PIP > LIQ_MAX_SWEEP_PIPS:
        return False, None, 0.0, LiqRejection.NO_SWEEP

    last = m15_recent.iloc[-1]
    prev = m15_recent.iloc[-2]
    close_back = float(last["close"]) < level
    if LIQ_CLOSE_BACK_REQUIRED and not close_back:
        # Try wick rejection
        if LIQ_ALLOW_WICK_REJECTION:
            body_size = max(_body(last), 0.01)
            wick_top = float(last["high"]) - max(float(last["close"]), float(last["open"]))
            if wick_top / body_size >= WICK_REJECTION_BODY_RATIO and float(last["high"]) > level:
                return True, "bearish_rejection", sweep_high, ""
        return True, None, sweep_high, LiqRejection.NO_CLOSE_BACK_INSIDE
    if _is_bearish(last) and close_back:
        return True, "sweep_above_close_back_below", sweep_high, ""
    if (
        _is_bearish(last)
        and _is_bullish(prev)
        and float(last["close"]) < float(prev["open"])
        and float(last["open"]) > float(prev["close"])
        and float(prev["high"]) >= level
    ):
        return True, "bearish_engulfing", sweep_high, ""
    if LIQ_ALLOW_WICK_REJECTION:
        body_size = max(_body(last), 0.01)
        wick_top = float(last["high"]) - max(float(last["close"]), float(last["open"]))
        if wick_top / body_size >= WICK_REJECTION_BODY_RATIO and float(last["high"]) > level and float(last["close"]) < level:
            return True, "bearish_rejection", sweep_high, ""
    return True, None, sweep_high, LiqRejection.NO_DISPLACEMENT_AFTER_SWEEP


def _detect_sweep_buy(
    m15_recent: pd.DataFrame, level: float
) -> tuple[bool, str | None, float, str]:
    if m15_recent is None or len(m15_recent) < 3:
        return False, None, 0.0, LiqRejection.NO_SWEEP
    min_pips = _effective_sweep_min_pips()
    pierced = m15_recent[
        (level - m15_recent["low"].astype(float)) >= (min_pips * PIP)
    ]
    if len(pierced) == 0:
        return False, None, 0.0, LiqRejection.NO_SWEEP
    sweep_bar = pierced.iloc[-1]
    sweep_low = float(sweep_bar["low"])
    if (level - sweep_low) / PIP > LIQ_MAX_SWEEP_PIPS:
        return False, None, 0.0, LiqRejection.NO_SWEEP
    last = m15_recent.iloc[-1]
    prev = m15_recent.iloc[-2]
    close_back = float(last["close"]) > level
    if LIQ_CLOSE_BACK_REQUIRED and not close_back:
        if LIQ_ALLOW_WICK_REJECTION:
            body_size = max(_body(last), 0.01)
            wick_bot = min(float(last["close"]), float(last["open"])) - float(last["low"])
            if wick_bot / body_size >= WICK_REJECTION_BODY_RATIO and float(last["low"]) < level:
                return True, "bullish_rejection", sweep_low, ""
        return True, None, sweep_low, LiqRejection.NO_CLOSE_BACK_INSIDE
    if _is_bullish(last) and close_back:
        return True, "sweep_below_close_back_above", sweep_low, ""
    if (
        _is_bullish(last)
        and _is_bearish(prev)
        and float(last["close"]) > float(prev["open"])
        and float(last["open"]) < float(prev["close"])
        and float(prev["low"]) <= level
    ):
        return True, "bullish_engulfing", sweep_low, ""
    if LIQ_ALLOW_WICK_REJECTION:
        body_size = max(_body(last), 0.01)
        wick_bot = min(float(last["close"]), float(last["open"])) - float(last["low"])
        if wick_bot / body_size >= WICK_REJECTION_BODY_RATIO and float(last["low"]) < level and float(last["close"]) > level:
            return True, "bullish_rejection", sweep_low, ""
    return True, None, sweep_low, LiqRejection.NO_DISPLACEMENT_AFTER_SWEEP


# ─────────────────────────────────────────────────────────────────────────────
# TP ladder
# ─────────────────────────────────────────────────────────────────────────────


def _build_tp_ladder(
    direction: str, entry: float, sl: float, level_pool: list[dict[str, Any]]
) -> tuple[float, float, float]:
    risk = abs(entry - sl)
    if direction.upper() == "SELL":
        below = sorted(
            [p["level"] for p in level_pool if p["level"] < entry - 5 * PIP], reverse=True
        )
        if len(below) >= 3:
            return round(below[0], 2), round(below[1], 2), round(below[2], 2)
        if len(below) >= 1:
            tp1 = below[0]
            tp2 = below[1] if len(below) > 1 else round(entry - 2 * risk, 2)
            tp3 = round(entry - 3 * risk, 2)
            return round(tp1, 2), round(tp2, 2), round(tp3, 2)
        return round(entry - risk, 2), round(entry - 2 * risk, 2), round(entry - 3 * risk, 2)
    above = sorted([p["level"] for p in level_pool if p["level"] > entry + 5 * PIP])
    if len(above) >= 3:
        return round(above[0], 2), round(above[1], 2), round(above[2], 2)
    if len(above) >= 1:
        tp1 = above[0]
        tp2 = above[1] if len(above) > 1 else round(entry + 2 * risk, 2)
        tp3 = round(entry + 3 * risk, 2)
        return round(tp1, 2), round(tp2, 2), round(tp3, 2)
    return round(entry + risk, 2), round(entry + 2 * risk, 2), round(entry + 3 * risk, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy
# ─────────────────────────────────────────────────────────────────────────────


class SessionLiquiditySweepReversalStrategy:
    strategy_type = "session_liquidity_sweep_reversal"

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
    ) -> tuple[list[StrategySetup], list[RejectedCandidate], FunnelMetrics]:
        candidates: list[StrategySetup] = []
        rejected: list[RejectedCandidate] = []
        funnel = FunnelMetrics(strategy_type=self.strategy_type)

        if current_price is None:
            return candidates, rejected, funnel
        m15 = data.get("M15")
        if m15 is None or len(m15) < LIQ_SWEEP_LOOKBACK_M15:
            return candidates, rejected, funnel

        # Prefer plan-provided levels; fall back to candle-derived levels.
        level_pool = _level_pool_from_plan(plan)
        if not level_pool:
            level_pool = _level_pool_from_candles(m15)
        funnel.inc("liquidity_levels_found", len(level_pool))
        if not level_pool:
            rejected.append(
                RejectedCandidate(
                    strategy_type=self.strategy_type,
                    direction="?",
                    reason=LiqRejection.NO_SESSION_LEVELS,
                    level=current_price,
                )
            )
            return candidates, rejected, funnel

        # Drop levels too close to current price (already swept) and too far
        # (no near-term interaction). LIQ_MAX_SWEEP_PIPS approximates "too far".
        valid = []
        for p in level_pool:
            if _pips(current_price, p["level"]) > LIQ_MAX_SWEEP_PIPS * 4:
                continue
            valid.append(p)
        funnel.inc("valid_liquidity_levels", len(valid))
        if not valid:
            rejected.append(
                RejectedCandidate(
                    strategy_type=self.strategy_type,
                    direction="?",
                    reason=LiqRejection.NO_VALID_LIQUIDITY,
                    level=current_price,
                )
            )
            return candidates, rejected, funnel

        recent = m15.tail(LIQ_SWEEP_LOOKBACK_M15)

        for entry_dict in valid:
            level = entry_dict["level"]
            label = entry_dict["label"]
            kind = entry_dict["kind"]
            direction = "SELL" if kind == "buy_side" else "BUY"
            allowed = ALLOWED_SELL_CONFIRMATIONS if direction == "SELL" else ALLOWED_BUY_CONFIRMATIONS

            if direction == "SELL":
                swept, conf, extreme, fail_reason = _detect_sweep_sell(recent, level)
            else:
                swept, conf, extreme, fail_reason = _detect_sweep_buy(recent, level)

            if not swept:
                funnel.inc("rejected_no_sweep")
                rejected.append(self._reject(direction, level, label, LiqRejection.NO_SWEEP))
                continue
            funnel.inc("sweeps_detected")

            if conf is None:
                # sweep happened, but no actionable confirmation
                if fail_reason == LiqRejection.NO_CLOSE_BACK_INSIDE:
                    funnel.inc("rejected_no_close_back")
                elif fail_reason == LiqRejection.NO_DISPLACEMENT_AFTER_SWEEP:
                    funnel.inc("rejected_no_displacement")
                rejected.append(self._reject(direction, level, label, fail_reason))
                continue
            if conf not in allowed:
                funnel.inc("rejected_no_close_back")
                rejected.append(
                    self._reject(direction, level, label, LiqRejection.NO_CLOSE_BACK_INSIDE,
                                 extra=f"confirmation_disabled:{conf}")
                )
                continue
            funnel.inc("close_back_inside_detected")
            # spec: displacement may be optional for "watch" but required for entry.
            # Right now we emit entries; respect the required-for-entry flag.
            if LIQ_DISPLACEMENT_REQUIRED_FOR_ENTRY and conf in {"bearish_rejection", "bullish_rejection"} \
                    and not LIQ_DISPLACEMENT_OPTIONAL_FOR_WATCH:
                # Wick rejection alone may not satisfy displacement-required-for-entry
                # if optional-for-watch is also disabled. (Default: optional_for_watch
                # = True, so we typically accept it.)
                rejected.append(self._reject(direction, level, label, LiqRejection.NO_DISPLACEMENT_AFTER_SWEEP))
                continue
            funnel.inc("displacement_confirmed")

            entry = float(recent.iloc[-1]["close"])
            if direction == "SELL":
                sl = round(extreme + SL_BUFFER_PIPS * PIP, 2)
            else:
                sl = round(extreme - SL_BUFFER_PIPS * PIP, 2)
            ok, why = validate_sl_direction(direction, entry, sl)
            if not ok:
                funnel.inc("rejected_sl_invalid")
                rejected.append(self._reject(direction, level, label, LiqRejection.SL_INVALID, extra=why))
                continue
            tp1, tp2, tp3 = _build_tp_ladder(direction, entry, sl, level_pool)
            rr_check = check_risk_reward(direction, entry, sl, [tp1, tp2, tp3])
            if not rr_check.get("tp1_ok", False):
                funnel.inc("rejected_rr_invalid")
                rejected.append(
                    self._reject(direction, level, label, LiqRejection.RR_INVALID,
                                 extra=f"tp1_rr={rr_check.get('rr_values', [0])[0]:.2f}")
                )
                continue
            funnel.inc("risk_valid")
            setup = StrategySetup(
                strategy_type=self.strategy_type,
                symbol=symbol,
                direction=direction,
                entry_zone_low=min(level, extreme) - PIP,
                entry_zone_high=max(level, extreme) + PIP,
                trigger_level=level,
                confirmation_required=[conf],
                entry=round(entry, 2),
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                tp3=tp3,
                invalidation=round(extreme + (2 * PIP if direction == "SELL" else -2 * PIP), 2),
                reason=(
                    f"{'Buy-side' if direction == 'SELL' else 'Sell-side'} liquidity sweep at "
                    f"{label} ({level:.2f}); reversal confirmed by {conf}"
                ),
                confidence_internal=0.0,
                liquidity_score=80.0,
                session_name=getattr(ctx, "session_name", "") if ctx else "",
                higher_tf="H1",
                lower_tf="M15",
                confirmation_candle_time=self._candle_time(recent.iloc[-1]),
            )
            attach_level_intel_evidence(setup, self.level_intel, current_price=current_price)
            candidates.append(setup)

        return candidates, rejected, funnel

    def _reject(
        self, direction: str, level: float, label: str, reason: str, *, extra: str = ""
    ) -> RejectedCandidate:
        return RejectedCandidate(
            strategy_type=self.strategy_type,
            direction=direction,
            reason=reason,
            level=float(level),
            detail=f"{label} {extra}".strip(),
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
