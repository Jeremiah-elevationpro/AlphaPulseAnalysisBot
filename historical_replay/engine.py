from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import pandas as pd

from config.settings import (
    PENDING_ORDER_FILL_TOLERANCE_PIPS,
    PIP_SIZE,
    ACTIVE_TIMEFRAME_PAIR_LABELS,
    DISABLED_TIMEFRAME_PAIRS,
    MICRO_CONFIRMATION_ENABLED,
    REPLAY_DEFAULT_MONTHS,
    REPLAY_STEP_TIMEFRAME,
    REPLAY_WARMUP_DAYS,
    SYMBOL,
)
from data.mt5_client import MT5Client
from db.database import Database
from db.models import TradeStatus
from signals.signal_generator import SignalGenerator
from strategies.filters import SessionFilter
from strategies.strategy_manager import StrategyManager, StrategySignal
from historical_replay.models import PendingReplayTrade, ReplayCounters
from historical_replay.storage import ReplayStorage
from utils.logger import get_logger

logger = get_logger(__name__)


_TF_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


class HistoricalReplayEngine:
    """
    Candle-by-candle historical replay for AlphaPulse.

    The live bot is not imported or mutated. Replay feeds historical snapshots
    into StrategyManager, then applies the pending-order retest/fill model in
    this module and stores only activated replay trades in Supabase.
    """

    def __init__(
        self,
        *,
        db: Optional[Database] = None,
        mt5: Optional[MT5Client] = None,
        strategy_manager: Optional[StrategyManager] = None,
        signal_generator: Optional[SignalGenerator] = None,
    ):
        self.db = db or Database()
        self.mt5 = mt5 or MT5Client()
        self.strategy_manager = strategy_manager or StrategyManager(
            learning_engine=None,
            enabled_strategies=["gap_sweep"],
        )
        self.signal_generator = signal_generator or SignalGenerator(learning_engine=None)
        self.session_filter = SessionFilter()
        self.storage = ReplayStorage(self.db)
        self._enabled_replay_strategies = list(getattr(self.strategy_manager, "enabled_strategies", ["gap_sweep"]))
        if DISABLED_TIMEFRAME_PAIRS:
            disabled = ", ".join(f"{high}->{low}" for high, low in DISABLED_TIMEFRAME_PAIRS)
            logger.info("Historical replay disabled timeframe pair(s): %s", disabled)
        if not self._is_active_tf_pair("H4->H1"):
            logger.info("H4->H1 disabled for active intraday strategy; replay excludes H4->H1.")

    def run_last_months(self, months: int = REPLAY_DEFAULT_MONTHS) -> Dict:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=months * 30)
        return self.run(start=start, end=end)

    def run(self, *, start: datetime, end: datetime, symbol: str = SYMBOL) -> Dict:
        start = self._as_utc(start)
        end = self._as_utc(end)
        replay_run_id: Optional[int] = None

        try:
            self.db.init()
            self.mt5.connect()
            replay_run_id = self.storage.start_run(
                symbol=symbol,
                start_time=start,
                end_time=end,
                strategy_version="current_alpha_pulse",
                notes="Candle-by-candle replay using current strategy stack",
            )

            history = self._load_history(start, end)
            if REPLAY_STEP_TIMEFRAME not in history or history[REPLAY_STEP_TIMEFRAME].empty:
                raise RuntimeError(f"No {REPLAY_STEP_TIMEFRAME} data available for replay")

            result = self._replay(replay_run_id, history, start, end)
            result["replay_run_id"] = replay_run_id
            self.storage.finish_run(replay_run_id, status="completed", counters=result)
            logger.info("Historical replay completed: %s", result)
            return result
        except Exception as exc:
            logger.error("Historical replay failed: %s", exc, exc_info=True)
            if replay_run_id is not None:
                self.storage.fail_run(replay_run_id, str(exc))
            raise
        finally:
            self.mt5.disconnect()
            self.db.close()

    def _load_history(self, start: datetime, end: datetime) -> Dict[str, pd.DataFrame]:
        warmup_start = start - timedelta(days=REPLAY_WARMUP_DAYS)
        data = {}
        for timeframe in self.strategy_manager.get_required_timeframes():
            df = self.mt5.get_ohlcv_range(timeframe, warmup_start, end)
            if df is None or df.empty:
                logger.warning("Replay data missing for %s", timeframe)
                continue
            data[timeframe] = df
            logger.info("Replay loaded %s candles: %d", timeframe, len(df))
        # ── Micro-confirmation data availability summary ──────────────────────
        for micro_tf in ("M5", "M1"):
            if micro_tf in data:
                logger.info("MICRO DEBUG: %s loaded — %d bars available for micro-confirmation", micro_tf, len(data[micro_tf]))
            else:
                logger.warning("MICRO DEBUG: %s NOT loaded — micro-confirmation will be neutral for all setups", micro_tf)
        return data

    def _replay(
        self,
        replay_run_id: int,
        history: Dict[str, pd.DataFrame],
        start: datetime,
        end: datetime,
    ) -> Dict:
        counters = ReplayCounters()
        strategy_balance = self._init_strategy_balance()
        seen_watchlists = set()
        seen_pending = set()
        pending: Dict[str, PendingReplayTrade] = {}
        activated_or_closed: Dict[str, PendingReplayTrade] = {}
        _snapshot_micro_logged = False
        logged_strategy_skip_reasons: set[tuple[str, str]] = set()

        step_df = history[REPLAY_STEP_TIMEFRAME]
        step_delta = timedelta(minutes=_TF_MINUTES.get(REPLAY_STEP_TIMEFRAME, 15))
        replay_rows = step_df[(step_df["time"] >= start) & (step_df["time"] <= end)]

        for _, row in replay_rows.iterrows():
            bar_time = self._to_datetime(row["time"])
            bar_close_time = bar_time + step_delta
            current_price = float(row["close"])
            snapshot = self._snapshot(history, bar_close_time)
            if not self._snapshot_ready(snapshot):
                for strategy_name in self._enabled_replay_strategies:
                    self._log_strategy_skipped_once(
                        logged_strategy_skip_reasons,
                        strategy_name,
                        "missing data",
                    )
                continue

            session_label = self.session_filter.get_session(bar_close_time)
            replay_allowed, local_time, _active_until = self.session_filter.is_bot_window_active(bar_close_time)
            logger.info(
                "REPLAY SESSION CHECK: local_time=%s | bot_window=07:00-19:00 | allowed=%s | session_label=%s",
                local_time,
                str(replay_allowed).lower(),
                session_label,
            )

            # Log once that M5/M1 are (or are not) in the snapshot
            if not _snapshot_micro_logged:
                _snapshot_micro_logged = True
                for micro_tf in ("M5", "M1"):
                    bars = len(snapshot.get(micro_tf, []))
                    if bars:
                        logger.info("MICRO DEBUG: snapshot[%s] has %d bars — detection will run", micro_tf, bars)
                    else:
                        logger.warning("MICRO DEBUG: snapshot[%s] absent — %s micro-confirmation disabled for this replay", micro_tf, micro_tf)

            self._update_replay_trades(
                replay_run_id=replay_run_id,
                pending=pending,
                activated_or_closed=activated_or_closed,
                row=row,
                bar_time=bar_close_time,
                counters=counters,
                strategy_balance=strategy_balance,
            )

            if not replay_allowed:
                logger.info("REPLAY SCAN SKIPPED: outside bot operating window | %s", local_time)
                for strategy_name in self._enabled_replay_strategies:
                    self._log_strategy_skipped_once(
                        logged_strategy_skip_reasons,
                        strategy_name,
                        "outside replay window",
                    )
                continue

            for strategy_name in self._enabled_replay_strategies:
                strategy_balance[strategy_name]["scans_run"] += 1

            run_result = self.strategy_manager.run(
                snapshot,
                current_price=current_price,
                analysis_time=bar_close_time,
            )
            signal_counts = defaultdict(int)
            for signal in run_result.signals:
                signal_counts[signal.strategy_name] += 1
            for strategy_name in self._enabled_replay_strategies:
                count = int(signal_counts.get(strategy_name, 0))
                strategy_balance[strategy_name]["candidates_found"] += count
                if strategy_name == "engulfing_rejection":
                    strategy_balance[strategy_name]["shortlisted"] += count
                    strategy_balance[strategy_name]["revisited"] += count
            self._count_watchlists(
                run_result.outlook,
                current_price,
                seen_watchlists,
                counters,
            )

            for signal in run_result.signals:
                if not self._is_active_tf_pair(signal.tf_pair_str):
                    logger.info("REPLAY SIGNAL SKIPPED: disabled timeframe pair %s", signal.tf_pair_str)
                    self._log_strategy_skipped_once(
                        logged_strategy_skip_reasons,
                        signal.strategy_name,
                        "unsupported timeframe",
                    )
                    continue
                key = signal.fingerprint()
                if key in seen_pending or key in pending or key in activated_or_closed:
                    continue

                trade, rejection = self.signal_generator.generate(signal.setup)
                if trade is None:
                    logger.debug("Replay signal rejected: %s", rejection)
                    continue

                pending_trade = self._make_pending_trade(
                    key=key,
                    signal=signal,
                    trade=trade,
                    pending_time=bar_close_time,
                    market_condition=run_result.market_condition,
                    outlook=run_result.outlook,
                )
                pending[key] = pending_trade
                seen_pending.add(key)
                counters.total_pending_order_ready += 1
                strategy_balance[signal.strategy_name]["pending_ready"] += 1
                level_type = getattr(getattr(signal.setup, "level", None), "level_type", "?")
                logger.info(
                    "REPLAY PENDING ORDER READY: %s %s %.2f | %s | %s",
                    trade.direction,
                    trade.pair,
                    trade.entry_price,
                    pending_trade.timeframe_pair,
                    bar_close_time,
                )
                if level_type in ("A", "V"):
                    logger.info(
                        "A/V PENDING-ORDER-READY: %s %.2f | %s %s | %s",
                        level_type, trade.entry_price, trade.direction, trade.pair, bar_close_time,
                    )

        replay_end_time = self._to_datetime(replay_rows.iloc[-1]["time"]) if len(replay_rows) else end
        for key, replay_trade in list(pending.items()):
            if replay_trade.activated:
                replay_trade.final_result = self._classify_replay_result(replay_trade, open_if_no_tp=True)
                replay_trade.failure_reason = "replay ended before SL/TP completion"
                replay_trade.closed_time = replay_end_time
                if replay_trade.final_result == "LOSS":
                    counters.total_losses += 1
                elif replay_trade.final_result != "OPEN":
                    counters.total_wins += 1
                self._record_closed_trade(strategy_balance, replay_trade)
                self._store_replay_trade(replay_run_id, replay_trade)
                activated_or_closed[key] = replay_trade
            else:
                logger.info("LEARNING SKIPPED: replay pending order was never filled | %s", key)
            pending.pop(key, None)

        stats_payload = self._build_stats_payload(
            counters,
            activated_or_closed.values(),
            strategy_balance=strategy_balance,
        )
        logger.info(
            "MULTI STRATEGY SCAN BALANCE: gap_sweep scans_run=%d candidates=%d activated=%d | "
            "engulfing_rejection scans_run=%d candidates=%d activated=%d",
            strategy_balance["gap_sweep"]["scans_run"],
            strategy_balance["gap_sweep"]["candidates_found"],
            strategy_balance["gap_sweep"]["activated_trades"],
            strategy_balance["engulfing_rejection"]["scans_run"],
            strategy_balance["engulfing_rejection"]["candidates_found"],
            strategy_balance["engulfing_rejection"]["activated_trades"],
        )
        try:
            self.storage.insert_stats(replay_run_id, stats_payload)
        except Exception as exc:
            # Stats storage is non-critical — trades are already stored.
            # Mark the payload so callers know, but do not abort the replay.
            logger.error(
                "REPLAY STATS STORAGE FAILED (trades preserved, replay marked stats_insert_failed) — %s. "
                "Run migrate_historical_replay_stats_strategy_scan_balance.sql in Supabase.",
                exc,
            )
            stats_payload["stats_insert_failed"] = True
        return stats_payload

    def _update_replay_trades(
        self,
        *,
        replay_run_id: int,
        pending: Dict[str, PendingReplayTrade],
        activated_or_closed: Dict[str, PendingReplayTrade],
        row: pd.Series,
        bar_time: datetime,
        counters: ReplayCounters,
        strategy_balance: Dict[str, Dict[str, float]],
    ):
        high = float(row["high"])
        low = float(row["low"])
        for key, replay_trade in list(pending.items()):
            trade = replay_trade.trade

            if not replay_trade.activated:
                if bar_time <= replay_trade.pending_order_ready_time:
                    continue
                if self._bar_touched_level(high, low, trade.entry_price):
                    replay_trade.activated = True
                    replay_trade.activation_time = bar_time
                    trade.status = TradeStatus.ACTIVE
                    trade.activated_at = bar_time
                    counters.total_activated_trades += 1
                    strategy_balance[trade.strategy_type]["activated_trades"] += 1
                    _activated_level = getattr(getattr(replay_trade.setup, "level", None), "level_type", "?")
                    logger.info(
                        "REPLAY ACTIVATED: %s %s | entry=%.2f | time=%s",
                        trade.direction, trade.pair, trade.entry_price, bar_time,
                    )
                    if _activated_level in ("A", "V"):
                        logger.info(
                            "A/V ACTIVATED: %s %.2f | %s %s | time=%s",
                            _activated_level, trade.entry_price, trade.direction, trade.pair, bar_time,
                        )
                else:
                    continue

            self._update_excursions(replay_trade, high, low)
            if self._is_sl_hit(trade, high, low):
                replay_trade.final_result = self._classify_replay_result(replay_trade)
                replay_trade.closed = True
                replay_trade.closed_time = bar_time
                replay_trade.breakeven_exit = bool(replay_trade.protected_after_tp1)
                replay_trade.failure_reason = (
                    "stop_loss_hit_before_tp1"
                    if replay_trade.final_result == "LOSS"
                    else "protected_be_exit_after_tp1"
                )
                if replay_trade.final_result == "LOSS":
                    counters.total_losses += 1
                else:
                    counters.total_wins += 1
                self._record_closed_trade(strategy_balance, replay_trade)
                self._store_replay_trade(replay_run_id, replay_trade)
                activated_or_closed[key] = replay_trade
                pending.pop(key, None)
                continue

            next_tp = replay_trade.tp_progress
            while next_tp < len(trade.tp_levels) and self._is_tp_hit(trade, high, low, next_tp):
                replay_trade.tp_hit[next_tp] = True
                counters.tp_hits[next_tp] += 1
                if next_tp == 0:
                    trade.sl_price = trade.entry_price
                    replay_trade.protected_after_tp1 = True
                    replay_trade.tp1_alert_sent = True
                    logger.info(
                        "REPLAY TP1 HIT: %s %s | move SL to BE | entry=%.2f | current=%.2f",
                        trade.direction, trade.pair, trade.entry_price, trade.tp_levels[next_tp],
                    )
                next_tp += 1

            if replay_trade.tp_progress >= len(trade.tp_levels):
                replay_trade.final_result = "STRONG_WIN"
                replay_trade.closed = True
                replay_trade.closed_time = bar_time
                counters.total_wins += 1
                self._record_closed_trade(strategy_balance, replay_trade)
                self._store_replay_trade(replay_run_id, replay_trade)
                activated_or_closed[key] = replay_trade
                pending.pop(key, None)

    def _store_replay_trade(self, replay_run_id: int, replay_trade: PendingReplayTrade):
        try:
            self.storage.insert_trade(replay_run_id, replay_trade.to_supabase_payload(replay_run_id))
        except Exception as exc:
            # Log and skip — a schema mismatch on one trade must not abort the whole replay.
            # Check the Supabase error body logged above to identify missing columns,
            # then run migrate_replay_pip_metrics.sql in the Supabase SQL Editor.
            trade = replay_trade.trade
            logger.error(
                "REPLAY TRADE STORAGE FAILED (skipped) — %s %s entry=%.2f result=%s | %s",
                trade.direction, trade.pair, trade.entry_price,
                replay_trade.final_result, exc,
            )

    def _make_pending_trade(
        self,
        *,
        key: str,
        signal: StrategySignal,
        trade,
        pending_time: datetime,
        market_condition: str,
        outlook,
    ) -> PendingReplayTrade:
        ctx = getattr(outlook, "context", None)
        confirmation_type = getattr(signal.setup.confirmation, "confirmation_type", "rejection")
        confirmation = "first rejection closed correctly"
        if trade.direction == "BUY":
            confirmation = f"{confirmation_type}: first bearish rejection closed above support ({trade.lower_tf})"
        elif trade.direction == "SELL":
            confirmation = f"{confirmation_type}: first bullish rejection closed below resistance ({trade.lower_tf})"

        micro_type  = getattr(signal.setup, "micro_confirmation_type", "none") or "none"
        micro_score = float(getattr(signal.setup, "micro_confirmation_score", 0.0) or 0.0)
        micro_dec   = getattr(signal.setup, "micro_layer_decision", "neutral") or "neutral"
        h1_sweep = bool(getattr(signal.setup, "h1_liquidity_sweep", False))
        h1_sweep_dir = getattr(signal.setup, "h1_sweep_direction", "none") or "none"
        h1_reclaim = bool(getattr(signal.setup, "h1_reclaim_confirmed", False))
        pd_location = getattr(signal.setup, "pd_location", "unknown") or "unknown"
        pd_score = float(getattr(signal.setup, "pd_filter_score", 0.0) or 0.0)
        bias_gate = getattr(signal.setup, "bias_gate_result", "not_checked") or "not_checked"
        high_quality = bool(getattr(signal.setup, "high_quality_trade", False))
        micro_strength = getattr(signal.setup, "micro_strength", "normal") or "normal"
        logger.info(
            "MICRO DEBUG: pending trade created | %s %s | micro=%s score=%+g strength=%s decision=%s high_quality=%s",
            trade.direction, trade.pair, micro_type, micro_score,
            micro_strength, micro_dec, high_quality,
        )

        return PendingReplayTrade(
            key=key,
            trade=trade,
            setup=signal.setup,
            pending_order_ready_time=pending_time,
            confirmation_pattern=confirmation,
            market_condition=market_condition,
            micro_confirmation_type=micro_type,
            micro_confirmation_score=micro_score,
            micro_layer_decision=micro_dec,
            h1_liquidity_sweep=h1_sweep,
            h1_sweep_direction=h1_sweep_dir,
            h1_reclaim_confirmed=h1_reclaim,
            pd_location=pd_location,
            pd_filter_score=pd_score,
            bias_gate_result=bias_gate,
            high_quality_trade=high_quality,
            micro_strength=micro_strength,
            dominant_bias=getattr(ctx, "dominant_bias", trade.h4_bias) if ctx else trade.h4_bias,
            bias_strength=getattr(ctx, "bias_strength", "") if ctx else "",
            h1_state=getattr(ctx, "h1_state", "") if ctx else "",
        )

    def _count_watchlists(self, outlook, current_price: float, seen: set, counters: ReplayCounters):
        for tfl in outlook.timeframe_levels:
            tf_pair = f"{tfl.higher_tf}->{tfl.lower_tf}"
            if not self._is_active_tf_pair(tf_pair):
                logger.info("REPLAY WATCHLIST SKIPPED: disabled timeframe pair %s", tf_pair)
                continue
            for level in tfl.levels + tfl.recent_levels + tfl.previous_levels:
                direction = self._level_trade_direction(level, current_price)
                key = f"{outlook.pair}:{direction}:{tf_pair}:{round(level.price, 2)}"
                if key not in seen:
                    seen.add(key)
                    counters.total_watchlists += 1

    @staticmethod
    def _build_stats_payload(
        counters: ReplayCounters,
        replay_trades: Iterable[PendingReplayTrade],
        *,
        strategy_balance: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> Dict:
        trades = list(replay_trades)
        activated = counters.total_activated_trades
        by_tf = HistoricalReplayEngine._group_performance(trades, "timeframe_pair")
        by_bias = HistoricalReplayEngine._group_performance(trades, "dominant_bias")
        by_session = HistoricalReplayEngine._group_performance(trades, "session_name")
        by_setup = HistoricalReplayEngine._group_performance(trades, "setup_type")
        by_micro = HistoricalReplayEngine._group_performance(trades, "micro_confirmation_type")
        by_sweep = HistoricalReplayEngine._group_performance(trades, "h1_sweep_direction")
        by_pd = HistoricalReplayEngine._group_performance(trades, "pd_location")
        by_bias_gate = HistoricalReplayEngine._group_performance(trades, "bias_gate_result")

        return {
            "total_watchlists": counters.total_watchlists,
            "total_pending_order_ready": counters.total_pending_order_ready,
            "total_activated_trades": activated,
            "total_wins": counters.total_wins,
            "total_losses": counters.total_losses,
            "tp1_hit_rate": HistoricalReplayEngine._rate(counters.tp_hits[0], activated),
            "tp2_hit_rate": HistoricalReplayEngine._rate(counters.tp_hits[1], activated),
            "tp3_hit_rate": HistoricalReplayEngine._rate(counters.tp_hits[2], activated),
            "performance_by_timeframe_pair": by_tf,
            "performance_by_bias": by_bias,
            "performance_by_session": by_session,
            "performance_by_setup_type": by_setup,
            "performance_by_micro_confirmation": by_micro,
            "performance_by_h1_sweep": by_sweep,
            "performance_by_pd_location": by_pd,
            "performance_by_bias_gate": by_bias_gate,
            "pip_summary": HistoricalReplayEngine._pip_summary(trades),
            "pip_by_timeframe_pair": by_tf,
            "pip_by_bias": by_bias,
            "pip_by_session": by_session,
            "strategy_scan_balance": strategy_balance or {},
        }

    @staticmethod
    def _init_strategy_balance() -> Dict[str, Dict[str, float]]:
        return {
            "gap_sweep": {
                "scans_run": 0,
                "candidates_found": 0,
                "pending_ready": 0,
                "activated_trades": 0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "final_pips_total": 0.0,
                "missing_pips_count": 0,
            },
            "engulfing_rejection": {
                "scans_run": 0,
                "candidates_found": 0,
                "shortlisted": 0,
                "revisited": 0,
                "pending_ready": 0,
                "activated_trades": 0,
                "closed_trades": 0,
                "wins": 0,
                "losses": 0,
                "final_pips_total": 0.0,
                "missing_pips_count": 0,
            },
        }

    @staticmethod
    def _record_closed_trade(strategy_balance: Dict[str, Dict[str, float]], replay_trade: PendingReplayTrade) -> None:
        strategy_name = getattr(replay_trade.trade, "strategy_type", "gap_sweep") or "gap_sweep"
        if strategy_name not in strategy_balance:
            return
        bucket = strategy_balance[strategy_name]
        bucket["closed_trades"] += 1
        if replay_trade.final_result == "LOSS":
            bucket["losses"] += 1
        else:
            bucket["wins"] += 1
        final_pips = replay_trade.final_pips
        if final_pips is None:
            bucket["missing_pips_count"] += 1
            return
        bucket["final_pips_total"] += round(float(final_pips), 2)

    @staticmethod
    def _log_strategy_skipped_once(
        logged: set[tuple[str, str]],
        strategy_name: str,
        reason: str,
    ) -> None:
        key = (strategy_name, reason)
        if key in logged:
            return
        logged.add(key)
        logger.info("STRATEGY SKIPPED: strategy=%s | reason=%s", strategy_name, reason)

    @staticmethod
    def _group_performance(trades: Iterable[PendingReplayTrade], attr: str) -> Dict:
        groups = defaultdict(
            lambda: {
                "activated": 0,
                "wins": 0,
                "losses": 0,
                "tp1_hits": 0,
                "total_pips_gained": 0.0,
                "total_pips_lost": 0.0,
                "net_pips": 0.0,
            }
        )
        for replay_trade in trades:
            if attr == "timeframe_pair":
                key = replay_trade.timeframe_pair
            elif attr == "dominant_bias":
                key = replay_trade.dominant_bias or "unknown"
            elif attr == "session_name":
                key = replay_trade.trade.session_name or "off-session"
            elif attr == "setup_type":
                key = replay_trade.trade.setup_type or "unknown"
            elif attr == "micro_confirmation_type":
                key = replay_trade.micro_confirmation_type or "none"
            elif attr == "h1_sweep_direction":
                key = replay_trade.h1_sweep_direction or "none"
            elif attr == "pd_location":
                key = replay_trade.pd_location or "unknown"
            elif attr == "bias_gate_result":
                key = replay_trade.bias_gate_result or "unknown"
            else:
                key = "unknown"

            bucket = groups[key]
            bucket["activated"] += 1
            bucket["wins"] += 1 if replay_trade.final_result in (
                "PARTIAL_WIN",
                "BREAKEVEN_WIN",
                "WIN",
                "STRONG_WIN",
            ) else 0
            bucket["losses"] += 1 if replay_trade.final_result == "LOSS" else 0
            bucket["tp1_hits"] += 1 if replay_trade.tp_progress >= 1 else 0
            final_pips = replay_trade.final_pips
            if final_pips >= 0:
                bucket["total_pips_gained"] += final_pips
            else:
                bucket["total_pips_lost"] += abs(final_pips)
            bucket["net_pips"] += final_pips

        for bucket in groups.values():
            bucket["win_rate"] = HistoricalReplayEngine._rate(bucket["wins"], bucket["wins"] + bucket["losses"])
            bucket["tp1_hit_rate"] = HistoricalReplayEngine._rate(bucket["tp1_hits"], bucket["activated"])
            bucket["total_pips_gained"] = round(bucket["total_pips_gained"], 2)
            bucket["total_pips_lost"] = round(bucket["total_pips_lost"], 2)
            bucket["net_pips"] = round(bucket["net_pips"], 2)
            bucket["avg_pips_per_trade"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
        return dict(groups)

    @staticmethod
    def _pip_summary(trades: Iterable[PendingReplayTrade]) -> Dict:
        trade_list = list(trades)
        gained = sum(max(0.0, trade.final_pips) for trade in trade_list)
        lost = sum(abs(min(0.0, trade.final_pips)) for trade in trade_list)
        net = gained - lost
        return {
            "total_pips_gained": round(gained, 2),
            "total_pips_lost": round(lost, 2),
            "net_pips": round(net, 2),
            "average_pips_per_trade": round(net / len(trade_list), 2) if trade_list else 0.0,
        }

    @staticmethod
    def _snapshot(history: Dict[str, pd.DataFrame], current_time: datetime) -> Dict[str, pd.DataFrame]:
        snapshot = {}
        for timeframe, df in history.items():
            minutes = _TF_MINUTES.get(timeframe, 60)
            close_time = df["time"] + pd.to_timedelta(minutes, unit="m")
            visible = df[close_time <= current_time]
            if not visible.empty:
                snapshot[timeframe] = visible.reset_index(drop=True)
        return snapshot

    @staticmethod
    def _snapshot_ready(snapshot: Dict[str, pd.DataFrame]) -> bool:
        minimums = {"D1": 30, "H4": 50, "H1": 80, "M30": 100, "M15": 100}
        if MICRO_CONFIRMATION_ENABLED:
            minimums["M5"] = 100
        for timeframe, minimum in minimums.items():
            if timeframe not in snapshot or len(snapshot[timeframe]) < minimum:
                return False
        return bool(snapshot)

    @staticmethod
    def _is_active_tf_pair(tf_pair: str) -> bool:
        return str(tf_pair).replace("->", "-").replace(" ", "") in ACTIVE_TIMEFRAME_PAIR_LABELS

    @staticmethod
    def _bar_touched_level(high: float, low: float, level: float) -> bool:
        tolerance = PENDING_ORDER_FILL_TOLERANCE_PIPS * PIP_SIZE
        return low - tolerance <= level <= high + tolerance

    @staticmethod
    def _is_sl_hit(trade, high: float, low: float) -> bool:
        if trade.direction == "SELL":
            return high >= trade.sl_price
        return low <= trade.sl_price

    @staticmethod
    def _is_tp_hit(trade, high: float, low: float, tp_idx: int) -> bool:
        tp = trade.tp_levels[tp_idx]
        if trade.direction == "SELL":
            return low <= tp
        return high >= tp

    @staticmethod
    def _classify_replay_result(
        replay_trade: PendingReplayTrade,
        *,
        open_if_no_tp: bool = False,
    ) -> str:
        progress = replay_trade.tp_progress
        if progress <= 0:
            return "OPEN" if open_if_no_tp else "LOSS"
        if progress == 1:
            return "BREAKEVEN_WIN" if replay_trade.protected_after_tp1 else "PARTIAL_WIN"
        if progress == 2:
            return "WIN"
        return "STRONG_WIN"

    @staticmethod
    def _update_excursions(replay_trade: PendingReplayTrade, high: float, low: float):
        entry = replay_trade.trade.entry_price
        if replay_trade.trade.direction == "BUY":
            favorable = high - entry
            adverse = entry - low
        else:
            favorable = entry - low
            adverse = high - entry
        replay_trade.max_favorable_excursion = max(replay_trade.max_favorable_excursion, favorable)
        replay_trade.max_adverse_excursion = max(replay_trade.max_adverse_excursion, adverse)

    @staticmethod
    def _level_trade_direction(level, current_price: float) -> str:
        explicit = getattr(level, "trade_direction", "")
        if explicit in ("BUY", "SELL"):
            return explicit
        if level.level_type == "A":
            return "SELL"
        if level.level_type == "V":
            return "BUY"
        return "SELL" if level.price >= current_price else "BUY"

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 3) if denominator else 0.0

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _to_datetime(value) -> datetime:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.to_pydatetime()
