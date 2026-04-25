"""
AlphaPulse - Signal Generator
===============================
Validates confirmed SetupResults / LSDSignals and produces Trade objects.

Gates still enforced here:
  - Counter-trend: blocked entirely (requires H4 bias alignment)
  - SL distance: 15-30 pips (default) / 15-80 pips (LSD swing)
  - Minimum RR: 1.3x (default) / 2.0x (LSD)
  - TP clearance: TP1 adjusted if a structural level blocks the path
  - Strategy performance multiplier: scales confidence, may block weak strategies

Session gate removed from this layer — handled in main.py for manual execution.
"""

from __future__ import annotations

from typing import List, Optional

from strategies.multi_timeframe import SetupResult
from strategies.liquidity_sweep_displacement import LSDSignal
from db.models import Trade, TradeStatus
from config.settings import (
    TP_PIPS, MAX_SL_PIPS, MIN_SL_PIPS, MIN_RR_RATIO, PIP_SIZE,
    MIN_TP_CLEARANCE_PIPS, MIN_SIGNAL_CONFIDENCE, MIN_TRADES_FOR_LEARNING,
    ACTIVE_TIMEFRAME_PAIR_LABELS,
    BIAS_STRONG_BLOCK_COUNTER_TREND,
    SESSION_TP1_MIN_PIPS,
    ENGULF_ALLOWED_LIVE_TIMEFRAMES,
)
from utils.helpers import price_to_pips
from utils.logger import get_logger

logger = get_logger(__name__)


