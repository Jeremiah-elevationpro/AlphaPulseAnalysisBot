from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any

from analysis.sl_engine import validate_sl_direction
from config.settings import (
    ALLOW_THIRD_TRADE_IF_ELITE,
    ANALYST_ALLOWED_CONFIRMATIONS,
    ANALYST_ALLOWED_PRIMARY_ENTRIES,
    ANALYST_MIN_CONFIRMATION_GRADE,
    ANALYST_MIN_LEARNING_SCORE,
    ANALYST_PRIMARY_MIN_GRADE,
    ANALYST_QUALITY_GATE_ENABLED,
    ANALYST_SECONDARY_ENTRIES_REQUIRE_A_PLUS,
    ANALYST_SECONDARY_MIN_GRADE,
    ANALYST_SECONDARY_REQUIRE_STRUCTURE_FLIP,
    ANALYST_STRUCTURE_SHIFT_ALONE_ALLOWED,
    ELITE_TRADE_MIN_SCORE,
    MAX_ANALYST_ENTRIES_PER_DAY,
    MAX_ANALYST_ENTRIES_PER_SESSION,
    MAX_ANALYST_ENTRIES_PER_ZONE_PER_DAY,
    RISK_QUALITY_GATE_ENABLED,
    VERY_WIDE_SL_MIN_CANDIDATE_SCORE,
    VERY_WIDE_SL_MIN_TP1_RR,
    WIDE_SL_ALLOWED_CONFIRMATIONS,
    WIDE_SL_MIN_CANDIDATE_SCORE,
    WIDE_SL_MIN_TP1_RR,
    WIDE_SL_REQUIRE_H4_H1_ALIGNMENT,
    WIDE_SL_RESTRICT_CONFIRMATIONS,
    XAUUSD_VERY_WIDE_SL_THRESHOLD_PIPS,
    XAUUSD_WIDE_SL_THRESHOLD_PIPS,
)
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class DecisionResult:
    action: str
    reason: str
    priority: int
    alert_type: str
    event_key: str
    cooldown_status: str
    no_chase_status: str
    memory_update_required: bool
    setup_payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DecisionEngine:
    _GRADE_ORDER = {"REJECT": 0, "B": 1, "A": 2, "A+": 3}
    _CONFIRMATION_TYPE_BONUS = {
        "break_retest_close_confirmation": 25,
        "sweep_reclaim_confirmation": 25,
        "engulfing_level_confirmation": 20,
        "failed_retest_confirmation": 10,
        "structure_shift_confirmation": 0,
        "displacement_confirmation": 0,
    }

    def __init__(self):
        self._per_day_entries: dict[str, dict[str, int]] = {}
        self._per_day_losses: dict[str, dict[str, int]] = {}
        self._per_session_entries: dict[str, dict[str, dict[str, int]]] = {}
        self._per_zone_entries: dict[str, dict[str, dict[str, int]]] = {}

    def _grade_meets(self, actual: str, minimum: str) -> bool:
        return self._GRADE_ORDER.get(str(actual).upper(), -1) >= self._GRADE_ORDER.get(str(minimum).upper(), -1)

    def record_entry(self, *, symbol: str, date_key: str, session_name: str, zone_key: str) -> None:
        self._per_day_entries.setdefault(symbol, {})
        self._per_day_entries[symbol][date_key] = self._per_day_entries[symbol].get(date_key, 0) + 1
        self._per_session_entries.setdefault(symbol, {}).setdefault(date_key, {})
        self._per_session_entries[symbol][date_key][session_name] = self._per_session_entries[symbol][date_key].get(session_name, 0) + 1
        self._per_zone_entries.setdefault(symbol, {}).setdefault(date_key, {})
        self._per_zone_entries[symbol][date_key][zone_key] = self._per_zone_entries[symbol][date_key].get(zone_key, 0) + 1

    def record_loss(self, *, symbol: str, date_key: str) -> None:
        self._per_day_losses.setdefault(symbol, {})
        self._per_day_losses[symbol][date_key] = self._per_day_losses[symbol].get(date_key, 0) + 1

    def rank_candidate(self, market_plan, confirmation, learning_score, *, gate_context: dict[str, Any] | None = None) -> tuple[float, list[str]]:
        gate_context = gate_context or {}
        reasons: list[str] = []
        score = 0.0

        grade = str(getattr(confirmation, "confirmation_grade", getattr(confirmation, "grade", "Reject"))).upper()
        confirmation_type = str(getattr(confirmation, "confirmation_type", ""))
        scenario_type = str(getattr(confirmation, "scenario", "primary"))
        direction = str(getattr(confirmation, "direction", "")).upper()
        session_name = str(gate_context.get("session_name", "unknown"))
        bias = str(getattr(market_plan, "dominant_bias", "neutral")).lower()
        bias_strength = str(getattr(market_plan, "bias_strength", "weak")).lower()

        if grade == "A+":
            score += 30
            reasons.append("grade_a_plus")
        elif grade == "A":
            score += 20
            reasons.append("grade_a")

        score += self._CONFIRMATION_TYPE_BONUS.get(confirmation_type, 0)
        reasons.append(f"confirmation={confirmation_type}")

        if scenario_type == "primary":
            score += 20
            reasons.append("primary")
        elif confirmation_type in {"break_retest_close_confirmation", "sweep_reclaim_confirmation"}:
            score += 15
            reasons.append("secondary_structure_flip")
        else:
            score += 5
            reasons.append("secondary")

        if (direction == "BUY" and bias == "bullish") or (direction == "SELL" and bias == "bearish"):
            score += 15
            reasons.append("h4_h1_aligned")
            if bias_strength == "strong":
                score += 10
                reasons.append("strong_bias")
        elif bias in {"mixed", "neutral"}:
            score -= 10
            reasons.append("mixed_or_neutral_bias")

        entry = float(getattr(confirmation, "suggested_entry", 0.0) or 0.0)
        sl = float(getattr(confirmation, "suggested_sl", 0.0) or 0.0)
        tp1 = float(getattr(confirmation, "suggested_tps", {}).get("tp1", 0.0) or 0.0)
        tp2 = float(getattr(confirmation, "suggested_tps", {}).get("tp2", 0.0) or 0.0)
        risk = abs(entry - sl)
        tp1_rr = abs(tp1 - entry) / risk if risk > 0 else 0.0
        tp2_rr = abs(tp2 - entry) / risk if risk > 0 else 0.0
        if tp1_rr >= 1.0:
            score += 10
            reasons.append("tp1_ge_1r")
        else:
            score -= 20
            reasons.append("weak_rr")
        if tp2_rr >= 1.5:
            score += 5
            reasons.append("tp2_ge_1_5r")

        if session_name in {"new_york", "london"}:
            score += 10
            reasons.append(f"session={session_name}")
        elif session_name == "off_session":
            score -= 5
            reasons.append("off_session")

        action = str(getattr(learning_score, "recommended_action", "allow"))
        if action == "boost":
            score += 15
            reasons.append("learning_boost")
        elif action == "allow":
            score += 5
            reasons.append("learning_allow")
        elif action == "caution":
            score -= 5
            reasons.append("learning_caution")

        logger.info("CANDIDATE RANKED: score=%.1f reason=%s", score, ", ".join(reasons))
        return score, reasons

    def decide(self, market_plan, confirmation, learning_score, *, duplicate_blocked: bool = False, stale_reason: str = "", gate_context: dict[str, Any] | None = None) -> DecisionResult:
        gate_context = gate_context or {}
        direction = str(getattr(confirmation, "direction", "")).upper() if confirmation is not None else ""
        grade = str(getattr(confirmation, "confirmation_grade", getattr(confirmation, "grade", "Reject"))).upper() if confirmation is not None else "REJECT"
        scenario_type = str(getattr(confirmation, "scenario", "primary")) if confirmation is not None else "primary"
        symbol = str(gate_context.get("symbol", "XAUUSD"))
        session_name = str(gate_context.get("session_name", "unknown"))
        candle_time = str(gate_context.get("candle_time") or getattr(confirmation, "candle_time", ""))
        structure_flip_confirmed = bool(gate_context.get("structure_flip_confirmed", False))
        key_level_aligned = bool(gate_context.get("key_level_aligned", True))
        opposing_structure_distance = float(gate_context.get("opposing_structure_distance_pips", 999.0))
        close_away_distance = float(gate_context.get("close_away_distance_pips", 999.0))
        date_key = "unknown_date"
        if candle_time:
            try:
                date_key = datetime.fromisoformat(candle_time.replace("Z", "+00:00")).date().isoformat()
            except Exception:
                pass
        zone_key = str(gate_context.get("zone_key") or f"{direction}:{getattr(confirmation, 'zone_low', 0.0):.2f}-{getattr(confirmation, 'zone_high', 0.0):.2f}")

        rank_score = 0.0
        rank_reasons: list[str] = []
        if confirmation is not None:
            rank_score, rank_reasons = self.rank_candidate(market_plan, confirmation, learning_score, gate_context=gate_context)
        risk_pips = float(getattr(confirmation, "risk_pips", abs(float(getattr(confirmation, "suggested_entry", 0.0) or 0.0) - float(getattr(confirmation, "suggested_sl", 0.0) or 0.0))) or 0.0) if confirmation is not None else 0.0
        tp1_rr = float(getattr(confirmation, "tp1_rr", 0.0) or 0.0) if confirmation is not None else 0.0
        bias = str(getattr(market_plan, "dominant_bias", "neutral")).lower()
        bias_strength = str(getattr(market_plan, "bias_strength", "weak")).lower()
        h4_h1_aligned = (direction == "BUY" and bias == "bullish") or (direction == "SELL" and bias == "bearish")

        setup_payload = {
            "market_plan_status": getattr(market_plan, "plan_status", "fresh"),
            "learning_score": learning_score.to_dict() if hasattr(learning_score, "to_dict") else {},
            "candidate_rank_score": round(rank_score, 2),
            "candidate_rank_reason": ", ".join(rank_reasons),
            "risk_pips": round(risk_pips, 2),
            "tp1_rr": round(tp1_rr, 2),
        }

        if getattr(market_plan, "plan_status", "fresh") in {"played_out", "invalidated", "stale"}:
            action = "mark_stale" if getattr(market_plan, "plan_status", "") == "stale" else "mark_played_out"
            reason = f"scenario {market_plan.plan_status}"
            logger.info("DECISION CANCEL: reason=%s", reason)
            return DecisionResult(action, reason, 10, "scenario_update", "", "n/a", market_plan.plan_status, False, setup_payload)

        if stale_reason:
            logger.info("DECISION IGNORE: reason=%s", stale_reason)
            return DecisionResult("ignore", stale_reason, 5, "none", "", "n/a", "stale", False, setup_payload)

        if confirmation is None:
            logger.info("DECISION WAIT: reason=no_confirmation")
            return DecisionResult("wait", "no confirmation yet", 25, "none", "", "n/a", "fresh", False, setup_payload)

        if duplicate_blocked:
            logger.info("DECISION IGNORE: reason=duplicate")
            return DecisionResult("ignore", "duplicate confirmation", 1, "none", confirmation.confirmation_signature, "duplicate", "fresh", False, setup_payload)

        if not bool(getattr(confirmation, "is_entry_valid", True)):
            logger.info("DECISION IGNORE: reason=%s", getattr(confirmation, "rejection_reason", "invalid_setup"))
            return DecisionResult("ignore", str(getattr(confirmation, "rejection_reason", "invalid_setup")), 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

        if grade not in {"A", "A+"} or not self._grade_meets(grade, ANALYST_MIN_CONFIRMATION_GRADE):
            logger.info("DECISION WAIT: reason=grade_b_or_lower")
            return DecisionResult("wait", "weak_confirmation", 40, "scenario_update", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

        is_valid_sl, sl_reason = validate_sl_direction(direction, float(getattr(confirmation, "suggested_entry", 0.0)), float(getattr(confirmation, "suggested_sl", 0.0)))
        if not is_valid_sl:
            logger.info("DECISION IGNORE: reason=invalid_sl_direction")
            return DecisionResult("ignore", sl_reason, 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

        if learning_score.recommended_action == "block":
            logger.info("DECISION IGNORE: reason=learning_blocked")
            return DecisionResult("ignore", "learning blocked setup", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

        if ANALYST_QUALITY_GATE_ENABLED:
            confirmation_type = str(getattr(confirmation, "confirmation_type", ""))
            if confirmation_type == "structure_shift_confirmation" and not ANALYST_STRUCTURE_SHIFT_ALONE_ALLOWED:
                logger.info("ENTRY BLOCKED: structure_shift_alone_not_allowed")
                return DecisionResult("ignore", "structure_shift_alone_not_allowed", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
            if confirmation_type == "displacement_confirmation":
                logger.info("ENTRY BLOCKED: displacement_alone_not_allowed")
                return DecisionResult("ignore", "displacement_alone_not_allowed", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
            if confirmation_type not in ANALYST_ALLOWED_CONFIRMATIONS:
                logger.info("DECISION IGNORE: reason=confirmation_not_allowed")
                return DecisionResult("ignore", "weak_confirmation", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
            if learning_score.final_score < ANALYST_MIN_LEARNING_SCORE:
                logger.info("DECISION IGNORE: reason=low_learning_score")
                return DecisionResult("ignore", "low_learning_score", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

            if confirmation_type == "failed_retest_confirmation":
                if scenario_type != "primary":
                    logger.info("FAILED RETEST BLOCKED: secondary_not_allowed")
                    return DecisionResult("ignore", "secondary_requires_a_plus", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if grade != "A+":
                    logger.info("FAILED RETEST BLOCKED: weak_grade")
                    return DecisionResult("ignore", "weak_confirmation", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if close_away_distance < 5.0:
                    logger.info("FAILED RETEST BLOCKED: weak_close_away")
                    return DecisionResult("ignore", "weak_close_away", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if opposing_structure_distance <= 15.0:
                    logger.info("FAILED RETEST BLOCKED: opposing_structure_too_close")
                    return DecisionResult("ignore", "opposing_structure_too_close", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if rank_score < 75.0:
                    logger.info("FAILED RETEST BLOCKED: weak_rr")
                    return DecisionResult("ignore", "weak_rr", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

            if confirmation_type == "sweep_reclaim_confirmation" and not key_level_aligned:
                logger.info("ENTRY BLOCKED: sweep_requires_key_level")
                return DecisionResult("ignore", "weak_confirmation", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

            if RISK_QUALITY_GATE_ENABLED and risk_pips > XAUUSD_WIDE_SL_THRESHOLD_PIPS:
                strong_alignment = h4_h1_aligned and bias_strength == "strong"
                if confirmation_type == "sweep_reclaim_confirmation":
                    if rank_score < VERY_WIDE_SL_MIN_CANDIDATE_SCORE or tp1_rr < VERY_WIDE_SL_MIN_TP1_RR:
                        logger.info("ENTRY BLOCKED: sweep_wide_sl_blocked")
                        return DecisionResult("ignore", "sweep_wide_sl_blocked", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if confirmation_type == "failed_retest_confirmation":
                    if rank_score < VERY_WIDE_SL_MIN_CANDIDATE_SCORE or tp1_rr < VERY_WIDE_SL_MIN_TP1_RR or not strong_alignment or close_away_distance < 5.0:
                        logger.info("ENTRY BLOCKED: failed_retest_wide_sl_blocked")
                        return DecisionResult("ignore", "failed_retest_wide_sl_blocked", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if risk_pips > XAUUSD_VERY_WIDE_SL_THRESHOLD_PIPS:
                    if confirmation_type not in {"break_retest_close_confirmation", "engulfing_level_confirmation"}:
                        logger.info("ENTRY BLOCKED: very_wide_sl_quality_block")
                        return DecisionResult("ignore", "very_wide_sl_quality_block", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                    if rank_score < VERY_WIDE_SL_MIN_CANDIDATE_SCORE or tp1_rr < VERY_WIDE_SL_MIN_TP1_RR or not strong_alignment:
                        logger.info("ENTRY BLOCKED: very_wide_sl_quality_block")
                        return DecisionResult("ignore", "very_wide_sl_quality_block", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                else:
                    if confirmation_type in WIDE_SL_RESTRICT_CONFIRMATIONS:
                        if rank_score < VERY_WIDE_SL_MIN_CANDIDATE_SCORE or tp1_rr < VERY_WIDE_SL_MIN_TP1_RR:
                            reason = "sweep_wide_sl_blocked" if confirmation_type == "sweep_reclaim_confirmation" else "failed_retest_wide_sl_blocked"
                            logger.info("ENTRY BLOCKED: %s", reason)
                            return DecisionResult("ignore", reason, 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                    if confirmation_type not in WIDE_SL_ALLOWED_CONFIRMATIONS:
                        logger.info("ENTRY BLOCKED: wide_sl_quality_block")
                        return DecisionResult("ignore", "wide_sl_quality_block", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                    if rank_score < WIDE_SL_MIN_CANDIDATE_SCORE or tp1_rr < WIDE_SL_MIN_TP1_RR:
                        logger.info("ENTRY BLOCKED: wide_sl_quality_block")
                        return DecisionResult("ignore", "wide_sl_quality_block", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                    if WIDE_SL_REQUIRE_H4_H1_ALIGNMENT and not h4_h1_aligned:
                        logger.info("ENTRY BLOCKED: wide_sl_quality_block")
                        return DecisionResult("ignore", "wide_sl_quality_block", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)

            if scenario_type == "primary":
                if not ANALYST_ALLOWED_PRIMARY_ENTRIES:
                    return DecisionResult("ignore", "primary_entries_disabled", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if not self._grade_meets(grade, ANALYST_PRIMARY_MIN_GRADE):
                    return DecisionResult("wait", "weak_confirmation", 40, "scenario_update", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
            else:
                if confirmation_type not in {"break_retest_close_confirmation", "sweep_reclaim_confirmation"}:
                    logger.info("SECONDARY ENTRY BLOCKED: not_high_quality_flip")
                    return DecisionResult("wait", "not_high_quality_flip", 20, "scenario_update", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if ANALYST_SECONDARY_ENTRIES_REQUIRE_A_PLUS and not self._grade_meets(grade, ANALYST_SECONDARY_MIN_GRADE):
                    logger.info("SECONDARY ENTRY BLOCKED: requires_a_plus_structure_flip")
                    return DecisionResult("wait", "secondary_requires_a_plus", 20, "scenario_update", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if ANALYST_SECONDARY_REQUIRE_STRUCTURE_FLIP and not structure_flip_confirmed:
                    logger.info("SECONDARY ENTRY BLOCKED: not_high_quality_flip")
                    return DecisionResult("wait", "not_high_quality_flip", 20, "scenario_update", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                if learning_score.final_score < 85.0 or rank_score < 85.0:
                    logger.info("SECONDARY ENTRY BLOCKED: not_high_quality_flip")
                    return DecisionResult("ignore", "not_high_quality_flip", 1, "none", confirmation.confirmation_signature, "n/a", "fresh", False, setup_payload)
                logger.info("SECONDARY ENTRY ALLOWED: high_quality_flip")

            day_count = self._per_day_entries.get(symbol, {}).get(date_key, 0)
            if day_count >= MAX_ANALYST_ENTRIES_PER_DAY:
                if ALLOW_THIRD_TRADE_IF_ELITE and day_count == MAX_ANALYST_ENTRIES_PER_DAY and rank_score >= ELITE_TRADE_MIN_SCORE and learning_score.recommended_action == "boost" and self._per_day_losses.get(symbol, {}).get(date_key, 0) == 0:
                    logger.info("THIRD TRADE ALLOWED: elite_setup")
                else:
                    logger.info("ENTRY BLOCKED: daily_limit_reached")
                    return DecisionResult("ignore", "daily_limit_reached", 1, "none", confirmation.confirmation_signature, "cooldown", "fresh", False, setup_payload)
            if self._per_session_entries.get(symbol, {}).get(date_key, {}).get(session_name, 0) >= MAX_ANALYST_ENTRIES_PER_SESSION:
                logger.info("ENTRY BLOCKED: session_limit_reached")
                return DecisionResult("ignore", "session_limit_reached", 1, "none", confirmation.confirmation_signature, "cooldown", "fresh", False, setup_payload)
            if self._per_zone_entries.get(symbol, {}).get(date_key, {}).get(zone_key, 0) >= MAX_ANALYST_ENTRIES_PER_ZONE_PER_DAY:
                logger.info("ENTRY BLOCKED: zone_already_traded_today")
                return DecisionResult("ignore", "zone_already_traded_today", 1, "none", confirmation.confirmation_signature, "cooldown", "fresh", False, setup_payload)

        logger.info("DECISION ENTRY_ALERT: reason=graded_confirmation_allowed")
        return DecisionResult(
            "send_entry_alert",
            "A/A+ confirmation with learning approval",
            int(round(rank_score)),
            "analyst_entry",
            confirmation.confirmation_signature,
            "ready",
            "fresh",
            True,
            setup_payload,
        )
