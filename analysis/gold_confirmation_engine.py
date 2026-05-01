from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Optional

import pandas as pd

from analysis.sl_engine import build_structure_sl
from analysis.tp_engine import build_structure_tps
from analysis.trade_path_engine import build_trade_path_plan, validate_final_trade_setup
from config.settings import CONFIRMATION_MAX_AGE_CANDLES, MAX_ENTRY_CHASE_DISTANCE_PIPS
from utils.logger import get_logger

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Setup quality label mapping
# ─────────────────────────────────────────────────────────────────────────────

def quality_label_from_score(score: float) -> str:
    """Map a candidate_rank_score to a human-readable setup quality label."""
    if score >= 100:
        label = "ELITE A+ SETUP"
    elif score >= 90:
        label = "ELITE SETUP"
    elif score >= 80:
        label = "HIGH-PROBABILITY SETUP"
    elif score >= 70:
        label = "VALID SETUP"
    elif score >= 60:
        label = "LOW-CONFIDENCE SETUP"
    else:
        label = "WATCHLIST ONLY"
    logger.debug("QUALITY LABEL ASSIGNED: score=%.0f label=%s", score, label)
    return label


@dataclass
class ConfirmationSignal:
    symbol: str
    confirmation_type: str
    direction: str
    level: float
    candle_time: str
    score: float
    strength: str
    grade: str
    reason: str
    invalidation: str
    suggested_entry: float
    suggested_sl: float
    suggested_tps: dict[str, float]
    tp_rationale: str
    sl_rationale: str
    trade_path_rationale: str
    scenario: str
    watch_zone_id: str
    scenario_id: str
    zone_low: float
    zone_high: float
    confirmation_score: float = 0.0
    confirmation_grade: str = "Reject"
    entry_reference: float = 0.0
    invalidation_reference: float = 0.0
    is_entry_valid: bool = False
    rejection_reason: str = ""
    freshness_status: str = "fresh"
    confirmation_signature: str = ""
    confirmation_age_candles: int = 0
    reaction_level: float = 0.0
    invalidation_level: float = 0.0
    risk_pips: float = 0.0
    tp1_reward_pips: float = 0.0
    tp2_reward_pips: float = 0.0
    tp3_reward_pips: float = 0.0
    tp1_rr: float = 0.0
    tp2_rr: float = 0.0
    tp3_rr: float = 0.0
    sl_source: str = "structure_sl_engine"
    tp_source: str = "structure_tp_engine"
    trade_path_source: str = "trade_path_engine"
    target_roles: dict[str, str] | None = None
    setup_quality_label: str = "HIGH-PROBABILITY SETUP"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


AnalystConfirmation = ConfirmationSignal