class SignalGenerator:
    """
    Takes a confirmed SetupResult and produces a fully validated Trade object.

    Entry model (double-touch / limit order):
      - First touch  → rejection candle confirmed by ConfirmationEngine
      - Second touch → LIMIT order at the level price (entry_price == level.price)

    TP targets are fixed pip distances from entry — not multipliers.
    This enforces disciplined, predictable risk management.
    """

    def __init__(self, learning_engine=None):
        self._learning = learning_engine

    def generate(self, setup: SetupResult) -> tuple[Optional[Trade], str]:
        """
        Build and validate a Trade from a SetupResult.

        Returns:
            (Trade, "")              — success
            (None, rejection_reason) — validation failed
        """
        conf  = setup.confirmation
        entry = conf.entry_price
        sl    = conf.sl_price
        tf_pair = f"{setup.higher_tf}-{setup.lower_tf}"
        strategy_name = getattr(setup, "strategy_type", "gap_sweep") or "gap_sweep"

        if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS and not (
            strategy_name == "engulfing_rejection" and setup.lower_tf in ENGULF_ALLOWED_LIVE_TIMEFRAMES
        ):
            reason = f"Timeframe pair disabled: {tf_pair}"
            logger.info("Signal rejected - %s", reason)
            return None, reason

        # ── 1. Counter-trend gate — blocked entirely ──────────────────────────
        # Session check is handled (leniently) in main.py for manual execution.
        if (
            not setup.trend_aligned
            and getattr(setup, "bias_strength", "weak") == "strong"
            and BIAS_STRONG_BLOCK_COUNTER_TREND
        ):
            reason = (
                f"Strong-bias counter-trend blocked "
                f"({setup.direction} vs {setup.h4_bias}/{setup.bias_strength} bias)"
            )
            logger.info("Signal rejected — %s", reason)
            return None, reason

        # ── 2. Validate SL distance ───────────────────────────────────────────
        sl_dist = abs(entry - sl)
        sl_pips = price_to_pips(sl_dist)

        if sl_pips > MAX_SL_PIPS:
            reason = f"SL too wide: {sl_pips:.0f} pips (max {MAX_SL_PIPS})"
            logger.warning("Signal rejected — %s", reason)
            return None, reason

        if sl_pips < MIN_SL_PIPS:
            reason = f"SL too tight: {sl_pips:.0f} pips (min {MIN_SL_PIPS})"
            logger.warning("Signal rejected — %s", reason)
            return None, reason

        # ── 3. Build fixed TP levels ──────────────────────────────────────────
        sign = 1 if setup.direction == "BUY" else -1
        tp_levels = [round(entry + sign * pips * PIP_SIZE, 2) for pips in TP_PIPS]

        # ── 4. TP clearance — check for blocking structural level ─────────────
        # Filter 5: if any opposing structural level sits between entry and TP1,
        # adjust TP1 to that level (trade to the nearest obstacle, not through it).
        # If the adjusted TP1 still gives adequate RR, continue; otherwise reject.
        tp_levels, tp1_adjusted_note = self._apply_tp_clearance(
            entry, tp_levels, setup.opposing_levels, sign, setup.session_name
        )

        # ── 5. Validate minimum RR for TP1 ───────────────────────────────────
        tp1_dist = abs(tp_levels[0] - entry)
        tp1_pips = price_to_pips(tp1_dist)
        required_tp1 = self._session_tp1_min(setup.session_name)
        if tp1_pips < required_tp1:
            reason = f"TP1 room too tight for {setup.session_name}: {tp1_pips:.0f}p < {required_tp1:.0f}p"
            logger.info(
                "TP1 MIN CHECK: session=%s | required=%.0fp | actual=%.0fp -> REJECT",
                setup.session_name or "off_session", required_tp1, tp1_pips,
            )
            logger.warning("Signal rejected - %s", reason)
            return None, reason
        logger.info(
            "TP1 MIN CHECK: session=%s | required=%.0fp | actual=%.0fp -> PASS",
            setup.session_name or "off_session", required_tp1, tp1_pips,
        )
        rr = tp1_dist / sl_dist if sl_dist > 0 else 0.0
        if rr < MIN_RR_RATIO:
            reason = (
                f"RR too low after TP clearance: {rr:.2f}x (min {MIN_RR_RATIO}x)"
                + (f" — {tp1_adjusted_note}" if tp1_adjusted_note else "")
            )
            logger.warning("Signal rejected — %s", reason)
            return None, reason

        # ── 6. Get confirmation type from confirmation result ─────────────────
        confirmation_type = getattr(conf, "confirmation_type", "rejection")

        # ── 7. Get confidence score ───────────────────────────────────────────
        confidence = self._get_confidence(setup)

        # ── 7b. Apply strategy performance multiplier ────────────────────────
        confidence, strategy_skip = self._apply_strategy_score(confidence, strategy_name)
        if strategy_skip:
            return None, strategy_skip

        # ── 7c. Apply historical rank multiplier ─────────────────────────────
        confidence = self._apply_rank(
            confidence,
            session=setup.session_name,
            h4_bias=setup.h4_bias,
            direction=setup.direction,
            setup_type=setup.setup_type,
            confirmation_type=confirmation_type,
        )

        # ── 8. Build Trade object ─────────────────────────────────────────────
        trade = Trade(
            direction=setup.direction,
            entry_price=round(entry, 2),
            sl_price=round(sl, 2),
            tp_levels=tp_levels,
            level_type=setup.level.level_type,
            level_price=setup.level.price,
            higher_tf=setup.higher_tf,
            lower_tf=setup.lower_tf,
            confidence=confidence,
            pair=setup.pair,
            status=TradeStatus.PENDING,
            setup_type=setup.setup_type,
            is_qm=setup.is_qm,
            is_psychological=setup.is_psychological,
            is_liquidity_sweep=setup.is_liquidity_sweep,
            session_name=setup.session_name,
            h4_bias=setup.h4_bias,
            trend_aligned=setup.trend_aligned,
            confirmation_type=confirmation_type,
            micro_confirmation_type=getattr(setup, "micro_confirmation_type", ""),
            bias_gate_result=getattr(setup, "bias_gate_result", ""),
            pd_location=getattr(setup, "pd_location", ""),
            high_quality_trade=getattr(setup, "high_quality_trade", False),
            micro_strength=getattr(setup, "micro_strength", "normal"),
            strategy_type=strategy_name,
            source=getattr(setup, "source", "live_bot"),
            dominant_bias=getattr(setup, "dominant_bias", setup.h4_bias),
            bias_strength=getattr(setup, "bias_strength", "weak"),
            confirmation_score=float(getattr(setup, "confirmation_score", 0.0) or 0.0),
            confirmation_path=getattr(setup, "confirmation_path", ""),
            quality_rejection_count=int(getattr(setup, "quality_rejection_count", 0) or 0),
            structure_break_count=int(getattr(setup, "structure_break_count", 0) or 0),
            level_timeframe=getattr(setup.level, "timeframe", setup.lower_tf),
            confluence_with=list(getattr(setup, "confluence_with", []) or []),
        )

        tp1_pips = price_to_pips(tp1_dist)
        logger.info(
            "[%s] Signal generated: %s %s | Entry %.2f | SL %.2f (%.0fpips) | "
            "TP1 %.2f (+%.0fpips, %.1fR) | TF=%s | Conf %.0f%% | "
            "QM=%s Psych=%s Session=%s Bias=%s | ConfType=%s",
            setup.setup_type.upper(),
            trade.direction, trade.pair,
            trade.entry_price, trade.sl_price, sl_pips,
            trade.tp1, tp1_pips, rr,
            setup.lower_tf, confidence * 100,
            trade.is_qm, trade.is_psychological,
            trade.session_name, trade.h4_bias,
            trade.confirmation_type,
        )

        return trade, ""

    def generate_batch(self, setups: List[SetupResult]) -> List[Trade]:
        """Convenience wrapper — returns only successful trades."""
        trades = []
        for setup in setups:
            trade, _ = self.generate(setup)
            if trade:
                trades.append(trade)
        return trades

    def generate_lsd(self, signal: LSDSignal) -> tuple[Optional[Trade], str]:
        """
        Convert an LSDSignal (from LSDStrategy.analyze) into a Trade object.

        Validates:
          - Confidence meets MIN_SIGNAL_CONFIDENCE threshold
          - SL distance within allowed range (scalp) or max cap (swing)
          - Minimum RR satisfied

        Returns:
            (Trade, "")              — success
            (None, rejection_reason) — validation failed
        """
        from config.settings import LSD_SCALP_MIN_SL_PIPS, LSD_SCALP_MAX_SL_PIPS, LSD_SWING_MAX_SL_PIPS

        # ── 1. Apply strategy performance score ───────────────────────────────
        adjusted_conf, strategy_skip = self._apply_strategy_score(signal.confidence, "lsd")
        if strategy_skip:
            return None, strategy_skip

        # ── 2. Confidence gate (checked on adjusted confidence) ───────────────
        if adjusted_conf < MIN_SIGNAL_CONFIDENCE:
            reason = (
                f"LSD confidence too low: {adjusted_conf:.0%} "
                f"(min {MIN_SIGNAL_CONFIDENCE:.0%})"
            )
            logger.info("LSD signal rejected — %s", reason)
            return None, reason

        # ── 3. SL distance validation ─────────────────────────────────────────
        sl_pips = signal.sl_pips
        if signal.model == "LSD_SCALP":
            if sl_pips < LSD_SCALP_MIN_SL_PIPS:
                reason = f"LSD_SCALP SL too tight: {sl_pips:.0f} pips (min {LSD_SCALP_MIN_SL_PIPS})"
                logger.warning("LSD signal rejected — %s", reason)
                return None, reason
            if sl_pips > LSD_SCALP_MAX_SL_PIPS:
                reason = f"LSD_SCALP SL too wide: {sl_pips:.0f} pips (max {LSD_SCALP_MAX_SL_PIPS})"
                logger.warning("LSD signal rejected — %s", reason)
                return None, reason
        else:  # LSD_SWING
            if sl_pips > LSD_SWING_MAX_SL_PIPS:
                reason = f"LSD_SWING SL too wide: {sl_pips:.0f} pips (max {LSD_SWING_MAX_SL_PIPS})"
                logger.warning("LSD signal rejected — %s", reason)
                return None, reason

        # ── 4. RR validation ──────────────────────────────────────────────────
        from config.settings import LSD_MIN_RR
        if signal.rr < LSD_MIN_RR:
            reason = f"LSD RR too low: {signal.rr:.2f}x (min {LSD_MIN_RR}x)"
            logger.warning("LSD signal rejected — %s", reason)
            return None, reason

        # ── 5. Convert to Trade, stamp adjusted confidence ────────────────────
        trade = signal.to_trade()
        trade.confidence = adjusted_conf   # override with strategy-adjusted value

        logger.info(
            "[%s] LSD signal generated: %s %s | Entry %.2f | SL %.2f (%.0fpips) | "
            "TP1 %.2f (%.1fR) | TF=%s | Conf %.0f%% | Bias=%s | Session=%s",
            signal.model,
            trade.direction, trade.pair,
            trade.entry_price, trade.sl_price, sl_pips,
            trade.tp1, signal.rr,
            signal.timeframe, adjusted_conf * 100,
            signal.htf_bias, signal.session or "off-session",
        )

        return trade, ""

    def generate_lsd_batch(self, signals: List[LSDSignal]) -> List[Trade]:
        """Convenience wrapper for a list of LSDSignals — returns only successful trades."""
        trades = []
        for sig in signals:
            trade, _ = self.generate_lsd(sig)
            if trade:
                trades.append(trade)
        return trades

    # ─────────────────────────────────────────────────────
    # HISTORICAL RANK MULTIPLIER
    # ─────────────────────────────────────────────────────

    def _apply_rank(
        self,
        base_confidence: float,
        session: str,
        h4_bias: str,
        direction: str,
        setup_type: str,
        confirmation_type: str,
    ) -> float:
        """
        Apply SetupRanker multiplier (0.80–1.20) to confidence.

        When history is insufficient the ranker returns 1.0 — no change.
        Always clamps final confidence to [0.0, 1.0].
        """
        if self._learning is None:
            return base_confidence
        try:
            rank = self._learning.get_rank_result(
                session=session,
                h4_bias=h4_bias,
                direction=direction,
                setup_type=setup_type,
                confirmation_type=confirmation_type,
            )
            adjusted = round(min(1.0, base_confidence * rank.rank_score), 3)
            if rank.rank_score != 1.0:
                logger.debug(
                    "Rank multiplier ×%.2f → confidence %.0f%% (was %.0f%%) | %s",
                    rank.rank_score, adjusted * 100, base_confidence * 100, rank.note,
                )
            return adjusted
        except Exception as e:
            logger.debug("Rank lookup failed: %s", e)
            return base_confidence

    # ─────────────────────────────────────────────────────
    # FILTER 5: TP CLEARANCE
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _apply_tp_clearance(
        entry: float,
        tp_levels: list,
        opposing_levels: list,
        sign: int,            # +1 for BUY, -1 for SELL
        session_name: str = "",
    ) -> tuple[list, str]:
        """
        Filter 5 — TP Clearance:
        If any opposing structural level sits between entry and TP1 AND is
        closer than MIN_TP_CLEARANCE_PIPS from entry, cap TP1 at that level.

        This prevents the bot from projecting TP1 through a structural wall
        that will likely stop the trade dead before reaching target.

        Returns:
            (adjusted_tp_levels, note_string)
            note_string is "" when no adjustment was made.
        """
        if not opposing_levels:
            return tp_levels, ""

        tp1 = tp_levels[0]
        min_clearance = SignalGenerator._session_tp1_min(session_name) * PIP_SIZE

        # Find all opposing levels between entry and TP1
        if sign == 1:  # BUY — opposing levels are above entry, below TP1
            blocking = [
                l for l in opposing_levels
                if entry < l.price < tp1
            ]
        else:  # SELL — opposing levels are below entry, above TP1
            blocking = [
                l for l in opposing_levels
                if tp1 < l.price < entry
            ]

        if not blocking:
            return tp_levels, ""

        # Nearest blocker to entry
        nearest = min(blocking, key=lambda l: abs(l.price - entry))
        dist_to_blocker = abs(nearest.price - entry)

        if dist_to_blocker >= 0:
            # Blocker is too close — cap TP1 at the blocker price
            adjusted_tp1 = round(nearest.price, 2)
            note = (
                f"TP1 capped at blocking {nearest.level_type} level "
                f"{nearest.price:.2f} ({dist_to_blocker / PIP_SIZE:.0f} pips; "
                f"session min {min_clearance / PIP_SIZE:.0f}p)"
            )
            logger.info("TP clearance: %s", note)
            adjusted = [adjusted_tp1] + tp_levels[1:]
            return adjusted, note

        return tp_levels, ""

    # ─────────────────────────────────────────────────────
    # CONFIDENCE SCORING
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _session_tp1_min(session_name: str) -> float:
        return float(SESSION_TP1_MIN_PIPS.get(session_name or "off_session", MIN_TP_CLEARANCE_PIPS))

    def _get_confidence(self, setup: SetupResult) -> float:
        """Retrieve confidence from learning engine, fall back to setup score."""
        tf_pair = f"{setup.higher_tf}-{setup.lower_tf}"
        if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
            logger.info("Learning score skipped: disabled timeframe pair %s", tf_pair)
            return setup.confidence
        if self._learning is None:
            return setup.confidence
        try:
            score = self._learning.get_confidence(
                level_type=setup.level.level_type,
                tf_pair=tf_pair,
            )
            return round(score, 3)
        except Exception as e:
            logger.warning("Could not fetch confidence score: %s", e)
            return setup.confidence

    def _apply_strategy_score(
        self,
        base_confidence: float,
        strategy_name: str,
    ) -> tuple[float, str]:
        """
        Scale confidence by strategy historical performance.

        Rules:
          score < 0.45  → reject the signal entirely (return skip reason)
          score 0.45-1  → final = base × score
          score > 0.70  → additional 1.1× boost, capped at 1.0

        Returns:
            (adjusted_confidence, "")           — proceed
            (base_confidence, skip_reason_str)  — caller should reject signal
        """
        if self._learning is None:
            return base_confidence, ""

        try:
            score, n_trades = self._learning.get_strategy_score(strategy_name)
        except Exception as e:
            logger.debug("Strategy score unavailable for '%s': %s", strategy_name, e)
            return base_confidence, ""

        # Not enough history — pass through without adjustment to avoid penalising
        # strategies that haven't been seen enough times to produce a reliable score.
        if n_trades < MIN_TRADES_FOR_LEARNING:
            logger.debug(
                "Strategy '%s': only %d trade(s) — score multiplier skipped (need %d).",
                strategy_name, n_trades, MIN_TRADES_FOR_LEARNING,
            )
            return base_confidence, ""

        # Filter — strategy is underperforming, skip all its signals
        if score < 0.45:
            reason = (
                f"Strategy {strategy_name.upper()} skipped — "
                f"low performance ({score:.2f}, {n_trades} trades)"
            )
            logger.info(reason)
            return base_confidence, reason

        # Scale confidence by strategy score
        adjusted = base_confidence * score

        # Boost for high-performing strategies
        if score > 0.70:
            adjusted = min(1.0, adjusted * 1.1)

        logger.info(
            "SPENCER LEARNING WEIGHT APPLIED: strategy=%s | weight=%.2f | base=%.0f%% -> %.0f%%",
            strategy_name,
            score,
            base_confidence * 100,
            adjusted * 100,
        )

        return round(adjusted, 3), ""
