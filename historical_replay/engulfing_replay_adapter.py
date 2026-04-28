"""
AlphaPulse — Engulfing Replay Adapter
======================================
Converts an engulfing SetupResult into a Trade object suitable for
candle-by-candle replay activation/closure simulation.

Unlike the live SignalGenerator (which enforces fixed SL limits that are too
tight for the engulfing zone-based SL), this adapter:
  - Enforces a minimum SL of MIN_SL_PIPS from entry (pads zone SL if needed)
  - Caps SL at MAX_SL_PIPS from entry
  - Only activates for H1 / M30 timeframes
  - Requires quality_rejection_count >= 3
  - Validates minimum RR before accepting
"""

from __future__ import annotations

from typing import Optional

from config.settings import (
    ENGULF_ALLOWED_LIVE_TIMEFRAMES,
    MAX_SL_PIPS,
    MIN_RR_RATIO,
    MIN_SL_PIPS,
    PIP_SIZE,
    SYMBOL,
    TP_PIPS,
)
from db.models import Trade, TradeStatus
from utils.logger import get_logger

logger = get_logger(__name__)

_ENGULF_REPLAY_MIN_QUALITY_REJECTIONS = 3


class EngulfingReplayAdapter:
    """Mirrors SignalGenerator.generate() for engulfing signals in replay context."""

    def generate(self, setup) -> tuple[Optional[Trade], str]:
        """
        Build a Trade from an engulfing SetupResult using replay-appropriate SL.

        Returns:
            (Trade, "")              — success
            (None, rejection_reason) — validation failed
        """
        tf = getattr(setup, "lower_tf", "") or ""
        if tf not in ENGULF_ALLOWED_LIVE_TIMEFRAMES:
            reason = f"Engulfing replay: timeframe {tf!r} not in {ENGULF_ALLOWED_LIVE_TIMEFRAMES}"
            logger.debug("ENGULFING_REPLAY REJECT: %s", reason)
            return None, reason

        q_count = int(getattr(setup, "quality_rejection_count", 0) or 0)
        if q_count < _ENGULF_REPLAY_MIN_QUALITY_REJECTIONS:
            reason = f"Engulfing replay: quality_rejection_count={q_count} < {_ENGULF_REPLAY_MIN_QUALITY_REJECTIONS}"
            logger.debug("ENGULFING_REPLAY REJECT: %s", reason)
            return None, reason

        conf = getattr(setup, "confirmation", None)
        if conf is None:
            return None, "Engulfing replay: no confirmation object"

        entry = float(getattr(conf, "entry_price", 0.0) or 0.0)
        sl_zone = float(getattr(conf, "sl_price", 0.0) or 0.0)
        direction = (getattr(setup, "direction", "") or "").upper()
        if not entry or not sl_zone or direction not in ("BUY", "SELL"):
            return None, f"Engulfing replay: invalid entry={entry} sl={sl_zone} dir={direction}"

        min_dist = MIN_SL_PIPS * PIP_SIZE
        max_dist = MAX_SL_PIPS * PIP_SIZE
        zone_dist = abs(entry - sl_zone)

        if direction == "BUY":
            # SL below entry — take the furthest (most protective) SL
            effective_dist = max(zone_dist, min_dist)
            effective_dist = min(effective_dist, max_dist)
            sl = round(entry - effective_dist, 2)
        else:
            effective_dist = max(zone_dist, min_dist)
            effective_dist = min(effective_dist, max_dist)
            sl = round(entry + effective_dist, 2)

        sl_dist = abs(entry - sl)

        sign = 1 if direction == "BUY" else -1
        tp_levels = [round(entry + sign * pips * PIP_SIZE, 2) for pips in TP_PIPS]

        tp1_dist = abs(tp_levels[0] - entry)
        rr = tp1_dist / sl_dist if sl_dist > 0 else 0.0
        if rr < MIN_RR_RATIO:
            reason = f"Engulfing replay: RR={rr:.2f} < {MIN_RR_RATIO} | sl_dist={sl_dist/PIP_SIZE:.1f}p"
            logger.debug("ENGULFING_REPLAY REJECT: %s", reason)
            return None, reason

        level = getattr(setup, "level", None)
        trade = Trade(
            direction=direction,
            entry_price=round(entry, 2),
            sl_price=sl,
            tp_levels=tp_levels,
            level_type=getattr(level, "level_type", "Gap") if level else "Gap",
            level_price=getattr(level, "price", entry) if level else entry,
            higher_tf=getattr(setup, "higher_tf", tf),
            lower_tf=tf,
            confidence=float(getattr(setup, "confidence", 0.7) or 0.7),
            pair=getattr(setup, "pair", SYMBOL),
            status=TradeStatus.PENDING,
            setup_type=getattr(setup, "setup_type", "engulfing_live"),
            is_qm=bool(getattr(setup, "is_qm", False)),
            is_psychological=bool(getattr(setup, "is_psychological", False)),
            is_liquidity_sweep=bool(getattr(setup, "is_liquidity_sweep", False)),
            session_name=getattr(setup, "session_name", "") or "",
            h4_bias=getattr(setup, "h4_bias", "neutral") or "neutral",
            trend_aligned=bool(getattr(setup, "trend_aligned", True)),
            confirmation_type=getattr(conf, "confirmation_type", "engulfing_live") or "engulfing_live",
            micro_confirmation_type=getattr(setup, "micro_confirmation_type", "") or "",
            bias_gate_result=getattr(setup, "bias_gate_result", "") or "",
            pd_location=getattr(setup, "pd_location", "") or "",
            high_quality_trade=bool(getattr(setup, "high_quality_trade", False)),
            micro_strength=getattr(setup, "micro_strength", "normal") or "normal",
            strategy_type="engulfing_rejection",
            source=getattr(setup, "source", "replay") or "replay",
            dominant_bias=getattr(setup, "dominant_bias", None) or getattr(setup, "h4_bias", "neutral"),
            bias_strength=getattr(setup, "bias_strength", "weak") or "weak",
            confirmation_score=float(getattr(setup, "confirmation_score", 0.0) or 0.0),
            confirmation_path=getattr(setup, "confirmation_path", "") or "",
            quality_rejection_count=q_count,
            structure_break_count=int(getattr(setup, "structure_break_count", 0) or 0),
            level_timeframe=getattr(level, "timeframe", tf) if level else tf,
            confluence_with=list(getattr(setup, "confluence_with", []) or []),
        )

        logger.info(
            "ENGULFING_REPLAY ACCEPTED: %s %s | entry=%.2f | sl=%.2f (%.0fp) | "
            "tp1=%.2f | RR=%.2f | tf=%s | q=%d",
            direction, trade.pair, entry, sl,
            effective_dist / PIP_SIZE, tp_levels[0], rr, tf, q_count,
        )
        return trade, ""