@dataclass
class AnalystTradeSetup:
    strategy_type: str
    scenario_id: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    confirmation_type: str
    confirmation_grade: str
    entry_reason: str
    sl_rationale: str
    tp_rationale: str
    invalidation: str
    no_chase_status: str
    source: str = "analyst_layer"
    reaction_level: float = 0.0
    invalidation_level: float = 0.0
    risk_pips: float = 0.0
    tp1_reward_pips: float = 0.0
    tp2_reward_pips: float = 0.0
    tp3_reward_pips: float = 0.0
    tp1_rr: float = 0.0
    tp2_rr: float = 0.0
    tp3_rr: float = 0.0
    sl_source: str = "structure_sl_engine"
    tp_source: str = "structure_tp_engine"
    trade_path_source: str = "trade_path_engine"
    trade_path_rationale: str = ""
    target_roles: dict[str, str] | None = None
    setup_quality_label: str = "HIGH-PROBABILITY SETUP"
    candidate_rank_score: float = 0.0
    strategy_suggested_entry: Optional[float] = None
    strategy_suggested_sl: Optional[float] = None
    strategy_suggested_tp1: Optional[float] = None
    strategy_suggested_tp2: Optional[float] = None
    strategy_suggested_tp3: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GoldConfirmationEngine:
    def analyze(self, m15: pd.DataFrame, market_plan, current_price: float) -> list[ConfirmationSignal]:
        if m15 is None or m15.empty or len(m15) < 8:
            return []

        logger.info("CONFIRMATION ENGINE STARTED")
        confirmations: list[ConfirmationSignal] = []
        active_zones = [
            zone for zone in getattr(market_plan, "active_watch_zones", [])
            if zone.get("status") in {"fresh", "active"}
        ]
        recent = m15.tail(8).reset_index(drop=True)
        last = recent.iloc[-1]
        prev = recent.iloc[-2]
        avg_body = float((recent["close"] - recent["open"]).abs().tail(6).mean() or 0.01)

        for zone in active_zones:
            direction = str(zone.get("direction", "")).upper()
            zone_low = float(zone.get("level_low", zone.get("watch_low", zone.get("level", current_price))))
            zone_high = float(zone.get("level_high", zone.get("watch_high", zone.get("level", current_price))))
            zone_mid = round((zone_low + zone_high) / 2.0, 2)
            scenario = zone.get("scenario_type", "primary")
            base_invalidation = float(zone.get("invalidation_level", zone_high if direction == "SELL" else zone_low))

            def build_confirmation(
                confirmation_type: str,
                score: float,
                grade: str,
                reason: str,
                entry: float,
                strength: str,
            ) -> ConfirmationSignal:
                trade_path = build_trade_path_plan(
                    symbol="XAUUSD",
                    direction=direction,
                    entry=float(entry),
                    current_price=current_price,
                    watch_zone=(zone_low, zone_high),
                    scenario=scenario,
                    key_supports=getattr(market_plan, "key_supports", []),
                    key_resistances=getattr(market_plan, "key_resistances", []),
                    psychological_levels=getattr(market_plan, "actionable_psych_levels", getattr(market_plan, "psychological_levels", [])),
                    liquidity_zones=getattr(market_plan, "liquidity_zones", []),
                    invalidation_level=base_invalidation,
                )
                sl_info = build_structure_sl(
                    direction,
                    zone_low,
                    zone_high,
                    base_invalidation,
                    entry=float(entry),
                    structural_level=trade_path.opposing_levels[0] if trade_path.opposing_levels else None,
                    invalidation_reason=trade_path.invalidation_reason,
                )
                tp_info = build_structure_tps(
                    direction,
                    entry,
                    getattr(market_plan, "key_supports", []),
                    getattr(market_plan, "key_resistances", []),
                    getattr(market_plan, "actionable_psych_levels", getattr(market_plan, "psychological_levels", [])),
                    liquidity_levels=getattr(market_plan, "liquidity_zones", []),
                    sl=float(sl_info["sl"]),
                    trade_path=trade_path,
                )
                signature = (
                    f"XAUUSD:{direction}:{confirmation_type}:{zone_low:.2f}-{zone_high:.2f}:"
                    f"{pd.Timestamp(last['time']).isoformat()}:{round(float(entry), 2):.2f}"
                )
                signal = ConfirmationSignal(
                    symbol="XAUUSD",
                    confirmation_type=confirmation_type,
                    direction=direction,
                    level=zone_mid,
                    candle_time=str(pd.Timestamp(last["time"]).isoformat()),
                    score=round(score, 1),
                    strength=strength,
                    grade=grade,
                    reason=reason,
                    invalidation=str(sl_info.get("invalidation_reason", f"Break beyond {base_invalidation:.2f}")),
                    suggested_entry=round(float(entry), 2),
                    suggested_sl=float(sl_info["sl"]),
                    suggested_tps={"tp1": float(tp_info["tp1"]), "tp2": float(tp_info["tp2"]), "tp3": float(tp_info["tp3"])},
                    tp_rationale=str(tp_info["tp_rationale"]),
                    sl_rationale=str(sl_info["sl_rationale"]),
                    trade_path_rationale=trade_path.path_rationale,
                    scenario=scenario,
                    watch_zone_id=str(zone.get("zone_id")),
                    scenario_id=str(zone.get("zone_id")),
                    zone_low=zone_low,
                    zone_high=zone_high,
                    confirmation_score=round(score, 1),
                    confirmation_grade=grade,
                    entry_reference=round(float(entry), 2),
                    invalidation_reference=base_invalidation,
                    is_entry_valid=grade in {"A", "A+"},
                    rejection_reason="",
                    freshness_status="fresh",
                    confirmation_signature=signature,
                    confirmation_age_candles=0,
                    reaction_level=float(tp_info.get("reaction_level", 0.0) or 0.0),
                    invalidation_level=float(sl_info.get("invalidation_level", base_invalidation) or base_invalidation),
                    risk_pips=float(tp_info.get("risk_pips", 0.0) or 0.0),
                    tp1_reward_pips=float(tp_info.get("tp1_reward_pips", 0.0) or 0.0),
                    tp2_reward_pips=float(tp_info.get("tp2_reward_pips", 0.0) or 0.0),
                    tp3_reward_pips=float(tp_info.get("tp3_reward_pips", 0.0) or 0.0),
                    tp1_rr=float(tp_info.get("tp1_rr", 0.0) or 0.0),
                    tp2_rr=float(tp_info.get("tp2_rr", 0.0) or 0.0),
                    tp3_rr=float(tp_info.get("tp3_rr", 0.0) or 0.0),
                    sl_source=str(sl_info.get("sl_source", "structure_sl_engine")),
                    tp_source=str(tp_info.get("tp_source", "structure_tp_engine")),
                    trade_path_source="trade_path_engine",
                    target_roles=dict(tp_info.get("target_roles", {}) or {}),
                    setup_quality_label=quality_label_from_score(score),
                )
                valid_setup, validation_reason = validate_final_trade_setup(signal)
                if not valid_setup:
                    signal.is_entry_valid = False
                    signal.rejection_reason = validation_reason
                    logger.warning("FINAL SETUP REJECTED: reason=%s", validation_reason)
                else:
                    signal.is_entry_valid = grade in {"A", "A+"}
                logger.info(
                    "CONFIRMATION DETECTED: type=%s grade=%s direction=%s zone=%s",
                    confirmation_type,
                    grade,
                    direction,
                    signal.watch_zone_id,
                )
                return signal

            # 1. sweep reclaim
            if direction == "SELL" and float(last["high"]) > zone_high and float(last["close"]) < zone_high:
                score = 88.0
                if float(last["open"]) - float(last["close"]) > avg_body:
                    score += 8.0
                grade = "A+" if score >= 95 else "A"
                confirmations.append(build_confirmation(
                    "sweep_reclaim_confirmation", score, grade,
                    f"Swept above {zone_high:.2f} and closed back below resistance.",
                    min(float(last["close"]), zone_high), "strong" if grade == "A+" else "good",
                ))
            elif direction == "BUY" and float(last["low"]) < zone_low and float(last["close"]) > zone_low:
                score = 88.0
                if float(last["close"]) - float(last["open"]) > avg_body:
                    score += 8.0
                grade = "A+" if score >= 95 else "A"
                confirmations.append(build_confirmation(
                    "sweep_reclaim_confirmation", score, grade,
                    f"Swept below {zone_low:.2f} and closed back above support.",
                    max(float(last["close"]), zone_low), "strong" if grade == "A+" else "good",
                ))

            # 2. break retest close
            if direction == "SELL":
                if float(prev["close"]) < zone_low and float(last["high"]) >= zone_low and float(last["close"]) < zone_low:
                    confirmations.append(build_confirmation(
                        "break_retest_close_confirmation", 92.0, "A",
                        f"Clean close below {zone_low:.2f}, retest held as resistance.",
                        min(float(last["close"]), zone_low), "good",
                    ))
            else:
                if float(prev["close"]) > zone_high and float(last["low"]) <= zone_high and float(last["close"]) > zone_high:
                    confirmations.append(build_confirmation(
                        "break_retest_close_confirmation", 92.0, "A",
                        f"Clean close above {zone_high:.2f}, retest held as support.",
                        max(float(last["close"]), zone_high), "good",
                    ))

            # 3. displacement
            body = abs(float(last["close"]) - float(last["open"]))
            if body >= avg_body * 1.6:
                if direction == "SELL" and float(last["close"]) < float(last["open"]) and float(last["high"]) >= zone_low:
                    confirmations.append(build_confirmation(
                        "displacement_confirmation", 90.0, "A",
                        "Bearish displacement from active sell watch zone.",
                        float(last["close"]), "good",
                    ))
                elif direction == "BUY" and float(last["close"]) > float(last["open"]) and float(last["low"]) <= zone_high:
                    confirmations.append(build_confirmation(
                        "displacement_confirmation", 90.0, "A",
                        "Bullish displacement from active buy watch zone.",
                        float(last["close"]), "good",
                    ))

            # 4. structure shift
            recent_high = float(recent["high"].iloc[-4:-1].max())
            recent_low = float(recent["low"].iloc[-4:-1].min())
            if direction == "BUY" and float(last["close"]) > recent_high and float(last["low"]) <= zone_high:
                confirmations.append(build_confirmation(
                    "structure_shift_confirmation", 96.0, "A+",
                    f"Bullish structure shift confirmed above {recent_high:.2f}.",
                    float(last["close"]), "strong",
                ))
            elif direction == "SELL" and float(last["close"]) < recent_low and float(last["high"]) >= zone_low:
                confirmations.append(build_confirmation(
                    "structure_shift_confirmation", 96.0, "A+",
                    f"Bearish structure shift confirmed below {recent_low:.2f}.",
                    float(last["close"]), "strong",
                ))

            # 5. engulfing at key level
            prev_body_low = min(float(prev["open"]), float(prev["close"]))
            prev_body_high = max(float(prev["open"]), float(prev["close"]))
            last_body_low = min(float(last["open"]), float(last["close"]))
            last_body_high = max(float(last["open"]), float(last["close"]))
            at_level = zone_low - 4 <= float(last["close"]) <= zone_high + 4 or zone_low - 4 <= float(last["high"]) <= zone_high + 4
            if at_level:
                if direction == "SELL" and float(prev["close"]) > float(prev["open"]) and float(last["close"]) < float(last["open"]) and last_body_low <= prev_body_low and last_body_high >= prev_body_high:
                    confirmations.append(build_confirmation(
                        "engulfing_level_confirmation", 91.0, "A",
                        "Bearish engulfing formed at key resistance zone.",
                        float(last["close"]), "good",
                    ))
                elif direction == "BUY" and float(prev["close"]) < float(prev["open"]) and float(last["close"]) > float(last["open"]) and last_body_low <= prev_body_low and last_body_high >= prev_body_high:
                    confirmations.append(build_confirmation(
                        "engulfing_level_confirmation", 91.0, "A",
                        "Bullish engulfing formed at key support zone.",
                        float(last["close"]), "good",
                    ))

            # 6. failed retest
            if direction == "SELL":
                if float(last["high"]) >= zone_low and float(last["close"]) < zone_low and float(last["close"]) < float(last["open"]):
                    confirmations.append(build_confirmation(
                        "failed_retest_confirmation", 94.0, "A+",
                        f"Failed retest rejected from {zone_low:.2f} and closed away.",
                        float(last["close"]), "strong",
                    ))
            else:
                if float(last["low"]) <= zone_high and float(last["close"]) > zone_high and float(last["close"]) > float(last["open"]):
                    confirmations.append(build_confirmation(
                        "failed_retest_confirmation", 94.0, "A+",
                        f"Failed retest held above {zone_high:.2f} and closed away.",
                        float(last["close"]), "strong",
                    ))

        return self._dedupe_best(confirmations)

    @staticmethod
    def _dedupe_best(confirmations: list[ConfirmationSignal]) -> list[ConfirmationSignal]:
        best: dict[tuple[str, str], ConfirmationSignal] = {}
        for item in confirmations:
            key = (item.watch_zone_id, item.direction)
            if key not in best or item.score > best[key].score:
                best[key] = item
        return sorted(best.values(), key=lambda item: item.score, reverse=True)


