"""
AlphaPulse — Main Orchestrator (Manual Execution Mode)
=======================================================
Pure analysis + signal assistant. No automatic trade execution.

Scan loop:
  1. Connect to MT5 + DB
  2. Load previously tracked setups (recovery)
  3. 5-minute silent analysis phase on startup (no Telegram alerts)
  4. Every SCAN_INTERVAL_SECONDS:
       a. Fetch OHLCV for all required timeframes
       b. Run StrategyManager → MarketOutlook + unified signals
       c. Watch-level approach alerts (deduplicated)
       d. For each new high-quality signal:
            i.  send_confirmation — pending-order / retest-entry alert
            ii. register_trade    — track pending order until retest fill
       e. Update tracked setups against live price (simulated)
       f. Periodically refresh learning engine
  5. Graceful shutdown on Ctrl+C
"""

import signal
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Set

import pandas as pd

from config.settings import (
    SCAN_INTERVAL_SECONDS, TIMEFRAME_PAIRS, MIN_SIGNAL_CONFIDENCE,
    WATCH_DISTANCE_PIPS, LEVEL_TOLERANCE_PIPS, PIP_SIZE,
    LEVEL_CROWDING_PIPS, WATCHLIST_MAX_DISTANCE_PIPS,
    WATCHLIST_SOFT_DISTANCE_PIPS, WATCHLIST_MIN_ADJUSTED_SCORE,
    WATCHLIST_MAX_ALERTS_BY_HORIZON, ENTRY_READY_MAX_ALERTS_PER_SCAN,
    ACTIVE_TIMEFRAME_PAIR_LABELS, DISABLED_TIMEFRAME_PAIRS,
)
from data.mt5_client import MT5Client
from strategies.strategy_manager import StrategyManager
from signals.signal_generator import SignalGenerator
from trade_manager.trade_tracker import TradeManager
from notifications.telegram_bot import TelegramBot
from db.database import Database
from learning.stats_learner import StatisticalLearner
from learning.rl_engine import LearningEngine
from utils.logger import get_logger

logger = get_logger("AlphaPulse")


