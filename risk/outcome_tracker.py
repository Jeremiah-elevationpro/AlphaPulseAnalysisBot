from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TradeReview:
    run_id: int | None
    setup_id: str
    symbol: str
    scenario_key: str
    scenario_type: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    final_result: str
    pips_result: float
    confirmation_type: str
    confirmation_grade: str
    learning_score: float
    reaction_level: float
    invalidation_level: float
    risk_pips: float
    tp1_reward_pips: float
    tp2_reward_pips: float
    tp3_reward_pips: float
    tp1_rr: float
    tp2_rr: float
    tp3_rr: float
    sl_source: str
    tp_source: str
    trade_path_source: str
    trade_path_rationale: str
    target_roles: dict[str, Any]
    setup_quality_label: str
    decision_reason: str
    session_name: str
    h4_bias: str
    h1_bias: str
    market_condition: str
    result: str
    tp1_hit: bool
    tp2_hit: bool
    tp3_hit: bool
    protected_after_tp1: bool
    review_notes: str
    created_at: str
    closed_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class OutcomeTracker:
    def __init__(self):
        self._reviews: list[TradeReview] = []

    def store_review(self, management_state, confirmation_type: str, confirmation_grade: str, learning_score: float, review_notes: str = "") -> TradeReview:
        metadata = dict(getattr(management_state, "metadata", {}) or {})
        review = TradeReview(
            run_id=metadata.get("run_id"),
            setup_id=management_state.setup_id,
            symbol=management_state.symbol,
            scenario_key=str(metadata.get("scenario_key", "")),
            scenario_type=str(metadata.get("scenario_type", "")),
            direction=management_state.direction,
            entry=management_state.entry,
            sl=management_state.sl,
            tp1=management_state.tp1,
            tp2=management_state.tp2,
            tp3=management_state.tp3,
            final_result=management_state.final_result or management_state.current_status.upper(),
            pips_result=management_state.pips_result,
            confirmation_type=confirmation_type,
            confirmation_grade=confirmation_grade,
            learning_score=learning_score,
            reaction_level=float(metadata.get("reaction_level", 0.0) or 0.0),
            invalidation_level=float(metadata.get("invalidation_level", 0.0) or 0.0),
            risk_pips=float(metadata.get("risk_pips", abs(management_state.entry - management_state.sl)) or 0.0),
            tp1_reward_pips=float(metadata.get("tp1_reward_pips", abs(management_state.tp1 - management_state.entry)) or 0.0),
            tp2_reward_pips=float(metadata.get("tp2_reward_pips", abs(management_state.tp2 - management_state.entry)) or 0.0),
            tp3_reward_pips=float(metadata.get("tp3_reward_pips", abs(management_state.tp3 - management_state.entry)) or 0.0),
            tp1_rr=float(metadata.get("tp1_rr", 0.0) or 0.0),
            tp2_rr=float(metadata.get("tp2_rr", 0.0) or 0.0),
            tp3_rr=float(metadata.get("tp3_rr", 0.0) or 0.0),
            sl_source=str(metadata.get("sl_source", "structure_sl_engine")),
            tp_source=str(metadata.get("tp_source", "structure_tp_engine")),
            trade_path_source=str(metadata.get("trade_path_source", "trade_path_engine")),
            trade_path_rationale=str(metadata.get("trade_path_rationale", "")),
            target_roles=dict(metadata.get("target_roles", {}) or {}),
            setup_quality_label=str(metadata.get("setup_quality_label", "QUALITY SETUP")),
            decision_reason=str(metadata.get("decision_reason", "")),
            session_name=str(metadata.get("session_name", "")),
            h4_bias=str(metadata.get("h4_bias", "")),
            h1_bias=str(metadata.get("h1_bias", "")),
            market_condition=str(metadata.get("market_condition", "")),
            result=management_state.final_result or management_state.current_status.upper(),
            tp1_hit=management_state.current_status in {"tp1_hit", "tp2_hit", "tp3_hit"} or management_state.protected_after_tp1,
            tp2_hit=management_state.current_status in {"tp2_hit", "tp3_hit"} or management_state.final_result in {"WIN", "STRONG_WIN"},
            tp3_hit=management_state.current_status == "tp3_hit" or management_state.final_result == "STRONG_WIN",
            protected_after_tp1=management_state.protected_after_tp1,
            review_notes=review_notes,
            created_at=datetime.now(timezone.utc).isoformat(),
            closed_at=datetime.now(timezone.utc).isoformat(),
        )
        self._reviews.append(review)
        logger.info("POST TRADE REVIEW STORED: setup_id=%s", management_state.setup_id)
        return review