def confirmation_is_fresh(confirmation: ConfirmationSignal, current_price: float) -> tuple[bool, str]:
    if confirmation.confirmation_age_candles > CONFIRMATION_MAX_AGE_CANDLES:
        logger.info("CONFIRMATION STALE: signature=%s", confirmation.confirmation_signature)
        return False, "ENTRY REJECTED: confirmation stale"

    distance = abs(current_price - confirmation.suggested_entry)
    if distance > MAX_ENTRY_CHASE_DISTANCE_PIPS:
        logger.info("CONFIRMATION REJECTED: signature=%s reason=price_too_far", confirmation.confirmation_signature)
        return False, "ENTRY REJECTED: price too far from entry"

    direction = confirmation.direction.upper()
    tp1 = confirmation.suggested_tps.get("tp1")
    if tp1 is not None:
        if direction == "SELL" and current_price <= tp1:
            logger.info("CONFIRMATION REJECTED: signature=%s reason=tp1_already_reached", confirmation.confirmation_signature)
            return False, "ENTRY REJECTED: TP1 already reached"
        if direction == "BUY" and current_price >= tp1:
            logger.info("CONFIRMATION REJECTED: signature=%s reason=tp1_already_reached", confirmation.confirmation_signature)
            return False, "ENTRY REJECTED: TP1 already reached"

    return True, ""
