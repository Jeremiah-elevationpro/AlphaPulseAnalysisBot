"""
AlphaPulse - Simulated Trade Tracker
=======================================
Tracks confirmed setups against live price feeds for outcome monitoring.
No broker dependency: confirmed rejections become simulated pending orders.

State machine:
  - PENDING  : first rejection confirmed; waiting for retest fill
  - ACTIVE   : retest/fill detected; TP/SL tracking starts
  - TP1-TP5  : targets hit in sequence, TP1 moves SL to BE
  - CLOSED   : SL hit, TP5 completed, or cancelled

Notifies Telegram on every state change.
Persists all state changes to the database.
"""

from __future__ import annotations

import uuid as uuid_mod
import threading
from datetime import datetime
from typing import Dict, List, Optional, Set, TYPE_CHECKING

from db.models import Trade, TradeStatus, TradeResult
from db.database import Database
from notifications.telegram_bot import TelegramBot
from config.settings import (
    PENDING_ORDER_FILL_TOLERANCE_PIPS,
    PIP_SIZE,
    ACTIVE_TIMEFRAME_PAIR_LABELS,
)
from utils.logger import get_logger

if TYPE_CHECKING:
    from learning.rl_engine import LearningEngine

logger = get_logger(__name__)


class TradeManager:
    """
    In-memory registry of all active trades with real-time price monitoring.
    Thread-safe via a single lock (trades are updated from the main scan loop).
    """

    def __init__(self, db: Database, telegram: TelegramBot,
                 learning: Optional["LearningEngine"] = None):
        self._db = db
        self._telegram = telegram
        self._learning = learning      # receives trade results for ML training
        self._trades: Dict[str, Trade] = {}  # uuid → Trade
        self._pending_activation_skip: Set[str] = set()
        self._lock = threading.Lock()

    # ─────────────────────────────────────────────────────
    # TRADE REGISTRATION
    # ─────────────────────────────────────────────────────

    def register_trade(self, trade: Trade) -> bool:
        """
        Register a first-rejection-confirmed setup as a simulated pending order.

        The trade is not ACTIVE yet. TP/SL tracking begins only after price
        revisits the exact level within the configured fill tolerance.

        Returns True always (the trade is tracked in-memory even if DB fails).
        """
        tf_pair = f"{trade.higher_tf}-{trade.lower_tf}"
        if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
            logger.info(
                "PENDING ORDER SKIPPED: disabled timeframe pair %s | %s %s %.2f",
                tf_pair,
                trade.direction,
                trade.pair,
                trade.entry_price,
            )
            return False

        trade.status = TradeStatus.PENDING
        trade.activated_at = None
        if not trade.notes:
            trade.notes = "PENDING_ORDER_READY: waiting for retest fill"

        with self._lock:
            self._trades[trade.trade_uuid] = trade
            self._pending_activation_skip.add(trade.trade_uuid)

        logger.info(
            "PENDING ORDER REGISTERED: %s %s | entry %.2f | SL %.2f | UUID %s | waiting for retest fill",
            trade.direction, trade.pair,
            trade.entry_price, trade.sl_price,
            trade.trade_uuid[:8],
        )

        # Persist to database (best-effort; trade already tracked in memory).
        try:
            db_id = self._db.insert_trade(trade.to_db_dict())
            trade.db_id = db_id
            logger.debug("Trade persisted to DB (id=%s)", db_id)
        except Exception as e:
            logger.error(
                "DB persist failed for trade %s: %s — tracked in memory only.",
                trade.trade_uuid[:8], e,
            )

        return True

    def cancel_stale_trades(self):
        """
        On startup, cancel all trades that were left PENDING or ACTIVE in the database
        from a previous session.

        This is a simulation-only bot — there are no real broker positions to
        recover. Reloading old trade levels from a prior session would cause
        immediate false TP/SL hits because price has moved since then.
        All leftover active trades are marked CANCELLED so the DB stays clean.
        """
        try:
            rows = self._db.get_active_trades()
            if not rows:
                logger.info("No stale active trades found in database.")
                return

            cancelled = 0
            for row in rows:
                trade_uuid = (
                    row.get("trade_uuid") if isinstance(row, dict)
                    else (row[1] if len(row) > 1 else None)
                )
                if trade_uuid:
                    try:
                        status = (
                            row.get("status") if isinstance(row, dict)
                            else (row[16] if len(row) > 16 else "")
                        )
                        if status == TradeStatus.PENDING:
                            logger.info(
                                "LEARNING SKIPPED: pending order was never filled | UUID %s",
                                str(trade_uuid)[:8],
                            )
                        self._db.update_trade_status(
                            trade_uuid,
                            TradeStatus.CANCELLED,
                            closed_at=datetime.utcnow().isoformat(),
                        )
                        cancelled += 1
                    except Exception as e:
                        logger.warning("Could not cancel stale trade %s: %s", trade_uuid[:8], e)

            logger.info(
                "Startup cleanup: cancelled %d stale trade(s) from previous session "
                "— starting fresh with empty trade registry.",
                cancelled,
            )
        except Exception as e:
            logger.error("Failed to cancel stale trades on startup: %s", e)

    def load_active_trades(self):
        """
        Reload persisted trades from the database on startup (recovery after restart).

        NOTE: Not called in normal operation — use cancel_stale_trades() instead.
        This method is kept for debugging / manual recovery scenarios only.
        """
        try:
            rows = self._db.get_active_trades()
            count = 0
            for row in rows:
                trade = self._row_to_trade(row)
                if trade:
                    tf_pair = f"{trade.higher_tf}-{trade.lower_tf}"
                    if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
                        logger.info(
                            "Stored trade skipped: disabled timeframe pair %s | UUID %s",
                            tf_pair,
                            trade.trade_uuid[:8],
                        )
                        continue
                    with self._lock:
                        self._trades[trade.trade_uuid] = trade
                        if trade.status == TradeStatus.PENDING:
                            self._pending_activation_skip.add(trade.trade_uuid)
                    count += 1
            logger.info("Loaded %d active trades from database.", count)
        except Exception as e:
            logger.error("Failed to load active trades: %s", e)

    # ─────────────────────────────────────────────────────
    # PRICE UPDATE (called every scan cycle)
    # ─────────────────────────────────────────────────────

    def update(self, current_price: float):
        """
        Process all active trades against the current market price.
        Must be called on every price tick or scan cycle.
        """
        with self._lock:
            uuids = list(self._trades.keys())

        for uuid in uuids:
            with self._lock:
                trade = self._trades.get(uuid)
            if trade is None:
                continue
            self._process_trade(trade, current_price)

    def _process_trade(self, trade: Trade, price: float):
        """Evaluate pending fills, then SL and TP levels for active trades."""

        if trade.status == TradeStatus.PENDING:
            if trade.trade_uuid in self._pending_activation_skip:
                self._pending_activation_skip.discard(trade.trade_uuid)
                logger.debug(
                    "PENDING FILL CHECK DEFERRED: %s %s | entry %.2f | avoids same-scan activation",
                    trade.direction, trade.pair, trade.entry_price,
                )
                return

            if self._is_pending_order_filled(trade, price):
                self._activate_trade(trade, price)
            return

        # Only process trades that are being tracked (ACTIVE + TP states)
        if trade.status not in (
            TradeStatus.ACTIVE, TradeStatus.TP1_HIT, TradeStatus.TP2_HIT,
            TradeStatus.TP3_HIT, TradeStatus.TP4_HIT,
        ):
            return

        # Check stop loss first
        if self._is_sl_hit(trade, price):
            self._handle_sl_hit(trade, price)
            return

        # Check next unhit TP
        next_tp_idx = trade.hit_count
        if next_tp_idx < len(trade.tp_levels):
            if self._is_tp_hit(trade, price, next_tp_idx):
                self._handle_tp_hit(trade, next_tp_idx, price)

    # ─────────────────────────────────────────────────────
    # STATE TRANSITIONS
    # ─────────────────────────────────────────────────────

    def _activate_trade(self, trade: Trade, price: float):
        trade.status = TradeStatus.ACTIVE
        trade.activated_at = datetime.utcnow()
        if "filled" not in (trade.notes or "").lower():
            trade.notes = f"{trade.notes}; retest filled @ {price:.2f}".strip("; ")

        logger.info(
            "REVISIT FILL DETECTED: %s %s | level revisited after confirmation | trade active | price=%.2f | entry=%.2f",
            trade.direction, trade.pair, price, trade.entry_price,
        )
        logger.info(
            "ACTIVE TRADE TRACKING STARTED: %s %s | entry_time=%s | entry=%.2f",
            trade.direction,
            trade.pair,
            trade.activated_at.isoformat(),
            trade.entry_price,
        )

        try:
            self._db.update_trade_status(
                trade.trade_uuid,
                TradeStatus.ACTIVE,
                activated_at=trade.activated_at.isoformat(),
                notes=trade.notes,
            )
        except Exception as e:
            logger.error("DB update failed for pending fill activation: %s", e)

        self._telegram.send_trade_executed(trade)

    def _handle_tp_hit(self, trade: Trade, tp_index: int, price: float):
        trade.tp_hit[tp_index] = True
        trade.status = TradeStatus.from_tp_index(tp_index)

        # TP1 → move SL to Break Even
        if tp_index == 0 and not trade.be_moved:
            trade.sl_price = trade.entry_price
            trade.be_moved = True
            trade.protected_after_tp1 = True
            logger.info(
                "TP1 HIT: %s %s | move SL to BE | entry=%.2f | current=%.2f | UUID %s",
                trade.direction, trade.pair, trade.entry_price, price, trade.trade_uuid[:8],
            )
            logger.info(
                "TP HIT → WIN (TP1) | %s | SL moved to BE @ %.2f",
                trade.trade_uuid[:8], trade.entry_price,
            )
        else:
            logger.info(
                "TP HIT → %s (TP%d) | %s",
                "STRONG_WIN" if tp_index >= 1 else "WIN",
                tp_index + 1,
                trade.trade_uuid[:8],
            )

        # TP5 → trade completed with STRONG_WIN (all targets reached)
        if tp_index == 4:
            trade.status  = TradeStatus.COMPLETED
            trade.result  = TradeResult.STRONG_WIN
            trade.realized_pips = self._realized_pips(trade)
            trade.closed_at = datetime.utcnow()
            logger.info(
                "TRADE COMPLETED → STRONG_WIN | %s | all %d TPs hit",
                trade.trade_uuid[:8], trade.hit_count,
            )
            self._remove_trade(trade.trade_uuid)
            self._notify_learning(trade)

        try:
            self._db.update_tp_hit(trade.trade_uuid, tp_index)
            self._db.update_trade_status(
                trade.trade_uuid,
                trade.status,
                sl_price=trade.sl_price,
                be_moved=trade.be_moved,
                tp_progress_reached=trade.hit_count,
                protected_after_tp1=trade.protected_after_tp1,
                tp1_alert_sent=trade.tp1_alert_sent,
                result=trade.result,
                closed_at=trade.closed_at,
            )
        except Exception as e:
            logger.error("DB update failed for TP hit: %s", e)

        # TRADE_UPDATE: COMPLETED supersedes TP_HIT when all 5 TPs are reached
        if tp_index == 4:
            self._telegram.send_trade_update(trade, "COMPLETED")
        else:
            if tp_index == 0 and not trade.tp1_alert_sent:
                self._telegram.send_trade_update(trade, "TP_HIT", tp_index, current_price=price)
                trade.tp1_alert_sent = True
                try:
                    self._db.update_trade_status(
                        trade.trade_uuid,
                        trade.status,
                        tp1_alert_sent=True,
                        protected_after_tp1=True,
                    )
                except Exception as e:
                    logger.error("DB update failed for TP1 alert flag: %s", e)
            elif tp_index > 0:
                self._telegram.send_trade_update(trade, "TP_HIT", tp_index, current_price=price)

    def _handle_sl_hit(self, trade: Trade, price: float):
        trade.status = TradeStatus.STOP_LOSS_HIT
        trade.result = self._classify_exit_result(trade)
        trade.breakeven_exit = bool(trade.protected_after_tp1 and abs(price - trade.entry_price) <= PIP_SIZE)
        trade.realized_pips = self._realized_pips(trade)
        trade.closed_at = datetime.utcnow()
        exit_note = "loss before TP1"
        if trade.result != TradeResult.LOSS:
            exit_note = f"protected exit after TP1 ({trade.result})"
        logger.info(
            "SL/BE EXIT: %s | %s @ %.2f | TPs hit: %d",
            exit_note, trade.trade_uuid[:8], price, trade.hit_count,
        )

        try:
            self._db.update_trade_status(
                trade.trade_uuid,
                TradeStatus.STOP_LOSS_HIT,
                result=trade.result,
                closed_at=trade.closed_at,
                tp_progress_reached=trade.hit_count,
                protected_after_tp1=trade.protected_after_tp1,
                tp1_alert_sent=trade.tp1_alert_sent,
                breakeven_exit=trade.breakeven_exit,
            )
        except Exception as e:
            logger.error("DB update failed for SL hit: %s", e)

        self._telegram.send_trade_update(trade, "SL_HIT")
        self._notify_learning(trade)
        self._remove_trade(trade.trade_uuid)

    # ─────────────────────────────────────────────────────
    # PRICE HIT DETECTION
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _classify_exit_result(trade: Trade) -> str:
        """Classify the outcome using protected-after-TP1 trade management."""
        tps_hit = trade.hit_count
        if tps_hit <= 0:
            return TradeResult.LOSS
        if tps_hit == 1:
            return TradeResult.BREAKEVEN_WIN if trade.be_moved else TradeResult.PARTIAL_WIN
        if tps_hit == 2:
            return TradeResult.WIN
        return TradeResult.STRONG_WIN

    @staticmethod
    def _realized_pips(trade: Trade) -> float:
        """Approximate managed-trade realized pips for learning reward shaping."""
        from utils.helpers import price_to_pips

        if trade.hit_count <= 0:
            return -price_to_pips(abs(trade.entry_price - trade.sl_price))
        idx = min(trade.hit_count, len(trade.tp_levels)) - 1
        if idx < 0:
            return 0.0
        return price_to_pips(abs(trade.tp_levels[idx] - trade.entry_price))

    @staticmethod
    def _is_pending_order_filled(trade: Trade, price: float) -> bool:
        """Return True when price revisits the pending entry level."""
        tolerance = PENDING_ORDER_FILL_TOLERANCE_PIPS * PIP_SIZE
        return abs(price - trade.entry_price) <= tolerance

    @staticmethod
    def _is_sl_hit(trade: Trade, price: float) -> bool:
        """Return True if price has crossed or touched the SL."""
        if trade.direction == "SELL":
            return price >= trade.sl_price
        return price <= trade.sl_price

    @staticmethod
    def _is_tp_hit(trade: Trade, price: float, tp_idx: int) -> bool:
        """Return True if price has reached the specified TP level."""
        tp = trade.tp_levels[tp_idx]
        if trade.direction == "SELL":
            return price <= tp
        return price >= tp

    # ─────────────────────────────────────────────────────
    # REGISTRY HELPERS
    # ─────────────────────────────────────────────────────

    def _notify_learning(self, trade: Trade):
        """Feed a closed trade's result into the learning engine."""
        if self._learning is None:
            return
        if trade.activated_at is None:
            logger.info(
                "LEARNING SKIPPED: pending order was never filled | %s %s | UUID %s",
                trade.direction,
                trade.pair,
                trade.trade_uuid[:8],
            )
            return
        if trade.result not in (
            TradeResult.PARTIAL_WIN,
            TradeResult.BREAKEVEN_WIN,
            TradeResult.WIN,
            TradeResult.STRONG_WIN,
            TradeResult.LOSS,
        ):
            return
        try:
            tf_pair = f"{trade.higher_tf}-{trade.lower_tf}"
            if tf_pair not in ACTIVE_TIMEFRAME_PAIR_LABELS:
                logger.info(
                    "LEARNING SKIPPED: disabled timeframe pair %s | UUID %s",
                    tf_pair,
                    trade.trade_uuid[:8],
                )
                return
            tps_hit = sum(trade.tp_hit)
            self._learning.process_trade_result(
                level_type=trade.level_type,
                tf_pair=tf_pair,
                result=trade.result,
                tps_hit=tps_hit,
                setup_type=getattr(trade, "setup_type", "major"),
                session_name=getattr(trade, "session_name", ""),
                is_qm=getattr(trade, "is_qm", False),
                is_psychological=getattr(trade, "is_psychological", False),
                micro_confirmation_type=getattr(trade, "micro_confirmation_type", ""),
                bias_gate_result=getattr(trade, "bias_gate_result", ""),
                pd_location=getattr(trade, "pd_location", ""),
                realized_pips=getattr(trade, "realized_pips", 0.0),
            )
            logger.info("Learning engine updated: %s %s|%s → %s (%d TPs)",
                        trade.level_type, trade.higher_tf, trade.lower_tf,
                        trade.result, tps_hit)
        except Exception as e:
            logger.error("Failed to update learning engine: %s", e)

    def _remove_trade(self, uuid: str):
        with self._lock:
            self._trades.pop(uuid, None)
            self._pending_activation_skip.discard(uuid)

    def get_all_trades(self) -> List[Trade]:
        with self._lock:
            return list(self._trades.values())

    def get_pending_trades(self) -> List[Trade]:
        with self._lock:
            return [t for t in self._trades.values()
                    if t.status == TradeStatus.PENDING]

    def get_active_trades(self) -> List[Trade]:
        with self._lock:
            return [t for t in self._trades.values()
                    if t.status not in (TradeStatus.PENDING,
                                        TradeStatus.COMPLETED,
                                        TradeStatus.STOP_LOSS_HIT,
                                        TradeStatus.CANCELLED)]

    # ─────────────────────────────────────────────────────
    # DAILY STATS
    # ─────────────────────────────────────────────────────

    def get_daily_stats(self) -> dict:
        """Compute today's stats from database."""
        try:
            today_trades = self._db.get_today_trades()
            total = len(today_trades)
            activated = 0
            for t in today_trades:
                if isinstance(t, dict):
                    if t.get("activated_at") or t.get("status") not in (TradeStatus.PENDING, TradeStatus.CANCELLED):
                        activated += 1
                elif isinstance(t, tuple) and len(t) > 24:
                    if t[24] or (len(t) > 16 and t[16] not in (TradeStatus.PENDING, TradeStatus.CANCELLED)):
                        activated += 1
            wins = sum(
                1 for t in today_trades
                if (
                    isinstance(t, dict)
                    and t.get("result") in (
                        TradeResult.PARTIAL_WIN,
                        TradeResult.BREAKEVEN_WIN,
                        TradeResult.WIN,
                        TradeResult.STRONG_WIN,
                    )
                )
                or (
                    isinstance(t, tuple) and len(t) > 21
                    and t[21] in (
                        TradeResult.PARTIAL_WIN,
                        TradeResult.BREAKEVEN_WIN,
                        TradeResult.WIN,
                        TradeResult.STRONG_WIN,
                    )
                )
            )
            losses = sum(
                1 for t in today_trades
                if (
                    isinstance(t, dict)
                    and t.get("result") == TradeResult.LOSS
                )
                or (
                    isinstance(t, tuple) and len(t) > 21 and t[21] == TradeResult.LOSS
                )
            )
            win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0
            return {
                "total_setups": total,
                "activated": activated,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate * 100, 1),
            }
        except Exception as e:
            logger.error("Failed to compute daily stats: %s", e)
            return {"total_setups": 0, "activated": 0, "wins": 0, "losses": 0, "win_rate": 0.0}

    # ─────────────────────────────────────────────────────
    # DB ROW → TRADE
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _row_to_trade(row) -> Optional[Trade]:
        """
        Reconstruct a Trade from a DB row.
        Handles both Supabase (dict) and PostgreSQL (tuple) row formats.

        PostgreSQL column order (CREATE TABLE in database.py):
          0:id  1:trade_uuid  2:pair  3:direction  4:entry_price  5:sl_price
          6:tp1 7:tp2 8:tp3 9:tp4 10:tp5
          11:tp1_hit 12:tp2_hit 13:tp3_hit 14:tp4_hit 15:tp5_hit
          16:status  17:level_type  18:level_price  19:higher_tf  20:lower_tf
          21:result  22:confidence  23:created_at  24:activated_at  25:closed_at
          26:be_moved  27:notes
        """
        try:
            if not row:
                return None

            if isinstance(row, dict):
                # ── Supabase REST returns dicts ─────────────────────────
                tp_levels = [
                    float(row[f"tp{i}"]) for i in range(1, 6)
                    if row.get(f"tp{i}") is not None
                ]
                tp_hit = [
                    bool(row.get("tp1_hit", False)),
                    bool(row.get("tp2_hit", False)),
                    bool(row.get("tp3_hit", False)),
                    bool(row.get("tp4_hit", False)),
                    bool(row.get("tp5_hit", False)),
                ]
                trade = Trade(
                    pair=row.get("pair", "XAUUSD"),
                    direction=row["direction"],
                    entry_price=float(row["entry_price"]),
                    sl_price=float(row["sl_price"]),
                    tp_levels=tp_levels,
                    level_type=row.get("level_type", ""),
                    level_price=float(row["level_price"]) if row.get("level_price") else 0.0,
                    higher_tf=row.get("higher_tf", ""),
                    lower_tf=row.get("lower_tf", ""),
                    confidence=float(row.get("confidence") or 0.5),
                    setup_type=row.get("setup_type", "major"),
                    is_qm=bool(row.get("is_qm", False)),
                    is_psychological=bool(row.get("is_psychological", False)),
                    is_liquidity_sweep=bool(row.get("is_liquidity_sweep", False)),
                    session_name=row.get("session_name", ""),
                    h4_bias=row.get("h4_bias", ""),
                    trend_aligned=bool(row.get("trend_aligned", True)),
                )
                trade.db_id = row.get("id")
                trade.trade_uuid = row.get("trade_uuid", str(uuid_mod.uuid4()))
                trade.status = row.get("status", TradeStatus.PENDING)
                trade.tp_hit = tp_hit
                trade.be_moved = bool(row.get("be_moved", False))
                trade.tp1_alert_sent = bool(row.get("tp1_alert_sent", False))
                trade.protected_after_tp1 = bool(row.get("protected_after_tp1", False))
                trade.breakeven_exit = bool(row.get("breakeven_exit", False))
                trade.result = row.get("result")
                trade.activated_at = row.get("activated_at")
                trade.closed_at = row.get("closed_at")
                trade.notes = row.get("notes", "")
                return trade

            else:
                # ── PostgreSQL tuple ────────────────────────────────────
                def _f(idx, default=None):
                    return row[idx] if len(row) > idx and row[idx] is not None else default

                tp_levels = [float(row[i]) for i in range(6, 11)
                             if len(row) > i and row[i] is not None]
                tp_hit = [bool(_f(i, False)) for i in range(11, 16)]

                trade = Trade(
                    pair=_f(2, "XAUUSD"),
                    direction=row[3],
                    entry_price=float(row[4]),
                    sl_price=float(row[5]),
                    tp_levels=tp_levels,
                    level_type=_f(17, ""),
                    level_price=float(_f(18, 0)),
                    higher_tf=_f(19, ""),
                    lower_tf=_f(20, ""),
                    confidence=float(_f(22, 0.5)),
                )
                trade.db_id = row[0]
                trade.trade_uuid = row[1]
                trade.status = _f(16, TradeStatus.PENDING)
                trade.result = _f(21)
                trade.activated_at = _f(24)
                trade.closed_at = _f(25)
                trade.tp_hit = tp_hit
                trade.be_moved = bool(_f(26, False))
                trade.protected_after_tp1 = bool(_f(28, False))
                trade.tp1_alert_sent = bool(_f(29, False))
                trade.breakeven_exit = bool(_f(30, False))
                trade.notes = _f(31, _f(27, ""))
                return trade

        except Exception as e:
            logger.error("Failed to reconstruct trade from DB row: %s", e)
            return None
