from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from config.settings import PIP_SIZE
from db.models import Trade


class ReplayState:
    WATCHLIST = "watchlist"
    PENDING_ORDER_READY = "pending_order_ready"
    ACTIVATED = "activated"
    CLOSED = "closed"


@dataclass
class ReplayCounters:
    total_watchlists: int = 0
    total_pending_order_ready: int = 0
    total_activated_trades: int = 0
    total_wins: int = 0
    total_losses: int = 0
    tp_hits: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 0])


@dataclass
class PendingReplayTrade:
    key: str
    trade: Trade
    setup: Any
    pending_order_ready_time: datetime
    confirmation_pattern: str
    market_condition: str
    micro_confirmation_type: str = "none"
    micro_confirmation_score: float = 0.0
    micro_layer_decision: str = "neutral"
    h1_liquidity_sweep: bool = False
    h1_sweep_direction: str = "none"
    h1_reclaim_confirmed: bool = False
    pd_location: str = "unknown"
    pd_filter_score: float = 0.0
    bias_gate_result: str = "not_checked"
    high_quality_trade: bool = False
    micro_strength: str = "normal"
    dominant_bias: str = ""
    bias_strength: str = ""
    h1_state: str = ""
    activated: bool = False
    closed: bool = False
    initial_sl: Optional[float] = None
    protected_after_tp1: bool = False
    tp1_alert_sent: bool = False
    breakeven_exit: bool = False
    activation_time: Optional[datetime] = None
    closed_time: Optional[datetime] = None
    final_result: str = "OPEN"
    failure_reason: str = ""
    tp_hit: List[bool] = field(default_factory=lambda: [False, False, False, False, False])
    max_favorable_excursion: float = 0.0
    max_adverse_excursion: float = 0.0

    def __post_init__(self):
        if self.initial_sl is None:
            self.initial_sl = self.trade.sl_price

    @property
    def timeframe_pair(self) -> str:
        return f"{self.trade.higher_tf}->{self.trade.lower_tf}"

    @property
    def tp_progress(self) -> int:
        return sum(self.tp_hit)

    @property
    def pips_to_tp(self) -> List[float]:
        entry = self.trade.entry_price
        return [self._price_to_pips(abs(tp - entry)) for tp in self.trade.tp_levels[:5]]

    @property
    def pips_to_sl(self) -> float:
        sl = self.initial_sl if self.initial_sl is not None else self.trade.sl_price
        return self._price_to_pips(abs(self.trade.entry_price - sl))

    @property
    def realized_pips(self) -> float:
        """
        Replay follows the live management model:
        - SL before TP1 is negative risk.
        - TP1 protection means the trade is no longer a loss.
        - The realized replay score is the highest TP level reached.
        """
        progress = self.tp_progress
        if progress > 0:
            tp_pips = self.pips_to_tp
            return round(tp_pips[min(progress, len(tp_pips)) - 1], 2) if tp_pips else 0.0
        if self.final_result == "LOSS":
            return round(-self.pips_to_sl, 2)
        return 0.0

    @property
    def final_pips(self) -> float:
        return self.realized_pips

    @property
    def max_potential_pips(self) -> float:
        return self._price_to_pips(self.max_favorable_excursion)

    @property
    def reward_score(self) -> float:
        if self.final_result == "LOSS":
            return -3.0
        if self.final_result in ("PARTIAL_WIN", "BREAKEVEN_WIN"):
            return 1.0
        if self.final_result == "WIN":
            return 2.0
        if self.final_result == "STRONG_WIN":
            return 3.5 + max(0, self.tp_progress - 3) * 0.5
        return float(self.tp_progress)

    def to_supabase_payload(self, replay_run_id: int) -> Dict[str, Any]:
        trade = self.trade
        tp_pips = self.pips_to_tp + [0.0] * max(0, 5 - len(self.pips_to_tp))
        return {
            "replay_run_id": replay_run_id,
            "source": "historical_replay",
            "symbol": trade.pair,
            "timestamp": self.closed_time or self.activation_time or self.pending_order_ready_time,
            "direction": trade.direction,
            "setup_type": trade.setup_type,
            "level_type": trade.level_type,
            "timeframe_pair": self.timeframe_pair,
            "dominant_bias": self.dominant_bias or trade.h4_bias,
            "bias_strength": self.bias_strength,
            "h1_state": self.h1_state,
            "confirmation_pattern": self.confirmation_pattern,
            "micro_confirmation_type": self.micro_confirmation_type,
            "micro_confirmation_score": self.micro_confirmation_score,
            "micro_layer_decision": self.micro_layer_decision,
            "h1_liquidity_sweep": self.h1_liquidity_sweep,
            "h1_sweep_direction": self.h1_sweep_direction,
            "h1_reclaim_confirmed": self.h1_reclaim_confirmed,
            "pd_location": self.pd_location,
            "pd_filter_score": self.pd_filter_score,
            "bias_gate_result": self.bias_gate_result,
            "high_quality_trade": self.high_quality_trade,
            "micro_strength": self.micro_strength,
            "pending_order_ready_time": self.pending_order_ready_time,
            "activation_time": self.activation_time,
            "entry": trade.entry_price,
            "sl": self.initial_sl,
            "tp1": trade.tp1,
            "tp2": trade.tp2,
            "tp3": trade.tp3,
            "tp4": trade.tp4,
            "tp5": trade.tp5,
            "final_result": self.final_result,
            "tp_progress": self.tp_progress,
            "tp_progress_reached": self.tp_progress,
            "protected_after_tp1": self.protected_after_tp1,
            "tp1_alert_sent": self.tp1_alert_sent,
            "breakeven_exit": self.breakeven_exit,
            "pips_to_tp1": tp_pips[0],
            "pips_to_tp2": tp_pips[1],
            "pips_to_tp3": tp_pips[2],
            "pips_to_tp4": tp_pips[3],
            "pips_to_tp5": tp_pips[4],
            "realized_pips": self.realized_pips,
            "max_potential_pips": self.max_potential_pips,
            "final_pips": self.final_pips,
            "max_favorable_excursion": round(self.max_favorable_excursion, 2),
            "max_adverse_excursion": round(self.max_adverse_excursion, 2),
            "market_condition": self.market_condition,
            "session": trade.session_name,
            "reward_score": self.reward_score,
            "failure_reason": self.failure_reason,
        }

    @staticmethod
    def _price_to_pips(price_distance: float) -> float:
        pip_size = PIP_SIZE or 1.0
        return round(float(price_distance) / pip_size, 2)
