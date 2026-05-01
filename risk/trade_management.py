from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from analysis.sl_engine import validate_sl_direction
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TradeManagementState:
    setup_id: str
    symbol: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    current_status: str
    move_sl_to_be: bool
    partial_result: str
    final_result: str
    pips_result: float
    management_alert_required: bool
    review_required: bool
    protected_after_tp1: bool = False
    metadata: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeManagementEngine:
    def __init__(self):
        self._active_setups: dict[str, TradeManagementState] = {}

    def register_setup(self, setup_id: str, setup, metadata: dict[str, Any] | None = None) -> TradeManagementState:
        now = datetime.now(timezone.utc).isoformat()
        is_valid_sl, sl_reason = validate_sl_direction(str(setup.direction), float(setup.entry), float(setup.sl))
        state = TradeManagementState(
            setup_id=setup_id,
            symbol="XAUUSD",
            direction=setup.direction,
            entry=float(setup.entry),
            sl=float(setup.sl),
            tp1=float(setup.tp1),
            tp2=float(setup.tp2),
            tp3=float(setup.tp3),
            current_status="pending" if is_valid_sl else "invalidated",
            move_sl_to_be=False,
            partial_result="",
            final_result="" if is_valid_sl else "INVALIDATED",
            pips_result=0.0,
            management_alert_required=False,
            review_required=not is_valid_sl,
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
        )
        self._active_setups[setup_id] = state
        if is_valid_sl:
            logger.info("TRADE MANAGEMENT UPDATE: setup_id=%s status=pending", setup_id)
        else:
            logger.warning("TRADE MANAGEMENT REJECTED: setup_id=%s reason=%s", setup_id, sl_reason)
        return state

    def update_trade(self, setup_id: str, current_price: float) -> TradeManagementState | None:
        state = self._active_setups.get(setup_id)
        if state is None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        state.updated_at = now
        if state.direction.upper() == "SELL":
            if current_price <= state.tp1 and not state.protected_after_tp1:
                state.current_status = "tp1_hit"
                state.move_sl_to_be = True
                state.management_alert_required = True
                state.protected_after_tp1 = True
                state.partial_result = "TP1 HIT"
                logger.info("TP1 HIT: setup_id=%s", setup_id)
            if current_price <= state.tp2:
                state.current_status = "tp2_hit"
                state.final_result = "WIN"
                state.pips_result = round(state.entry - state.tp2, 2)
            if current_price <= state.tp3:
                state.current_status = "tp3_hit"
                state.final_result = "STRONG_WIN"
                state.pips_result = round(state.entry - state.tp3, 2)
                state.review_required = True
            if current_price >= state.sl and not state.final_result:
                state.current_status = "stopped"
                state.final_result = "LOSS"
                state.pips_result = round(state.entry - state.sl, 2)
                state.review_required = True
        else:
            if current_price >= state.tp1 and not state.protected_after_tp1:
                state.current_status = "tp1_hit"
                state.move_sl_to_be = True
                state.management_alert_required = True
                state.protected_after_tp1 = True
                state.partial_result = "TP1 HIT"
                logger.info("TP1 HIT: setup_id=%s", setup_id)
            if current_price >= state.tp2:
                state.current_status = "tp2_hit"
                state.final_result = "WIN"
                state.pips_result = round(state.tp2 - state.entry, 2)
            if current_price >= state.tp3:
                state.current_status = "tp3_hit"
                state.final_result = "STRONG_WIN"
                state.pips_result = round(state.tp3 - state.entry, 2)
                state.review_required = True
            if current_price <= state.sl and not state.final_result:
                state.current_status = "stopped"
                state.final_result = "LOSS"
                state.pips_result = round(state.sl - state.entry, 2)
                state.review_required = True

        if state.final_result:
            state.management_alert_required = True
            logger.info("TRADE RESULT FINALIZED: setup_id=%s result=%s", setup_id, state.final_result)
        return state
