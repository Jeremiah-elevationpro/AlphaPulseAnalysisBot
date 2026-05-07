from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LearningScore:
    strategy_type: str
    scenario_type: str
    confirmation_type: str
    session_name: str
    timeframe: str
    direction: str
    bias_context: str
    sample_size: int
    win_rate: float
    tp1_rate: float
    tp2_rate: float
    tp3_rate: float
    avg_pips: float
    net_pips: float
    loss_rate: float
    confidence_tier: str
    recommended_action: str
    score_adjustment: float
    profile_used: str
    historical_win_rate: float
    reason: str
    final_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScoringEngine:
    BASE_GRADES = {"A+": 90.0, "A": 80.0, "B": 60.0, "Reject": 0.0}

    def __init__(self, learning_engine=None):
        self._learning = learning_engine

    def score_confirmation(self, market_plan, confirmation, strategy_type: str, context: dict[str, Any]) -> LearningScore:
        logger.info("LEARNING PROFILE USED: strategy=%s confirmation=%s", strategy_type, confirmation.confirmation_type)
        profile = {}
        if self._learning is not None:
            profile = self._learning.get_strategy_learning_profile(strategy_type, context) or {}
            db = getattr(self._learning, "_db", None)
            if db is not None and hasattr(db, "get_analyst_learning_profile"):
                analyst_profile = db.get_analyst_learning_profile(
                    {
                        "scenario_type": str(getattr(confirmation, "scenario", "primary")),
                        "confirmation_type": confirmation.confirmation_type,
                        "direction": confirmation.direction,
                        "session_name": context.get("session_name", ""),
                        "h4_bias": getattr(market_plan, "dominant_bias", ""),
                        "h1_bias": getattr(market_plan, "market_structure_state", ""),
                        "market_condition": context.get("market_condition", ""),
                    }
                ) or {}
                if int(analyst_profile.get("sample_size", 0) or 0) >= int(profile.get("sample_size", 0) or 0):
                    profile = {**profile, **analyst_profile}

        sample_size = int(profile.get("sample_size", 0) or 0)
        win_rate = float(profile.get("win_rate", 0.0) or 0.0)
        net_pips = float(profile.get("net_pips", 0.0) or 0.0)
        avg_pips = float(profile.get("avg_pips", 0.0) or 0.0)
        confidence_tier = str(profile.get("confidence_tier") or ("insufficient_sample" if sample_size < 5 else "medium"))
        recommended_action = str(profile.get("recommended_action") or "allow")
        score = self.BASE_GRADES.get(getattr(confirmation, "confirmation_grade", confirmation.grade), 0.0)
        adjustment = 0.0
        reasons: list[str] = []

        if getattr(market_plan, "dominant_bias", "neutral") in {"bullish", "bearish"} and getattr(market_plan, "bias_strength", "weak") == "strong":
            adjustment += 10.0
            reasons.append("strong bias aligned")
        elif getattr(market_plan, "bias_strength", "weak") == "moderate":
            adjustment += 5.0
            reasons.append("moderate bias aligned")

        if len(getattr(market_plan, "actionable_psych_levels", []) or []) >= 3:
            adjustment += 5.0
            reasons.append("psychological level confluence")

        if sample_size < 5:
            adjustment -= 5.0
            reasons.append("low sample")
            logger.info("LOW SAMPLE WARNING: strategy=%s sample=%d", strategy_type, sample_size)
        elif win_rate < 40.0 and sample_size >= 5:
            adjustment -= 15.0
            recommended_action = "caution"
            reasons.append("historically weak profile")

        final_score = max(0.0, min(100.0, score + adjustment))
        if final_score < 65.0 and sample_size >= 5:
            recommended_action = "block"
            logger.info("LEARNING BLOCKED SETUP: strategy=%s score=%.1f", strategy_type, final_score)
        elif final_score >= 90.0:
            recommended_action = "boost"
            logger.info("LEARNING BOOSTED SETUP: strategy=%s score=%.1f", strategy_type, final_score)

        result = LearningScore(
            strategy_type=strategy_type,
            scenario_type=str(getattr(confirmation, "scenario", "primary")),
            confirmation_type=str(confirmation.confirmation_type),
            session_name=str(context.get("session_name", "")),
            timeframe=str(context.get("timeframe", "M15")),
            direction=str(confirmation.direction),
            bias_context=f"{getattr(market_plan, 'dominant_bias', 'neutral')}/{getattr(market_plan, 'bias_strength', 'weak')}",
            sample_size=sample_size,
            win_rate=win_rate,
            tp1_rate=float(profile.get("tp1_rate", 0.0) or 0.0),
            tp2_rate=float(profile.get("tp2_rate", 0.0) or 0.0),
            tp3_rate=float(profile.get("tp3_rate", 0.0) or 0.0),
            avg_pips=avg_pips,
            net_pips=net_pips,
            loss_rate=max(0.0, 100.0 - win_rate if win_rate <= 100 else 0.0),
            confidence_tier=confidence_tier,
            recommended_action=recommended_action,
            score_adjustment=adjustment,
            profile_used=str(profile.get("profile_used") or f"{strategy_type}:{getattr(confirmation, 'scenario', 'primary')}:{confirmation.confirmation_type}"),
            historical_win_rate=win_rate,
            reason=", ".join(reasons) or "base confirmation grade",
            final_score=final_score,
        )
        logger.info("LEARNING SCORE GENERATED: strategy=%s final_score=%.1f action=%s", strategy_type, final_score, recommended_action)
        return result