class AlphaPulse:
    """
    Central orchestrator. Instantiates all subsystems and runs
    the main event loop.
    """

    def __init__(self):
        logger.info("=" * 60)
        logger.info("  AlphaPulse — XAUUSD Analysis Engine")
        logger.info("=" * 60)

        self._running = False
        self._scan_count = 0
        self._last_outlook_hash: Optional[str] = None

        # --- Subsystems ---
        self.db = Database()
        self.mt5 = MT5Client()
        self.telegram = TelegramBot()
        # StrategyManager is created without a learning engine here;
        # _learning is wired in start() after LearningEngine is ready.
        self.strategy_manager = StrategyManager(learning_engine=None)

        self.stats_learner: Optional[StatisticalLearner] = None
        self.learning: Optional[LearningEngine] = None
        self.signal_gen: Optional[SignalGenerator] = None
        self.trade_mgr: Optional[TradeManager] = None

        # Timeframes we need data for (merged default + LSD timeframes)
        self._required_tfs = self.strategy_manager.get_required_timeframes()
        if DISABLED_TIMEFRAME_PAIRS:
            disabled = ", ".join(f"{high}->{low}" for high, low in DISABLED_TIMEFRAME_PAIRS)
            logger.info("Disabled timeframe pair(s): %s", disabled)
        if not self._is_active_tf_pair("H4->H1"):
            logger.info("H4->H1 disabled for active intraday strategy.")

        # ── Startup silent-analysis phase ─────────────────────────────────────
        # No Telegram alerts during the first 5 minutes after start.
        # Allows the engine to build structural-level state before alerting.
        self._startup_time: Optional[datetime] = None
        self._analysis_complete: bool = False
        _ANALYSIS_PHASE_SECONDS = 300  # 5 minutes

        # ── Signal deduplication ──────────────────────────────────────────────
        # _seen_setups    : fingerprints processed this level-cycle
        #                   (cleared when structural levels change)
        # _confirmed_setups: fingerprints for which pending-order alert was sent
        #                   (NOT cleared on level change — prevents double-confirm)
        self._seen_setups: Set[str] = set()
        self._confirmed_setups: Set[str] = set()

        # ── Watch-level deduplication ─────────────────────────────────────────
        # Key: "{symbol}_{round(price, 2)}" — one alert per price regardless of TF.
        # NOT cleared on outlook change; expires when price moves >25 pips away.
        self._watch_alerted: Set[str] = set()
        # Session-wide setup/watchlist dedupe, separate from final signal fingerprints.
        self._watchlist_alerted: Set[str] = set()
        self._confirmed_levels: Set[str] = set()
        self._last_watch_distance: Dict[str, float] = {}

        # Last daily summary sent
        self._last_daily_date: Optional[str] = None

    # ─────────────────────────────────────────────────────
    # STARTUP
    # ─────────────────────────────────────────────────────

    def start(self):
        """Initialize all subsystems and start the main loop."""
        logger.info("Starting AlphaPulse...")

        # Database
        try:
            self.db.init()
            logger.info("Database initialized.")
        except Exception as e:
            logger.warning("Database init failed: %s — continuing without DB.", e)

        # MT5
        if not self.mt5.connect():
            logger.warning("MT5 not connected — will use synthetic data.")

        # Learning
        self.stats_learner = StatisticalLearner(self.db)
        self.learning = LearningEngine(self.db, self.stats_learner)

        # Wire learning engine into strategy manager now that it's ready
        self.strategy_manager._learning = self.learning

        # Signal generator (with learning)
        self.signal_gen = SignalGenerator(learning_engine=self.learning)

        # Trade manager — pass learning engine so it trains on every closed trade
        self.trade_mgr = TradeManager(self.db, self.telegram, learning=self.learning)
        # Cancel any trades left ACTIVE from a previous session.
        # This is a simulation bot — reloading old price levels causes immediate
        # false TP/SL hits because the market has moved since the last run.
        self.trade_mgr.cancel_stale_trades()

        # Register shutdown handlers
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

        # Notify Telegram (sends "started" + "analyzing charts..." messages)
        self.telegram.send_startup()

        # Mark startup time — alerts suppressed for first 5 minutes
        self._startup_time = datetime.now(timezone.utc)

        self._running = True
        self._run_loop()

    # ─────────────────────────────────────────────────────
    # MAIN SCAN LOOP
    # ─────────────────────────────────────────────────────

    def _run_loop(self):
        logger.info("Scan loop started. Interval: %ds", SCAN_INTERVAL_SECONDS)
        while self._running:
            try:
                self._scan_cycle()
            except Exception as e:
                logger.error("Unhandled error in scan cycle: %s", e, exc_info=True)
                self.telegram.send_system_alert(f"Scan error: {e}")

            # Sleep in small increments so shutdown is responsive
            for _ in range(SCAN_INTERVAL_SECONDS):
                if not self._running:
                    break
                time.sleep(1)

    def _scan_cycle(self):
        self._scan_count += 1
        now = datetime.now(timezone.utc)
        logger.info("── Scan #%d at %s ──", self._scan_count,
                    now.strftime("%Y-%m-%d %H:%M:%S UTC"))

        # ── Silent analysis phase: no Telegram alerts for first 5 minutes ─────
        elapsed = (
            (now - self._startup_time).total_seconds()
            if self._startup_time else 9999
        )
        in_silent_phase = elapsed < 300
        if not self._analysis_complete and not in_silent_phase:
            self._analysis_complete = True
            logger.info("Analysis phase complete — setup alerts now active.")

        # 1. Fetch OHLCV data for all timeframes
        data = self._fetch_all_data()
        if not data:
            logger.warning("No data fetched — skipping scan.")
            if not in_silent_phase:
                self.telegram.send_system_alert(
                    "⚠️ No market data received from MT5.\n"
                    "Check MT5 connection. Retrying next scan."
                )
            return

        # 2. Get current price
        current_price = self.mt5.get_current_price()
        if current_price is None:
            df_m15 = data.get("M15")
            if df_m15 is not None and len(df_m15) > 0:
                current_price = float(df_m15.iloc[-1]["close"])

        # 3. Run strategy manager — selects best strategy, returns unified signals
        run_result = self.strategy_manager.run(data, current_price=current_price)
        outlook  = run_result.outlook
        signals  = run_result.signals
        ctx      = outlook.context

        # 3a. Log strategy performance and filter states (internal only)
        self._log_strategy_performance(run_result)
        if ctx:
            if not ctx.is_volatile:
                logger.debug("Filter: low volatility — signals may be weaker")
            if ctx.is_news_window:
                logger.debug("Filter: news window active")

        # 3b. Track structural level changes (state management — no Telegram send)
        outlook_key = self._outlook_fingerprint(outlook)
        if outlook_key != self._last_outlook_hash:
            self._last_outlook_hash = outlook_key
            self._seen_setups.clear()
            self._confirmed_levels.clear()
            self._last_watch_distance.clear()
            # _watch_alerted intentionally NOT cleared (expires via 25-pip rule)
            logger.info("Structural levels refreshed (%d groups)", len(outlook.timeframe_levels))

        # 3c. Setup watchlist + watch-level approach alerts (silent-phase gated)
        if current_price and not in_silent_phase:
            self._send_shortlisted_level_alerts(outlook, current_price)
            self._check_watch_levels(outlook, current_price)

        # 4. Freshness filter removed — historical rejection candles ARE valid pending
        #    setups (e.g. a level rejection from yesterday is still actionable today).
        #    _seen_setups handles deduplication so each setup alerts only once per session.

        # 5. Process signals from the selected strategy
        if signals:
            active_signals = []
            for signal in signals:
                if self._is_active_tf_pair(signal.tf_pair_str):
                    active_signals.append(signal)
                else:
                    logger.info("Signal skipped: disabled timeframe pair %s", signal.tf_pair_str)
            new_signals = [s for s in active_signals if not self._is_seen_setup(s)]

            if new_signals:
                # ── Session gate (relaxed for manual bot) ────────────────────
                if not (ctx and getattr(ctx, "session_allowed", True)):
                    session_label = getattr(ctx, "session_name", "off_session") if ctx else "off_session"
                    logger.info(
                        "Off-session (%s): %d signal(s) found — manual discretion advised.",
                        session_label, len(new_signals),
                    )

                # ── Confidence split ──────────────────────────────────────────
                high_prob = [s for s in new_signals if s.confidence >= MIN_SIGNAL_CONFIDENCE]
                low_prob  = [s for s in new_signals if s.confidence <  MIN_SIGNAL_CONFIDENCE]

                # Low-confidence: mark seen immediately and log skip
                for s in low_prob:
                    self._seen_setups.add(s.fingerprint())
                    self._mark_level_resolved(self._watch_key(s.pair, s.level_price))
                    logger.info(
                        "Signal skipped (%.0f%% < %.0f%%): %s %s @ %.2f | %s",
                        s.confidence * 100, MIN_SIGNAL_CONFIDENCE * 100,
                        s.direction, s.signal_type, s.level_price,
                        self._build_skip_reason(s),
                    )

                if in_silent_phase:
                    # Log but do NOT mark as seen — lets them dispatch after the phase
                    for sig in high_prob:
                        logger.info(
                            "[Silent phase] Setup found (will alert after 5 min): "
                            "%s %s @ %.2f Conf=%.0f%%",
                            sig.direction, sig.pair,
                            sig.level_price, sig.confidence * 100,
                        )
                else:
                    # Process the best confirmed setups only; the sorter already
                    # prioritises recent/previous/QM structure by timeframe pair.
                    for sig in high_prob[:ENTRY_READY_MAX_ALERTS_PER_SCAN]:
                        fp       = sig.fingerprint()
                        level_id = self._watch_key(sig.pair, sig.level_price)

                        # Mark seen so this fingerprint is not re-processed next scan
                        self._seen_setups.add(fp)

                        # Skip if already confirmed this fingerprint this session
                        if fp in self._confirmed_setups:
                            continue

                        # Generate trade from DEFAULT setup
                        trade, rejection = self.signal_gen.generate(sig.setup)

                        if trade is None:
                            self._mark_level_resolved(level_id)
                            logger.info(
                                "Signal rejected: %s %s @ %.2f | %s",
                                sig.direction, sig.pair, sig.level_price, rejection,
                            )
                            continue

                        # ── Alert sequence ───────────────────────────────────
                        self._confirmed_setups.add(fp)
                        self._mark_level_confirmed(level_id)

                        # Use this signal's own strategy score for alert context
                        _sig_score_obj = run_result.strategy_scores.get(sig.strategy_name)
                        _strat_score   = _sig_score_obj.raw_score / 100.0 if _sig_score_obj else 0.5

                        logger.info(
                            "FIRST REJECTION CONFIRMED: %s %s | level=%.2f | %s rejection closed correctly | pending order ready",
                            trade.direction,
                            trade.pair,
                            trade.entry_price,
                            trade.lower_tf,
                        )

                        # Pending-order alert. Registration below stores the
                        # setup as PENDING until the later retest/fill occurs.
                        alert_sent = self.telegram.send_confirmation(trade, strategy_score=_strat_score)
                        if alert_sent:
                            logger.info(
                                "PENDING ORDER ALERT SENT: %s %s | entry=%.2f | SL=%.2f | TP1=%.2f",
                                trade.direction,
                                trade.pair,
                                trade.entry_price,
                                trade.sl_price,
                                trade.tp1,
                            )

                        # Register for simulated pending-order tracking.
                        self.trade_mgr.register_trade(trade)

                        logger.info(
                            "Pending order dispatched: %s %s @ %.2f | SL %.2f (%dp) | Conf %.0f%%",
                            trade.direction, trade.pair, trade.entry_price,
                            trade.sl_price,
                            int(abs(trade.entry_price - trade.sl_price)),
                            trade.confidence * 100,
                        )

        elif self._scan_count % 5 == 0:
            logger.debug(
                "Scan #%d — no new setups | session=%s | bias=%s",
                self._scan_count,
                ctx.session_name if ctx else "off",
                ctx.h4_bias if ctx else "neutral",
            )

        # 6. Update tracked setups against live price (simulated tracking)
        if current_price:
            self.trade_mgr.update(current_price)
            logger.debug("Price update @ %.2f", current_price)

        # 7. Periodically refresh learning (every 5 scans = every 5 minutes)
        if self._scan_count % 5 == 0:
            self._refresh_learning()

        # 8. Check if we should send daily summary
        self._check_daily_summary(now)

    # ─────────────────────────────────────────────────────
    # DATA FETCHING
    # ─────────────────────────────────────────────────────

    def _fetch_all_data(self) -> Dict[str, pd.DataFrame]:
        """Fetch OHLCV for all required timeframes."""
        data = {}
        for tf in self._required_tfs:
            try:
                df = self.mt5.get_ohlcv(tf)
                if df is not None and len(df) >= 10:
                    data[tf] = df
                    logger.debug("[%s] Fetched %d bars", tf, len(df))
                else:
                    logger.warning("[%s] Insufficient data", tf)
            except Exception as e:
                logger.error("[%s] Data fetch error: %s", tf, e)
        return data

    # ─────────────────────────────────────────────────────
    # LEARNING REFRESH
    # ─────────────────────────────────────────────────────

    def _refresh_learning(self):
        try:
            self.stats_learner.refresh()
            logger.info("Learning engine refreshed.")
        except Exception as e:
            logger.error("Learning refresh failed: %s", e)

    # ─────────────────────────────────────────────────────
    # DAILY SUMMARY
    # ─────────────────────────────────────────────────────

    def _check_daily_summary(self, now: datetime):
        """Send daily summary once per day around 21:00 UTC."""
        if now.hour == 21 and now.minute < (SCAN_INTERVAL_SECONDS // 60 + 1):
            today_str = now.strftime("%Y-%m-%d")
            if self._last_daily_date != today_str:
                self._send_daily_summary(now)
                self._last_daily_date = today_str

    def _send_daily_summary(self, now: datetime):
        try:
            stats = self.trade_mgr.get_daily_stats()
            best_setup = self.stats_learner.get_best_setup_str() if self.stats_learner else None

            # Persist daily summary
            try:
                self.db.upsert_daily_summary({
                    "date": now.strftime("%Y-%m-%d"),
                    "total_setups": stats["total_setups"],
                    "activated": stats["activated"],
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "win_rate": stats["win_rate"] / 100,
                })
            except Exception:
                pass

            wr = stats.get("win_rate", 0)
            self.telegram.send_system_alert(
                f"📅 Daily summary {now.strftime('%Y-%m-%d')}\n"
                f"Setups: {stats['total_setups']} | Activated: {stats['activated']}\n"
                f"W: {stats['wins']} / L: {stats['losses']} | WR: {wr:.0f}%\n"
                + (f"Best: _{best_setup}_" if best_setup else "")
            )
            logger.info("Daily summary sent.")
        except Exception as e:
            logger.error("Failed to send daily summary: %s", e)

    # ─────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────

    def _filter_fresh_signals(self, signals) -> list:
        """
        Drop signals whose confirmation candle is older than 4 hours.

        Historical OHLCV data (300–500 candles) is fetched to build structural state.
        We allow signals from up to 4 hours before the current time — this covers:
          • Setups that formed during the 5-minute silent analysis window
          • Recent H4/H1 confirmation candles that are still actionable
          • LSD displacements that happened in the last trading session

        Anything older than 4 hours is discarded as a stale historical replay.
        """
        if not self._startup_time or not signals:
            return signals

        fresh = []
        stale_count = 0

        # 4-hour rolling window — keeps recent setups regardless of when bot started
        now = datetime.now(timezone.utc)
        lookback_cutoff = now - timedelta(hours=4)

        for sig in signals:
            ct = sig.confirmed_at
            if ct is None:
                fresh.append(sig)
                continue

            # Normalise to timezone-aware UTC for comparison
            try:
                ts = pd.Timestamp(ct)
                if ts.tzinfo is None:
                    ts = ts.tz_localize("UTC")
                else:
                    ts = ts.tz_convert("UTC")
                signal_dt = ts.to_pydatetime()
            except Exception:
                fresh.append(sig)   # can't parse time — allow through
                continue

            if signal_dt >= lookback_cutoff:
                fresh.append(sig)
            else:
                stale_count += 1
                logger.debug(
                    "Stale signal discarded: %s %s @ %.2f | candle %s (cutoff %s)",
                    sig.direction, sig.pair, sig.level_price,
                    signal_dt.strftime("%H:%M"), lookback_cutoff.strftime("%H:%M"),
                )

        if stale_count:
            logger.info(
                "Freshness filter: dropped %d stale signal(s) — candles older than 4 hours.",
                stale_count,
            )

        return fresh

    @staticmethod
    def _log_strategy_performance(run_result) -> None:
        """
        Log current strategy learning scores every scan so the log clearly
        shows how each strategy is performing over time.

        Format:
          Strategy Performance:
            - DEFAULT: 0.54 (20 trades)
            - LSD:     0.68 (12 trades)
        """
        lines = []
        for name, score in run_result.strategy_scores.items():
            label = name.upper().ljust(7)
            perf  = score.raw_score / 100.0
            lines.append(f"  - {label}: {perf:.2f} ({score.trades_seen} trades)")
        logger.info("Strategy Performance:\n%s", "\n".join(lines))

    def _check_watch_levels(self, outlook, current_price: float):
        """
        Send WATCH_LEVEL alert when price actively approaches a key structural level.

        Rules enforced here (not in TelegramBot):
          1. Key = symbol_price  — same price across all timeframes = ONE alert
          2. Re-alert allowed only when price has moved >25 pips away and returned
          3. Approach quality filter — only alert when distance is DECREASING
          4. At most one alert above price and one below per scan
          5. No alert when price is already within LEVEL_TOLERANCE_PIPS (at the level)
        """
        ctx = getattr(outlook, "context", None)
        if ctx is not None and not getattr(ctx, "session_allowed", True):
            logger.info(
                "WATCH LEVELS SKIPPED: session filter (%s | %s)",
                getattr(ctx, "session_name", "off_session"),
                getattr(ctx, "session_block_reason", "blocked"),
            )
            return

        watch_dist    = WATCH_DISTANCE_PIPS * PIP_SIZE
        touch_dist    = LEVEL_TOLERANCE_PIPS * PIP_SIZE
        re_alert_dist = 25.0 * PIP_SIZE

        # Step 1: expire watch alerts where price has moved far away (>25 pips)
        # This allows a fresh alert on the next genuine approach to the same level.
        for key in list(self._watch_alerted):
            try:
                level_price = float(key.split("_")[-1])
                if abs(current_price - level_price) > re_alert_dist:
                    self._watch_alerted.discard(key)
                    self._last_watch_distance.pop(key, None)
                    logger.debug("WATCH: %s expired — price moved away", key)
            except (ValueError, IndexError):
                pass

        # Step 2: collect candidate levels from all timeframe groups
        best_above: Optional[dict] = None
        best_below: Optional[dict] = None

        for tfl in outlook.timeframe_levels:
            tf_pair = f"{tfl.higher_tf}→{tfl.lower_tf}"
            if not self._is_active_tf_pair(tf_pair):
                logger.info("WATCH LEVEL SKIPPED: disabled timeframe pair %s", tf_pair)
                continue
            candidate_levels = tfl.levels + tfl.recent_levels + tfl.previous_levels

            for level in candidate_levels:
                dist = abs(current_price - level.price)

                # Outside the watch band
                if dist <= touch_dist or dist > watch_dist:
                    continue

                # Direction sanity: resistance must be above, support below
                if level.level_type == "A" and current_price > level.price:
                    continue
                if level.level_type == "V" and current_price < level.price:
                    continue

                # Dedup key: price only (timeframe-agnostic)
                alert_key = self._watch_key(outlook.pair, level.price)

                if alert_key in self._watch_alerted or alert_key in self._confirmed_levels:
                    continue

                # Approach quality filter: only fire when price is getting closer
                prev_dist = self._last_watch_distance.get(alert_key)
                self._last_watch_distance[alert_key] = dist  # always update for next scan
                if prev_dist is not None and dist >= prev_dist:
                    # Distance not decreasing — price stalling or moving away
                    continue

                candidate = {
                    "level":     level,
                    "alert_key": alert_key,
                    "dist":      dist,
                    "tf_pair":   tf_pair,
                }
                if level.price >= current_price:
                    if best_above is None or dist < best_above["dist"]:
                        best_above = candidate
                else:
                    if best_below is None or dist < best_below["dist"]:
                        best_below = candidate

        # Step 3: fire at most one alert above and one below per scan
        for candidate in (best_above, best_below):
            if candidate is None:
                continue

            level     = candidate["level"]
            alert_key = candidate["alert_key"]
            dist_pips = candidate["dist"] / PIP_SIZE

            logger.info(
                "WATCH LEVEL: XAUUSD %s %.2f | %.1f pips | [%s]",
                level.level_type, level.price, dist_pips, candidate["tf_pair"],
            )
            sent = self.telegram.send_watch_level(
                level_price=level.price,
                level_type=level.level_type,
                distance_pips=dist_pips,
                timeframe_pair=candidate["tf_pair"],
                current_price=current_price,
                scope=getattr(level, "scope", ""),
                is_qm=getattr(level, "is_qm", False),
            )
            if sent:
                self._watch_alerted.add(alert_key)

    def _send_shortlisted_level_alerts(self, outlook, current_price: float):
        """
        Send setup/watchlist alerts for accepted structural levels before they
        become full strategy signals.

        These are intentionally independent from signal generation: a level only
        needs to survive the elite level-selection pipeline to be announced here.
        The later confirmation and simulated trade-tracking flow remains driven
        by the existing signal pipeline.
        """
        ctx = getattr(outlook, "context", None)
        if ctx is not None and not getattr(ctx, "session_allowed", True):
            logger.info(
                "WATCHLIST SKIPPED: session filter (%s | %s)",
                getattr(ctx, "session_name", "off_session"),
                getattr(ctx, "session_block_reason", "blocked"),
            )
            return

        candidates = []
        for tfl in outlook.timeframe_levels:
            tf_pair = f"{tfl.higher_tf}->{tfl.lower_tf}"
            if not self._is_active_tf_pair(tf_pair):
                logger.info("WATCHLIST SKIPPED: disabled timeframe pair %s", tf_pair)
                continue
            horizon = self._watchlist_horizon(tf_pair)
            for level in tfl.levels + tfl.recent_levels + tfl.previous_levels:
                direction = self._level_trade_direction(level, current_price)
                alert_key = self._watchlist_key(outlook.pair, level, direction)
                level_id = self._watch_key(outlook.pair, level.price)

                if alert_key in self._watchlist_alerted:
                    continue
                if level_id in self._confirmed_levels:
                    continue

                watch_score, watch_notes, reject_reason = self._watchlist_score(
                    level=level,
                    tf_pair=tf_pair,
                    current_price=current_price,
                )
                base_score = self._level_selection_score(level)
                distance_pips = abs(current_price - level.price) / PIP_SIZE

                if reject_reason:
                    logger.info(
                        "WATCHLIST SKIPPED: %s %s %.2f | %s %s | base=%.0f adj=%.0f dist=%.1fp | %s | selected because: %s",
                        outlook.pair,
                        direction,
                        level.price,
                        horizon,
                        tf_pair,
                        base_score,
                        watch_score,
                        distance_pips,
                        reject_reason,
                        "; ".join(getattr(level, "accepted_reasons", [])[:5]) or "accepted by selector",
                    )
                    continue

                candidates.append({
                    "level": level,
                    "direction": direction,
                    "alert_key": alert_key,
                    "tf_pair": tf_pair,
                    "horizon": horizon,
                    "score": watch_score,
                    "base_score": base_score,
                    "distance_pips": distance_pips,
                    "watch_notes": watch_notes,
                    "origin_index": getattr(level, "origin_index", -1),
                })

        if not candidates:
            return

        # If the same price appears in multiple timeframe groups, keep only the
        # strongest representation so the trader gets one clean watchlist alert.
        best_by_key = {}
        for candidate in candidates:
            key = candidate["alert_key"]
            existing = best_by_key.get(key)
            if existing is None or self._prefer_watchlist_candidate(candidate, existing):
                best_by_key[key] = candidate

        ranked = []
        for candidate in sorted(
            best_by_key.values(),
            key=lambda item: (
                item["score"],
                -item["distance_pips"],
                item["origin_index"],
            ),
            reverse=True,
        ):
            if self._is_crowded_watchlist_candidate(candidate, ranked):
                logger.info(
                    "WATCHLIST SKIPPED: %s %s %.2f | %s %s | adj=%.0f dist=%.1fp | newer/closer similar level already queued",
                    outlook.pair,
                    candidate["direction"],
                    candidate["level"].price,
                    candidate["horizon"],
                    candidate["tf_pair"],
                    candidate["score"],
                    candidate["distance_pips"],
                )
                continue
            ranked.append(candidate)

        sent_count = 0
        horizon_counts: Dict[str, int] = {}
        bias = self._watchlist_bias(outlook)

        for candidate in ranked:
            horizon = candidate["horizon"]
            max_for_horizon = WATCHLIST_MAX_ALERTS_BY_HORIZON.get(horizon, 2)
            if horizon_counts.get(horizon, 0) >= max_for_horizon:
                continue

            level = candidate["level"]
            sent = self.telegram.send_watchlist_setup(
                symbol=outlook.pair,
                level_price=level.price,
                level_type=level.level_type,
                direction=candidate["direction"],
                distance_pips=candidate["distance_pips"],
                timeframe_pair=candidate["tf_pair"],
                current_price=current_price,
                quality_score=candidate["score"],
                base_quality_score=candidate["base_score"],
                scope=getattr(level, "scope", ""),
                bias=bias,
                horizon=horizon,
                confluences=self._watchlist_confluences(level, candidate["watch_notes"]),
                status=self._watchlist_status(horizon, candidate["distance_pips"]),
                is_qm=getattr(level, "is_qm", False),
                is_psychological=getattr(level, "is_psychological", False),
                psych_strength=getattr(level, "psych_strength", ""),
            )
            if sent:
                self._watchlist_alerted.add(candidate["alert_key"])
                horizon_counts[horizon] = horizon_counts.get(horizon, 0) + 1
                sent_count += 1
                logger.info(
                    "SETUP WATCHLIST: %s %s %.2f | %s %s | base=%.0f adj=%.0f dist=%.1fp | %s",
                    outlook.pair,
                    candidate["direction"],
                    level.price,
                    horizon,
                    candidate["tf_pair"],
                    candidate["base_score"],
                    candidate["score"],
                    candidate["distance_pips"],
                    "; ".join(getattr(level, "accepted_reasons", []) + candidate["watch_notes"]),
                )

        if sent_count:
            logger.info(
                "Setup watchlist alerts sent: %d shortlisted level(s).",
                sent_count,
            )

    @staticmethod
    def _outlook_fingerprint(outlook) -> str:
        """
        Create a fingerprint from structural levels only.
        Used to detect when levels have changed between scans.

        Psych levels are excluded because they are generated around current price
        and can drift every scan, which would incorrectly reset watch state.
        """
        parts = []
        for tfl in outlook.timeframe_levels:
            structural_levels = tfl.levels + tfl.recent_levels + tfl.previous_levels
            levels = sorted(
                structural_levels,
                key=lambda l: (l.level_type, round(l.price, 2), getattr(l, "scope", "")),
            )
            for level in levels:
                parts.append(
                    f"{tfl.higher_tf}-{tfl.lower_tf}:{level.level_type}:{level.price:.2f}:{getattr(level, 'scope', '')}"
                )
        return "|".join(parts)

    def _is_seen_setup(self, signal) -> bool:
        """Return True if this signal has already been processed this level-cycle."""
        return signal.fingerprint() in self._seen_setups

    @staticmethod
    def _build_skip_reason(signal) -> str:
        """Build a human-readable explanation for why a signal was not taken."""
        reasons = []
        if signal.session_name == "off_session" and not signal.is_swing:
            reasons.append("Outside London/New York session")
        if not signal.trend_aligned:
            reasons.append(
                f"Counter-trend ({signal.direction} vs H4 {signal.h4_bias})"
            )
        if signal.confidence < MIN_SIGNAL_CONFIDENCE:
            reasons.append(
                f"Confidence {signal.confidence*100:.0f}% below {MIN_SIGNAL_CONFIDENCE*100:.0f}% threshold"
            )
        if not reasons:
            reasons.append("Below minimum confidence threshold")
        return " | ".join(reasons)

    @staticmethod
    def _is_active_tf_pair(tf_pair: str) -> bool:
        return (
            str(tf_pair)
            .replace("->", "-")
            .replace("→", "-")
            .replace("â†’", "-")
            .replace(" ", "")
            in ACTIVE_TIMEFRAME_PAIR_LABELS
        )

    @staticmethod
    def _watchlist_horizon(tf_pair: str) -> str:
        """Classify a timeframe pair for Telegram watchlist intent."""
        if tf_pair == "H4->H1":
            return "swing"
        if tf_pair == "M30->M15":
            return "fast_intraday"
        return "intraday"

    @staticmethod
    def _watchlist_bias(outlook) -> str:
        ctx = getattr(outlook, "context", None)
        if not ctx:
            return "neutral"
        bias = getattr(ctx, "dominant_bias", "") or getattr(ctx, "h4_bias", "") or "neutral"
        bias_label = AlphaPulse._bias_storyline_label(bias)
        strength = getattr(ctx, "bias_strength", "weak")
        h1_state = getattr(ctx, "h1_state", "range")
        session = getattr(ctx, "session_name", "") or "off-session"
        return f"{bias_label} {strength} / H1 {h1_state} / {session}"

    @staticmethod
    def _bias_storyline_label(bias: str) -> str:
        labels = {
            "bullish": "Bullish Storyline",
            "bearish": "Bearish Storyline",
            "mixed": "Mixed Storyline",
            "neutral": "Neutral Storyline",
        }
        return labels.get((bias or "neutral").lower(), "Neutral Storyline")

    def _watchlist_score(self, level, tf_pair: str, current_price: float):
        """
        Rank accepted levels for Telegram usefulness without changing the
        underlying confirmation watchlist.
        """
        base_score = self._level_selection_score(level)
        score = base_score
        notes = []
        distance_pips = abs(current_price - level.price) / PIP_SIZE
        horizon = self._watchlist_horizon(tf_pair)

        max_distance = WATCHLIST_MAX_DISTANCE_PIPS.get(tf_pair, 180)
        soft_distance = WATCHLIST_SOFT_DISTANCE_PIPS.get(tf_pair, 75)
        min_score = WATCHLIST_MIN_ADJUSTED_SCORE.get(tf_pair, 55)

        if distance_pips > max_distance:
            return (
                score,
                notes,
                f"too far for {horizon} watchlist ({distance_pips:.1f}p > {max_distance}p)",
            )

        if distance_pips <= WATCH_DISTANCE_PIPS:
            score += 12.0
            notes.append("near active zone")
        elif distance_pips <= soft_distance:
            score += 7.0
            notes.append("actionable distance")
        else:
            distance_rates = {
                "H4->H1": 0.04,
                "H1->M30": 0.18,
                "M30->M15": 0.35,
            }
            penalty = (distance_pips - soft_distance) * distance_rates.get(tf_pair, 0.18)
            score -= penalty
            notes.append(f"distance penalty -{penalty:.0f}")

        scope = getattr(level, "scope", "")
        scope_bonus = {
            "M30->M15": {"recent": 18.0, "major": -14.0, "previous": -24.0},
            "H1->M30": {"recent": 12.0, "major": -6.0, "previous": -16.0},
            "H4->H1": {"major": 6.0, "recent": 4.0, "previous": -6.0},
        }.get(tf_pair, {})
        score += scope_bonus.get(scope, 0.0)

        if scope == "recent":
            notes.append("recent structure")
        elif scope == "previous" and horizon == "intraday":
            notes.append("older fallback structure")

        if getattr(level, "touch_count", 99) <= 2:
            score += 4.0
            notes.append("fresh low-touch level")

        if getattr(level, "is_qm", False):
            score += 3.0
            notes.append("QM confluence")

        if getattr(level, "is_psychological", False):
            score += 2.0
            notes.append("psychological confluence")

        if score < min_score:
            return (
                score,
                notes,
                f"adjusted watchlist score too low ({score:.0f} < {min_score})",
            )

        return min(100.0, max(0.0, score)), self._dedupe_strings(notes), ""

    @staticmethod
    def _prefer_watchlist_candidate(candidate: dict, existing: dict) -> bool:
        """Prefer stronger, then closer, then newer levels for duplicate prices."""
        return (
            candidate["score"],
            -candidate["distance_pips"],
            candidate["origin_index"],
        ) > (
            existing["score"],
            -existing["distance_pips"],
            existing["origin_index"],
        )

    @staticmethod
    def _is_crowded_watchlist_candidate(candidate: dict, queued: list) -> bool:
        """Suppress similar nearby watchlist messages in favour of the best one."""
        tol = LEVEL_CROWDING_PIPS * PIP_SIZE
        level = candidate["level"]
        for kept in queued:
            kept_level = kept["level"]
            if kept["horizon"] != candidate["horizon"]:
                continue
            if kept["direction"] != candidate["direction"]:
                continue
            if abs(kept_level.price - level.price) <= tol:
                return True
        return False

    def _watchlist_confluences(self, level, watch_notes: list) -> list:
        """Compress detailed selector/debug reasons into 2-3 Telegram bullets."""
        reasons = list(getattr(level, "accepted_reasons", [])) + list(watch_notes or [])
        lower_reasons = " | ".join(reasons).lower()
        confluences = []

        if getattr(level, "scope", "") == "recent":
            confluences.append("recent structure")
        if "active zone" in lower_reasons or "actionable distance" in lower_reasons:
            confluences.append("near price")
        if "trend aligned" in lower_reasons:
            confluences.append("trend aligned")
        if "fresh" in lower_reasons or getattr(level, "touch_count", 99) <= 2:
            confluences.append("fresh / low touch")
        if "room" in lower_reasons:
            confluences.append("clear TP room")
        if getattr(level, "level_type", "") == "Gap" or "imbalance" in lower_reasons:
            confluences.append("imbalance")
        if getattr(level, "is_qm", False):
            confluences.append("QM structure")
        if getattr(level, "is_psychological", False):
            confluences.append("psych level")
        if "liquidity sweep" in lower_reasons:
            confluences.append("liquidity sweep")

        return self._dedupe_strings(confluences)[:3] or ["elite selector pass"]

    @staticmethod
    def _watchlist_status(horizon: str, distance_pips: float) -> str:
        if distance_pips <= WATCH_DISTANCE_PIPS:
            return "near zone - wait for rejection confirmation"
        if horizon == "swing":
            return "swing candidate - monitor approach, no entry yet"
        if horizon == "fast_intraday":
            return "fast intraday candidate - waiting for price to approach"
        return "intraday candidate - waiting for price to approach"

    @staticmethod
    def _dedupe_strings(items: list) -> list:
        seen = set()
        out = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    @staticmethod
    def _level_trade_direction(level, current_price: float) -> str:
        """Infer the manual trade idea direction for a selected structural level."""
        explicit = getattr(level, "trade_direction", "")
        if explicit in ("BUY", "SELL"):
            return explicit
        if level.level_type == "A":
            return "SELL"
        if level.level_type == "V":
            return "BUY"
        return "SELL" if level.price >= current_price else "BUY"

    @staticmethod
    def _level_selection_score(level) -> float:
        """Return the selector score used for watchlist priority."""
        score = getattr(level, "selection_score", 0.0) or getattr(level, "quality_score", 0.0)
        return float(score or 0.0)

    @staticmethod
    def _watch_key(symbol: str, level_price: float) -> str:
        """
        Deduplication key for watch and confirmation state.
        Intentionally excludes level_type and timeframe so the same price
        across H1/M30/M15 always maps to one key — one alert maximum.
        """
        return f"{symbol}_{round(level_price, 2)}"

    @staticmethod
    def _watchlist_key(symbol: str, level, direction: str) -> str:
        """
        Deduplication key for pre-confirmation setup/watchlist alerts.
        This is separate from signal fingerprints because selected levels are
        not full trade signals yet.
        """
        return f"{symbol}_{direction}_{round(level.price, 2)}"

    def _mark_level_confirmed(self, level_id: str):
        """Level has produced a confirmed trade — suppress further watch alerts."""
        self._watch_alerted.discard(level_id)
        self._confirmed_levels.add(level_id)

    def _mark_level_resolved(self, level_id: str):
        """Level was processed (skipped or rejected) — suppress further watch alerts."""
        self._watch_alerted.discard(level_id)
        self._confirmed_levels.add(level_id)

    # ─────────────────────────────────────────────────────
    # SHUTDOWN
    # ─────────────────────────────────────────────────────

    def _shutdown_handler(self, sig, frame):
        logger.info("Shutdown signal received...")
        self.stop()

    def stop(self):
        self._running = False
        logger.info("Shutting down AlphaPulse...")
        self.telegram.send_shutdown()
        self.mt5.disconnect()
        self.db.close()
        logger.info("AlphaPulse stopped cleanly.")
        sys.exit(0)


# ─────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot = AlphaPulse()
    bot.start()
