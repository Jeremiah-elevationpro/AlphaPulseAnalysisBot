"""
AlphaPulse - Trade Data Models
================================
Pure Python dataclasses that mirror the database schema.
These are the runtime objects used throughout the system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


# ─────────────────────────────────────────────────────────
# TRADE STATUS CONSTANTS
# ─────────────────────────────────────────────────────────

class TradeStatus:
    PENDING       = "PENDING"
    ACTIVE        = "ACTIVE"
    TP1_HIT       = "TP1_HIT"
    TP2_HIT       = "TP2_HIT"
    TP3_HIT       = "TP3_HIT"
    TP4_HIT       = "TP4_HIT"
    TP5_HIT       = "TP5_HIT"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    COMPLETED     = "COMPLETED"
    CANCELLED     = "CANCELLED"

    @staticmethod
    def from_tp_index(idx: int) -> str:
        return ["TP1_HIT", "TP2_HIT", "TP3_HIT", "TP4_HIT", "TP5_HIT"][idx]


class TradeResult:
    PARTIAL_WIN   = "PARTIAL_WIN"
    BREAKEVEN_WIN = "BREAKEVEN_WIN"
    WIN        = "WIN"         # TP2 reached before trade closed
    STRONG_WIN = "STRONG_WIN"  # TP3 or higher reached before trade closed
    LOSS       = "LOSS"        # SL hit before TP1 protection


# ─────────────────────────────────────────────────────────
# TRADE MODEL
# ─────────────────────────────────────────────────────────

@dataclass
class Trade:
    direction: str               # "BUY" | "SELL"
    entry_price: float
    sl_price: float
    tp_levels: List[float]       # [tp1, tp2, tp3, tp4, tp5]
    level_type: str              # "A" | "V" | "Gap"
    level_price: float
    higher_tf: str
    lower_tf: str
    confidence: float = 0.5
    pair: str = "XAUUSD"
    setup_type: str = "major"
    # "major" | "recent_leg" | "previous_leg" | "qm_level" |
    # "imbalance_confluence" | "psychological_confluence"

    # Context flags persisted to DB
    is_qm: bool = False
    is_psychological: bool = False
    is_liquidity_sweep: bool = False
    session_name: str = ""        # "london" | "new_york" | ""
    h4_bias: str = ""             # "bullish" | "bearish" | "neutral"
    trend_aligned: bool = True
    confirmation_type: str = "rejection"
    # "rejection" | "liquidity_sweep_reclaim" | "double_pattern" | "engulfing_reversal"
    micro_confirmation_type: str = ""
    bias_gate_result: str = ""
    pd_location: str = ""
    high_quality_trade: bool = False
    micro_strength: str = "normal"
    strategy_type: str = "gap_sweep"
    source: str = "live_bot"
    dominant_bias: str = ""
    bias_strength: str = "weak"
    confirmation_score: float = 0.0
    confirmation_path: str = ""
    quality_rejection_count: int = 0
    structure_break_count: int = 0
    level_timeframe: str = ""
    confluence_with: List[str] = field(default_factory=list)
    realized_pips: float = 0.0

    # Auto-generated
    trade_uuid: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = TradeStatus.PENDING
    tp_hit: List[bool] = field(default_factory=lambda: [False] * 5)
    result: Optional[str] = None
    be_moved: bool = False
    tp1_alert_sent: bool = False
    protected_after_tp1: bool = False
    breakeven_exit: bool = False
    notes: str = ""

    created_at: datetime = field(default_factory=datetime.utcnow)
    activated_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    # Internal DB id
    db_id: Optional[int] = None

    # ── convenience ──────────────────────────────────────

    @property
    def tp1(self) -> Optional[float]:
        return self.tp_levels[0] if len(self.tp_levels) > 0 else None

    @property
    def tp2(self) -> Optional[float]:
        return self.tp_levels[1] if len(self.tp_levels) > 1 else None

    @property
    def tp3(self) -> Optional[float]:
        return self.tp_levels[2] if len(self.tp_levels) > 2 else None

    @property
    def tp4(self) -> Optional[float]:
        return self.tp_levels[3] if len(self.tp_levels) > 3 else None

    @property
    def tp5(self) -> Optional[float]:
        return self.tp_levels[4] if len(self.tp_levels) > 4 else None

    @property
    def tf_pair_str(self) -> str:
        return f"{self.higher_tf}-{self.lower_tf}"

    @property
    def hit_count(self) -> int:
        return sum(self.tp_hit)

    @property
    def sl_pips(self) -> float:
        from utils.helpers import price_to_pips
        return price_to_pips(abs(self.entry_price - self.sl_price))

    def to_db_dict(self) -> dict:
        d = {
            "trade_uuid": self.trade_uuid,
            "pair": self.pair,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "level_type": self.level_type,
            "level_price": self.level_price,
            "higher_tf": self.higher_tf,
            "lower_tf": self.lower_tf,
            "confidence": round(self.confidence, 3),
            "status": self.status,
            "setup_type": self.setup_type,
            "is_qm": self.is_qm,
            "is_psychological": self.is_psychological,
            "is_liquidity_sweep": self.is_liquidity_sweep,
            "session_name": self.session_name,
            "h4_bias": self.h4_bias,
            "trend_aligned": self.trend_aligned,
            "confirmation_type": self.confirmation_type,
            "strategy_type": self.strategy_type,
            "source": self.source,
            "dominant_bias": self.dominant_bias,
            "bias_strength": self.bias_strength,
            "confirmation_score": self.confirmation_score,
            "confirmation_path": self.confirmation_path,
            "quality_rejection_count": self.quality_rejection_count,
            "structure_break_count": self.structure_break_count,
            "level_timeframe": self.level_timeframe,
            "confluence_with": ",".join(self.confluence_with) if self.confluence_with else "",
            "tp_progress_reached": self.hit_count,
            "protected_after_tp1": self.protected_after_tp1,
            "tp1_alert_sent": self.tp1_alert_sent,
            "breakeven_exit": self.breakeven_exit,
        }
        for i, tp in enumerate(self.tp_levels[:5], start=1):
            d[f"tp{i}"] = tp
        return d

    def __repr__(self):
        return (f"Trade({self.direction} {self.pair} @ {self.entry_price:.2f} "
                f"| SL {self.sl_price:.2f} | {self.status})")
