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

import json
import os
import signal
import sys
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Set
from hashlib import sha1

import pandas as pd

from analysis.gold_confirmation_engine import AnalystTradeSetup, GoldConfirmationEngine, confirmation_is_fresh, quality_label_from_score
from analysis.level_intelligence import LevelIntelligenceEngine, validate_scenario_compliance
from analysis.market_analyst import MarketAnalyst
from learning.scoring_engine import ScoringEngine
from execution.decision_engine import DecisionEngine
from risk.trade_management import TradeManagementEngine
from risk.outcome_tracker import OutcomeTracker
from config.settings import (
    SCAN_INTERVAL_SECONDS, TIMEFRAME_PAIRS, MIN_SIGNAL_CONFIDENCE,
    WATCH_DISTANCE_PIPS, LEVEL_TOLERANCE_PIPS, PIP_SIZE,
    LEVEL_CROWDING_PIPS, WATCHLIST_MAX_DISTANCE_PIPS,
    WATCHLIST_SOFT_DISTANCE_PIPS, WATCHLIST_MIN_ADJUSTED_SCORE,
    WATCHLIST_MAX_ALERTS_BY_HORIZON, ENTRY_READY_MAX_ALERTS_PER_SCAN,
    ACTIVE_TIMEFRAME_PAIR_LABELS, DISABLED_TIMEFRAME_PAIRS,
    SEND_NO_SETUP_STATUS_ALERT, NO_SETUP_STATUS_INTERVAL_MINUTES,
    BOT_ACTIVE_START_HOUR, BOT_ACTIVE_END_HOUR,
    ENGULF_ALLOWED_LIVE_TIMEFRAMES, LIVE_ENABLED_STRATEGIES,
    RESEARCH_ONLY_STRATEGIES,
    MANUAL_SETUP_APPROACH_DISTANCE_PIPS, MANUAL_SETUP_ALERT_COOLDOWN_MINUTES,
    MARKET_PLAN_ALERT_COOLDOWN_MINUTES, SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES,
    LIVE_ALERT_ROUTING,
    SPENCER_MEMORY_RETENTION_HOURS, SPENCER_RESUME_WATCH_ALERT_ENABLED,
    MARKET_PLAN_MIN_INTERVAL_MINUTES, MARKET_PLAN_RESEND_ON_MINOR_PRICE_CHANGE,
    ENTRY_ALERT_COOLDOWN_MINUTES, ENTRY_ZONE_DUPLICATE_TOLERANCE_PIPS,
    PRIMARY_SETUP_ALERT_COOLDOWN_MINUTES,
    MT5_NO_DATA_ALERT_COOLDOWN_MINUTES,
    TELEGRAM_RUNTIME_ALERTS_ENABLED,
    LIVE_ARCHITECTURE_MODE,
    PYTORCH_AI_BLOCKING_MODE,
    PYTORCH_AI_ENABLED,
    PYTORCH_AI_MODEL_PATH,
    PYTORCH_AI_MODEL_TYPE,
    PYTORCH_AI_MODEL_VERSION,
    PYTORCH_AI_SCHEMA_PATH,
    SCENARIO_COMPLIANCE_ENABLED,
)
from data.mt5_client import MT5Client
from strategies.strategy_manager import StrategyManager
from strategies.filters import MarketContextEngine
from signals.signal_generator import SignalGenerator
from trade_manager.trade_tracker import TradeManager
from notifications.telegram_bot import TelegramBot
from db.database import Database
from learning.stats_learner import StatisticalLearner
from learning.rl_engine import LearningEngine
from utils.logger import get_logger, get_runtime_logger
from utils.alert_dedupe import AlertDedupeManager

logger = get_logger("AlphaPulse")
runtime_logger = get_runtime_logger()


class AlphaPulse:
    """
    Central orchestrator. Instantiates all subsystems and runs
    the main event loop.
    """

    def __init__(self):
        self._instance_id = str(os.getpid())
        os.environ["ALPHAPULSE_INSTANCE_ID"] = self._instance_id
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
        self.context_engine = MarketContextEngine()
        # StrategyManager is created without a learning engine here;
        # _learning is wired in start() after LearningEngine is ready.
        self.strategy_manager = StrategyManager(learning_engine=None, merge_confluence=False)
        self.market_analyst = MarketAnalyst()
        self.level_intelligence_engine = LevelIntelligenceEngine()
        self.gold_confirmation_engine = GoldConfirmationEngine()
        self.scoring_engine = ScoringEngine()
        self.decision_engine = DecisionEngine()
        self.trade_management_engine = TradeManagementEngine()
        self.outcome_tracker = OutcomeTracker()
        self._analyst_run_id: Optional[int] = None
        self._analyst_scenario_rows: Dict[str, int] = {}
        self._analyst_confirmation_rows: Dict[str, int] = {}
        self._analyst_reviewed_setups: Set[str] = set()

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
        self._last_market_plan_hash: Optional[str] = None
        self._last_market_plan_sent_at: Optional[datetime] = None
        self._last_scenario_update_at: Optional[datetime] = None
        self._last_session_market_plan: Optional[str] = None
        self._last_primary_scenario_key: Optional[str] = None
        self._last_primary_scenario_snapshot: Optional[dict] = None
        self._last_secondary_scenario_snapshot: Optional[dict] = None
        self._last_dominant_bias: Optional[str] = None
        self._last_bias_strength: Optional[str] = None
        self._last_scenario_update_by_type: Dict[str, datetime] = {}
        self._market_plan: Optional[dict] = None
        self._watch_zone_state: Dict[str, dict] = {}
        self._resume_watch_checked: bool = False
        self._alert_dedupe = AlertDedupeManager(
            ttl_hours={
                "market_plan": 24,
                "scenario_update": 48,
                "analyst_entry": 48,
                "gap_watchlist": 24,
                "watch_zone_update": 48,
            }
        )

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

        # Manual setup approach alert cooldown: setup_id → last alert time
        self._manual_setup_approach_alerted: Dict[int, datetime] = {}

        # Heartbeat file for API status sync
        self._heartbeat_file = Path(__file__).resolve().parent / "bot_heartbeat.json"
        self._memory_file = Path(__file__).resolve().parent / "spencer_memory.json"
        self._runtime_control_file = Path(__file__).resolve().parent / "bot_runtime_control.json"
        self._runtime_alerts_disabled_flag = Path(__file__).resolve().parent / "runtime_alerts_disabled.flag"
        # Tracks when the last no-setup status alert was sent
        self._last_no_setup_alert_time: Optional[datetime] = None
        self._background_tasks: Dict[str, bool] = {
            "scan_loop": False,
            "heartbeat_writer": False,
            "runtime_alerts": False,
            "market_analyst_loop": False,
            "watchlist_loop": False,
        }
        # Per-scan pipeline summary — written to heartbeat and SCAN COMPLETE log
        self._last_scan_summary: dict = {
            "last_candidates_count": 0,
            "last_alerts_sent":      0,
            "last_alerts_failed":    0,
            "last_reject_reason":    "",
            "last_telegram_status":  "none",
            "last_telegram_error":   "",
            "last_telegram_alert_type": "",
            "last_telegram_alert_time": "",
            "last_scan_number":      0,
            "session_blocking":      False,
            "levels_detected":       0,
            "gap_levels":            0,
            "bias_passed":           0,
            "sweep_confirmed":       0,
            "session_passed":        0,
            "distance_passed":       0,
            "watchlist_candidates":  0,
            "dedupe_rejections":     0,
            "instance_totals": {
                "total_scans": 0,
                "total_candidates_found": 0,
                "watchlist_candidates": 0,
                "alerts_sent": 0,
                "alerts_failed": 0,
                "duplicates_blocked": 0,
                "manual_alerts_sent": 0,
                "confirmation_alerts_sent": 0,
            },
            "strategy_scans": {
                "gap_liquidity_sweep_reclaim": {
                    "enabled": "gap_liquidity_sweep_reclaim" in LIVE_ENABLED_STRATEGIES,
                    "scans_run": 0,
                    "candidates_found": 0,
                    "watchlist_alerts_sent": 0,
                    "entry_alerts_sent": 0,
                    "alerts_failed": 0,
                    "duplicates_blocked": 0,
                    "last_result": "",
                    "last_reject_reason": "",
                    "last_scan_time": "",
                },
                "engulfing_rejection": {
                    "enabled": "engulfing_rejection" in LIVE_ENABLED_STRATEGIES,
                    "scans_run": 0,
                    "candidates_found": 0,
                    "alerts_sent": 0,
                    "alerts_failed": 0,
                    "duplicates_blocked": 0,
                    "last_result": "",
                    "last_reject_reason": "",
                    "last_scan_time": "",
                },
                "standard_break_retest": {
                    "enabled": "standard_break_retest" in LIVE_ENABLED_STRATEGIES,
                    "scans_run": 0,
                    "candidates_found": 0,
                    "alerts_sent": 0,
                    "alerts_failed": 0,
                    "duplicates_blocked": 0,
                    "last_result": "",
                    "last_reject_reason": "",
                    "last_scan_time": "",
                },
                "failed_engulf_break_retest": {
                    "enabled": False,
                    "mode": "research_only",
                },
            },
            "market_plan": None,
            "alert_dedupe": {
                "market_plan_skipped_duplicate": 0,
                "scenario_update_skipped_duplicate": 0,
                "entry_skipped_duplicate": 0,
                "watchlist_skipped_duplicate": 0,
                "last_skip_reason": "",
            },
            "five_layer_status": {
                "market_analyst": {},
                "confirmation_engine": {},
                "learning_score": {},
                "pytorch_ai": {},
                "decision_engine": {},
                "risk_management": {},
            },
        }
        self._load_memory()

    def _bot_window_active(self, _ctx) -> bool:
        # 24/7 mode — always active
        return True

    def _log_time_block_check(self, ctx) -> bool:
        # 24/7 mode — never blocks; logs session context only
        local_time = getattr(ctx, "local_time", "--:--") if ctx else "--:--"
        session_label = getattr(ctx, "session_name", "unknown") if ctx else "unknown"
        runtime_logger.info(
            "SESSION CONTEXT: label=%s | scan_allowed=true | mode=24_7 | local_time=%s",
            session_label, local_time,
        )
        return False

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
        self.scoring_engine = ScoringEngine(self.learning)

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

        try:
            self._analyst_run_id = self.db.create_analyst_replay_run(
                {
                    "symbol": "XAUUSD",
                    "source": "live",
                    "status": "running",
                    "notes": "Live Spencer analyst session",
                }
            )
        except Exception as exc:
            logger.debug("Analyst live run registration skipped: %s", exc)

        # Register shutdown handlers
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)
        # Windows: CTRL_BREAK_EVENT sent by API maps to SIGBREAK, not SIGTERM
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, self._shutdown_handler)

        # Notify Telegram (sends "started" + "analyzing charts..." messages)
        self.telegram.send_startup()

        runtime_logger.info("BOT INSTANCE STARTED: instance_id=%s", self._instance_id)
        runtime_logger.info("BOT STARTED")
        runtime_logger.info("ANALYSIS PHASE STARTED: 5-minute silent phase — no alerts until complete")
        self._background_tasks["scan_loop"] = True
        self._background_tasks["heartbeat_writer"] = True
        self._background_tasks["runtime_alerts"] = True
        self._background_tasks["market_analyst_loop"] = True
        self._background_tasks["watchlist_loop"] = True

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
                self._send_runtime_system_alert(
                    f"Scan error: {e}",
                    alert_type="scan_error",
                    source_module="main",
                    source_function="_run_loop",
                    cooldown_minutes=15,
                )

            # Sleep in small increments so shutdown is responsive
            for _ in range(SCAN_INTERVAL_SECONDS):
                if not self._running:
                    break
                time.sleep(1)

    def _scan_cycle(self):
        self._scan_count += 1
        self._last_scan_summary["instance_totals"]["total_scans"] = self._scan_count
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
            runtime_logger.info("ANALYSIS PHASE COMPLETE: setup watchlist alerts now active")

        runtime_logger.info(
            "SCAN STARTED: symbol=XAUUSD | scan=%d | phase=%s",
            self._scan_count, "analyzing" if in_silent_phase else "watching",
        )

        # 1. Fetch OHLCV data for all timeframes
        data = self._fetch_all_data()
        if not data:
            logger.warning("No data fetched — skipping scan.")
            runtime_logger.info("NO WATCHLIST SETUPS FOUND: symbol=XAUUSD | reason=mt5_no_data")
            self._last_scan_summary.update({
                "last_reject_reason": "mt5_no_data",
                "last_scan_number": self._scan_count,
                "session_blocking": False,
                "last_candidates_count": 0,
                "last_alerts_sent": 0,
                "last_alerts_failed": 0,
                "levels_detected": 0,
                "gap_levels": 0,
                "bias_passed": 0,
                "sweep_confirmed": 0,
                "session_passed": 0,
                "distance_passed": 0,
                "watchlist_candidates": 0,
                "dedupe_rejections": 0,
            })
            if not in_silent_phase:
                self._send_runtime_system_alert(
                    "⚠️ No market data received from MT5.\nCheck MT5 connection. Retrying next scan.",
                    alert_type="mt5_no_data",
                    source_module="main",
                    source_function="_scan_cycle",
                    cooldown_minutes=MT5_NO_DATA_ALERT_COOLDOWN_MINUTES,
                )
            return

        # 2. Get current price
        tick = self.mt5.get_tick()
        current_price = tick["mid"] if tick else self.mt5.get_current_price()
        candle_high = None
        candle_low = None
        df_m15_for_range = data.get("M15")
        if df_m15_for_range is not None and len(df_m15_for_range) > 0:
            try:
                candle_high = float(df_m15_for_range.iloc[-1]["high"])
                candle_low = float(df_m15_for_range.iloc[-1]["low"])
            except Exception:
                candle_high = None
                candle_low = None
        if current_price is None:
            df_m15 = data.get("M15")
            if df_m15 is not None and len(df_m15) > 0:
                current_price = float(df_m15.iloc[-1]["close"])
                tick = {
                    "bid": current_price,
                    "ask": current_price,
                    "mid": current_price,
                    "spread": None,
                    "spread_pips": None,
                }

        # 3. Monitoring layer always computes current market context
        ctx = self.context_engine.analyze(data, utc_dt=now)

        _session_label = getattr(ctx, "session_name", "unknown") if ctx else "unknown"
        _local_time = getattr(ctx, "local_time", "--:--") if ctx else "--:--"
        runtime_logger.info(
            "SESSION CONTEXT: label=%s | scan_allowed=true | mode=24_7 | local_time=%s",
            _session_label, _local_time,
        )
        self._last_scan_summary.update({
            "last_scan_number": self._scan_count,
            "session_blocking": False,
        })

        signals = []
        outlook = None
        watchlist_sent = 0

        # 3a. Scanning layer — always runs (24/7 mode)
        run_result = self.strategy_manager.run(data, current_price=current_price)
        outlook = run_result.outlook
        signals = run_result.signals
        ctx = outlook.context
        market_plan = None
        analyst_confirmations = []
        if current_price is not None:
            try:
                market_plan = self.market_analyst.analyze("XAUUSD", data, float(current_price), context=ctx)
                self._market_plan = market_plan.to_dict()
                self._merge_near_watchlists_into_market_plan(outlook, float(current_price), market_plan)
                self._market_plan = market_plan.to_dict()
                analyst_confirmations = self.gold_confirmation_engine.analyze(data.get("M15"), market_plan, float(current_price))
            except Exception as exc:
                logger.debug("Market analyst generation failed: %s", exc)
                self._market_plan = None
        strategy_scans = run_result.strategy_scans or {}
        cumulative_fields = {
            "scans_run",
            "candidates_found",
            "watchlist_alerts_sent",
            "entry_alerts_sent",
            "alerts_sent",
            "alerts_failed",
            "duplicates_blocked",
        }
        for strategy_name, summary in strategy_scans.items():
            if strategy_name not in self._last_scan_summary["strategy_scans"]:
                self._last_scan_summary["strategy_scans"][strategy_name] = {}
            existing = self._last_scan_summary["strategy_scans"][strategy_name]
            for key, value in summary.items():
                if key in cumulative_fields:
                    existing[key] = int(existing.get(key, 0) or 0) + int(value or 0)
                else:
                    existing[key] = value
            existing["last_scan_time"] = now.isoformat()

        # 3b. Log strategy performance and filter states (internal only)
        self._log_strategy_performance(run_result)
        if ctx:
            if not ctx.is_volatile:
                logger.debug("Filter: low volatility — signals may be weaker")
            if ctx.is_news_window:
                logger.debug("Filter: news window active")

        # 3c. Track structural level changes (state management — no Telegram send)
        if market_plan is not None and getattr(market_plan, "targets_source", "") != "structure_tp_engine":
            logger.warning(
                "OLD TARGET PATH BLOCKED: source=%s reason=market_plan_requires_structure_targets",
                getattr(market_plan, "targets_source", "unknown"),
            )
        if market_plan is not None and current_price and not in_silent_phase:
            self._handle_market_plan_alerts(
                market_plan,
                analyst_confirmations,
                float(current_price),
                ctx,
                active_signals=signals,
            )

        outlook_key = self._outlook_fingerprint(outlook)
        if outlook_key != self._last_outlook_hash:
            self._last_outlook_hash = outlook_key
            self._seen_setups.clear()
            self._last_watch_distance.clear()
            logger.info("Structural levels refreshed (%d groups)", len(outlook.timeframe_levels))
            runtime_logger.info(
                "WATCHLIST STRUCTURE REFRESHED: timeframe_groups=%d | watchlist dedupe preserved for instance_id=%s",
                len(outlook.timeframe_levels),
                self._instance_id,
            )

        # 3d. Alert layer — always active (24/7 mode)
        if current_price and not in_silent_phase:
            watchlist_sent = self._send_shortlisted_level_alerts(outlook, current_price) or 0
            self._check_watch_levels(outlook, current_price)
            gap_scan = self._last_scan_summary["strategy_scans"].setdefault("gap_liquidity_sweep_reclaim", {})
            gap_scan["watchlist_alerts_sent"] = gap_scan.get("watchlist_alerts_sent", 0) + watchlist_sent
            gap_scan["alerts_failed"] = gap_scan.get("alerts_failed", 0) + int(self._last_scan_summary.get("last_alerts_failed", 0) or 0)
            gap_scan["duplicates_blocked"] = gap_scan.get("duplicates_blocked", 0) + int(self._last_scan_summary.get("dedupe_rejections", 0) or 0)

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

                        # ── Live strategy gate ────────────────────────────────
                        # Research-only strategies are blocked from live alerts
                        # and live trade tracking regardless of confidence score.
                        _trade_strategy = getattr(trade, "strategy_type", "gap_liquidity_sweep_reclaim") or "gap_liquidity_sweep_reclaim"
                        if _trade_strategy in RESEARCH_ONLY_STRATEGIES:
                            logger.info(
                                "LIVE STRATEGY BLOCKED: %s is research-only — "
                                "no Telegram alert, no live trade tracking | %s %s @ %.2f",
                                _trade_strategy,
                                trade.direction, trade.pair, trade.entry_price,
                            )
                            continue

                        # ── Alert sequence ───────────────────────────────────
                        self._confirmed_setups.add(fp)
                        self._mark_level_confirmed(level_id)

                        # Use this signal's own strategy score for alert context
                        _sig_score_obj = run_result.strategy_scores.get(sig.strategy_name)
                        _strat_score   = _sig_score_obj.raw_score / 100.0 if _sig_score_obj else 0.5

                        logger.info(
                            "FIRST REJECTION CONFIRMED: %s %s | strategy=%s | level=%.2f | %s rejection closed correctly | pending order ready",
                            trade.direction,
                            trade.pair,
                            getattr(trade, "strategy_type", "gap_liquidity_sweep_reclaim"),
                            trade.entry_price,
                            trade.lower_tf,
                        )

                        logger.info(
                            "STRATEGY CLASSIFIED SETUP: strategy=%s analyst_scenario=%s_%.2f_%.2f",
                            sig.strategy_name,
                            trade.direction,
                            min(trade.entry_price, getattr(trade, "level_price", trade.entry_price)),
                            max(trade.entry_price, getattr(trade, "level_price", trade.entry_price)),
                        )

                        if LIVE_ARCHITECTURE_MODE == "five_layer":
                            logger.warning("OLD LIVE ALERT PATH BLOCKED: five_layer mode active")
                            continue
                        if LIVE_ALERT_ROUTING == "analyst_layer":
                            logger.warning("OLD LIVE ALERT ROUTE BLOCKED: use analyst_layer routing")
                            continue

                        # Pending-order alert. Registration below stores the
                        # setup as PENDING until the later retest/fill occurs.
                        alert_sent = self.telegram.send_confirmation(trade, strategy_score=_strat_score)
                        if alert_sent:
                            self._record_live_alert(sig.strategy_name, trade, alert_stage="entry" if sig.strategy_name == "gap_liquidity_sweep_reclaim" else "setup")
                        if alert_sent:
                            logger.info(
                                "PENDING ORDER ALERT SENT: %s %s | strategy=%s | entry=%.2f | SL=%.2f | TP1=%.2f",
                                trade.direction,
                                trade.pair,
                                getattr(trade, "strategy_type", "gap_liquidity_sweep_reclaim"),
                                trade.entry_price,
                                trade.sl_price,
                                trade.tp1,
                            )
                            strategy_scan = self._last_scan_summary["strategy_scans"].setdefault(sig.strategy_name, {})
                            if sig.strategy_name == "gap_liquidity_sweep_reclaim":
                                strategy_scan["entry_alerts_sent"] = int(strategy_scan.get("entry_alerts_sent", 0) or 0) + 1
                            else:
                                strategy_scan["alerts_sent"] = int(strategy_scan.get("alerts_sent", 0) or 0) + 1

                        # Register for simulated pending-order tracking.
                        self.trade_mgr.register_trade(trade)

                        logger.info(
                            "Pending order dispatched: %s %s @ %.2f | strategy=%s | SL %.2f (%dp) | Conf %.0f%%",
                            trade.direction, trade.pair, trade.entry_price,
                            getattr(trade, "strategy_type", "gap_liquidity_sweep_reclaim"),
                            trade.sl_price,
                            int(abs(trade.entry_price - trade.sl_price)),
                            trade.confidence * 100,
                        )

        elif self._scan_count % 5 == 0 and self._bot_window_active(ctx):
            logger.debug(
                "Scan #%d — no new setups | session=%s | bias=%s",
                self._scan_count,
                ctx.session_name if ctx else "off",
                ctx.h4_bias if ctx else "neutral",
            )

        # 6. Update tracked setups against live price (simulated tracking)
        if current_price:
            self.trade_mgr.update(current_price)
            self._update_analyst_trade_feedback(float(current_price), ctx, candle_high=candle_high, candle_low=candle_low)
            logger.debug("Price update @ %.2f", current_price)

        # 6b. Track manual setups — price proximity and confirmation alerts
        if current_price and not in_silent_phase:
            self._track_manual_setups(current_price)

        # 7. Periodically refresh learning (every 5 scans = every 5 minutes)
        if self._scan_count % 5 == 0:
            self._refresh_learning()

        # 8. Check if we should send daily summary
        self._check_daily_summary(now)

        # 9. Periodic no-setup status alert (disabled by default — opt-in via config)
        if not in_silent_phase and SEND_NO_SETUP_STATUS_ALERT:
            if not signals and watchlist_sent == 0:
                now_ts = datetime.now(timezone.utc)
                interval = timedelta(minutes=NO_SETUP_STATUS_INTERVAL_MINUTES)
                if (self._last_no_setup_alert_time is None
                        or (now_ts - self._last_no_setup_alert_time) >= interval):
                    session_name = getattr(ctx, "session_name", "unknown") if ctx else "unknown"
                    bias = getattr(ctx, "h4_bias", "neutral") if ctx else "neutral"
                    if True:
                        if self._send_runtime_system_alert(
                            f"Spencer is watching — no active setups in the last "
                            f"{NO_SETUP_STATUS_INTERVAL_MINUTES} minutes.\n"
                            f"Session: {session_name} | Bias: {bias}",
                            alert_type="no_setup_status",
                            source_module="main",
                            source_function="_scan_cycle",
                            cooldown_minutes=NO_SETUP_STATUS_INTERVAL_MINUTES,
                        ):
                            self._last_no_setup_alert_time = now_ts
                            logger.info(
                                "NO WATCHLIST SETUPS FOUND: no-setup alert sent (session=%s bias=%s)",
                                session_name, bias,
                            )

        # 10. Write heartbeat so the API can surface richer status
        self._write_heartbeat(in_silent_phase, signals, ctx, current_price, tick)
        logger.info(
            "SCAN COMPLETE: symbol=XAUUSD | scan=%d | signals=%d | watchlist_sent=%d",
            self._scan_count, len(signals or []), watchlist_sent,
        )
        runtime_logger.info(
            "SCAN COMPLETE: symbol=XAUUSD | scan=%d | signals=%d | watchlist=%d | reject=%s | tg=%s",
            self._scan_count,
            len(signals or []),
            watchlist_sent,
            self._last_scan_summary.get("last_reject_reason", ""),
            self._last_scan_summary.get("last_telegram_status", "none"),
        )

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
    # MANUAL SETUP TRACKING
    # ─────────────────────────────────────────────────────

    def _track_manual_setups(self, current_price: float) -> None:
        """Check active manual setups against live price; fire approach/confirmation alerts."""
        try:
            setups = self.db.get_watching_manual_setups()
        except Exception as exc:
            logger.debug("Manual setup fetch failed: %s", exc)
            return

        if not setups:
            return

        approach_dist = MANUAL_SETUP_APPROACH_DISTANCE_PIPS * PIP_SIZE
        cooldown = timedelta(minutes=MANUAL_SETUP_ALERT_COOLDOWN_MINUTES)
        now = datetime.now(timezone.utc)

        for setup in setups:
            setup_id = setup.get("id")
            entry = setup.get("entry_price")
            if not setup_id or not entry:
                continue

            try:
                entry = float(entry)
            except (TypeError, ValueError):
                continue

            distance_pips = abs(current_price - entry) / PIP_SIZE
            logger.info(
                "MANUAL SETUP TRACKING: setup_id=%s | status=%s | entry=%.2f | current=%.2f | distance=%.1fp",
                setup_id, setup.get("tracking_status", "watching"), entry, current_price, distance_pips,
            )

            if not setup.get("enable_telegram_alerts", True):
                continue

            if distance_pips <= MANUAL_SETUP_APPROACH_DISTANCE_PIPS:
                last_alerted = self._manual_setup_approach_alerted.get(setup_id)
                if last_alerted and (now - last_alerted) < cooldown:
                    continue

                logger.info("MANUAL SETUP APPROACHING: setup_id=%s | distance=%.1fp", setup_id, distance_pips)
                sent = self.telegram.send_manual_setup_approaching(setup, current_price, distance_pips)
                if sent:
                    self._manual_setup_approach_alerted[setup_id] = now
                    self._last_scan_summary["instance_totals"]["manual_alerts_sent"] += 1
                    self._last_scan_summary["last_telegram_status"] = "success"
                    self._last_scan_summary["last_telegram_alert_type"] = "manual_setup"
                    self._last_scan_summary["last_telegram_alert_time"] = now.isoformat()
                    try:
                        self.db.update_manual_setup(setup_id, {
                            "tracking_status": "approaching_entry",
                            "approach_alert_sent_at": now.isoformat(),
                            "last_alert_type": "approaching_entry",
                            "last_alert_time": now.isoformat(),
                        })
                    except Exception as exc:
                        logger.debug("Manual setup status update failed: %s", exc)

    # ─────────────────────────────────────────────────────
    # HEARTBEAT
    # ─────────────────────────────────────────────────────

    def _persist_analyst_market_plan(self, plan_dict: dict[str, Any], context) -> None:
        try:
            session_name = getattr(context, "session_name", "unknown") if context else "unknown"
            market_condition = getattr(context, "market_condition", "unknown") if context else "unknown"
            h4_bias = str(plan_dict.get("dominant_bias", "neutral"))
            h1_bias = str(plan_dict.get("market_structure_state", "unknown"))
            for label in ("primary", "secondary"):
                scenario = (plan_dict.get(f"{label}_scenario") or {})
                if not scenario:
                    continue
                scenario_key = f"{label}:{scenario.get('direction', '')}:{scenario.get('watch_zone', '')}:{plan_dict.get('market_plan_signature', '')}"
                if scenario_key in self._analyst_scenario_rows:
                    continue
                watch_zone = str(scenario.get("watch_zone", "0-0"))
                zone_low, zone_high = self._split_zone_bounds(watch_zone)
                row_id = self.db.insert_analyst_scenario_history(
                    {
                        "run_id": self._analyst_run_id,
                        "symbol": plan_dict.get("symbol", "XAUUSD"),
                        "scenario_key": scenario_key,
                        "source": "live",
                        "primary_or_secondary": label,
                        "scenario_type": f"{scenario.get('direction', '').lower()}_{label}",
                        "direction": scenario.get("direction", ""),
                        "zone_low": zone_low,
                        "zone_high": zone_high,
                        "trigger_conditions": scenario.get("triggers", []),
                        "invalidation_level": self._parse_numeric_level(scenario.get("invalidation")),
                        "tp_targets": scenario.get("targets", []),
                        "h4_bias": h4_bias,
                        "h1_bias": h1_bias,
                        "dominant_bias": plan_dict.get("dominant_bias", "neutral"),
                        "bias_strength": plan_dict.get("bias_strength", "weak"),
                        "session_name": session_name,
                        "psychological_level_context": plan_dict.get("actionable_psych_levels", []),
                        "market_condition": market_condition,
                        "status": plan_dict.get("plan_status", "active"),
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "notes": scenario.get("reason", ""),
                    }
                )
                if row_id is not None:
                    self._analyst_scenario_rows[scenario_key] = row_id
        except Exception as exc:
            logger.debug("Analyst scenario persistence skipped: %s", exc)

    def _persist_analyst_confirmation(
        self,
        confirmation,
        *,
        context,
        decision: str,
        rejection_reason: str = "",
        learning_score=None,
        final_outcome: str = "",
        pips_result: float = 0.0,
        tp1_hit: bool = False,
        tp2_hit: bool = False,
        tp3_hit: bool = False,
        sl_hit: bool = False,
    ) -> None:
        try:
            row_id = self._analyst_confirmation_rows.get(confirmation.confirmation_signature)
            payload = {
                "run_id": self._analyst_run_id,
                "symbol": "XAUUSD",
                "scenario_key": f"{confirmation.scenario}:{confirmation.direction}:{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}",
                "confirmation_key": confirmation.confirmation_signature,
                "source": "live",
                "scenario_type": str(getattr(confirmation, "scenario", "primary")),
                "confirmation_type": confirmation.confirmation_type,
                "confirmation_grade": getattr(confirmation, "confirmation_grade", confirmation.grade),
                "confirmation_score": float(getattr(confirmation, "confirmation_score", confirmation.score)),
                "direction": confirmation.direction,
                "level": float(confirmation.level),
                "entry": float(confirmation.suggested_entry),
                "sl": float(confirmation.suggested_sl),
                "tp1": float(confirmation.suggested_tps.get("tp1", 0.0)),
                "tp2": float(confirmation.suggested_tps.get("tp2", 0.0)),
                "tp3": float(confirmation.suggested_tps.get("tp3", 0.0)),
                "decision": decision,
                "rejection_reason": rejection_reason,
                "session_name": getattr(context, "session_name", "unknown") if context else "unknown",
                "timeframe": "M15",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "final_outcome": final_outcome or None,
                "pips_result": pips_result if final_outcome else None,
                "tp1_hit": tp1_hit,
                "tp2_hit": tp2_hit,
                "tp3_hit": tp3_hit,
                "sl_hit": sl_hit,
            }
            if row_id is None:
                row_id = self.db.insert_analyst_confirmation_history(payload)
                if row_id is not None:
                    self._analyst_confirmation_rows[confirmation.confirmation_signature] = row_id
            elif final_outcome:
                self.db.update_analyst_confirmation_history(row_id, payload)
        except Exception as exc:
            logger.debug("Analyst confirmation persistence skipped: %s", exc)

    def _update_analyst_trade_feedback(
        self,
        current_price: float,
        context,
        candle_high: float | None = None,
        candle_low: float | None = None,
    ) -> None:
        active_count = len(self.trade_management_engine._active_setups)
        logger.info(
            "TRADE MANAGEMENT SCAN: active_trades=%d current_price=%.2f candle_high=%s candle_low=%s",
            active_count,
            current_price,
            f"{candle_high:.2f}" if candle_high is not None else "n/a",
            f"{candle_low:.2f}" if candle_low is not None else "n/a",
        )
        if active_count == 0:
            logger.info("MISSED TP ALERT DIAGNOSTIC: setup_id=n/a reason=no_active_trade_registered")
        for setup_id, state in list(self.trade_management_engine._active_setups.items()):
            previous_status = state.current_status
            updated = self.trade_management_engine.update_trade(
                setup_id,
                current_price,
                candle_high=candle_high,
                candle_low=candle_low,
                candle_time=str(getattr(context, "candle_time", "") or ""),
            )
            if updated is None:
                continue
            self._send_trade_management_alerts(updated, current_price)
            self._last_scan_summary["five_layer_status"]["risk_management"] = updated.to_dict()
            if updated.current_status != previous_status:
                logger.info("TRADE MANAGEMENT UPDATE: setup_id=%s status=%s", setup_id, updated.current_status)
            if updated.review_required and setup_id not in self._analyst_reviewed_setups:
                metadata = dict(updated.metadata or {})
                review = self.outcome_tracker.store_review(
                    updated,
                    str(metadata.get("confirmation_type", "")),
                    str(metadata.get("confirmation_grade", "")),
                    float(metadata.get("learning_score", 0.0) or 0.0),
                    review_notes=str(metadata.get("review_notes", "")),
                )
                if review.result == "LOSS":
                    date_key = review.created_at.split("T", 1)[0] if "T" in review.created_at else "unknown_date"
                    self.decision_engine.record_loss(symbol=review.symbol, date_key=date_key)
                self._analyst_reviewed_setups.add(setup_id)
                try:
                    self.db.insert_analyst_trade_review(
                        {
                            "run_id": review.run_id,
                            "setup_id": review.setup_id,
                            "symbol": review.symbol,
                            "scenario_key": review.scenario_key,
                            "scenario_type": review.scenario_type,
                            "direction": review.direction,
                            "entry": review.entry,
                            "sl": review.sl,
                            "tp1": review.tp1,
                            "tp2": review.tp2,
                            "tp3": review.tp3,
                            "confirmation_type": review.confirmation_type,
                            "confirmation_grade": review.confirmation_grade,
                            "learning_score": review.learning_score,
                            "reaction_level": review.reaction_level,
                            "invalidation_level": review.invalidation_level,
                            "risk_pips": review.risk_pips,
                            "tp1_reward_pips": review.tp1_reward_pips,
                            "tp2_reward_pips": review.tp2_reward_pips,
                            "tp3_reward_pips": review.tp3_reward_pips,
                            "tp1_rr": review.tp1_rr,
                            "tp2_rr": review.tp2_rr,
                            "tp3_rr": review.tp3_rr,
                            "sl_source": review.sl_source,
                            "tp_source": review.tp_source,
                            "trade_path_source": review.trade_path_source,
                            "trade_path_rationale": review.trade_path_rationale,
                            "target_roles": review.target_roles,
                            "setup_quality_label": review.setup_quality_label,
                            "decision_reason": review.decision_reason,
                            "session_name": review.session_name,
                            "h4_bias": review.h4_bias,
                            "h1_bias": review.h1_bias,
                            "market_condition": review.market_condition,
                            "result": review.result,
                            "pips_result": review.pips_result,
                            "tp1_hit": review.tp1_hit,
                            "tp2_hit": review.tp2_hit,
                            "tp3_hit": review.tp3_hit,
                            "protected_after_tp1": review.protected_after_tp1,
                            "review_notes": review.review_notes,
                            "created_at": review.created_at,
                            "closed_at": review.closed_at,
                        }
                    )
                    confirmation_key = str(metadata.get("confirmation_key", ""))
                    row_id = self._analyst_confirmation_rows.get(confirmation_key)
                    if row_id is not None:
                        self.db.update_analyst_confirmation_history(
                            row_id,
                            {
                                "final_outcome": review.result,
                                "pips_result": review.pips_result,
                                "tp1_hit": review.tp1_hit,
                                "tp2_hit": review.tp2_hit,
                                "tp3_hit": review.tp3_hit,
                                "sl_hit": review.result == "LOSS",
                            },
                        )
                    scenario_row = self._analyst_scenario_rows.get(str(metadata.get("scenario_db_key", "")))
                    if scenario_row is not None:
                        self.db.update_analyst_scenario_history(
                            scenario_row,
                            {
                                "status": "played_out" if review.tp1_hit else "invalidated" if review.result == "LOSS" else "active",
                                "resolved_at": datetime.now(timezone.utc).isoformat(),
                                "final_outcome": "TP3_HIT" if review.tp3_hit else "TP2_HIT" if review.tp2_hit else "TP1_HIT" if review.tp1_hit else "SL_HIT" if review.result == "LOSS" else review.result,
                                "max_favorable_pips": review.pips_result if review.pips_result > 0 else 0.0,
                                "max_adverse_pips": abs(review.pips_result) if review.pips_result < 0 else 0.0,
                                "tp1_reached": review.tp1_hit,
                                "tp2_reached": review.tp2_hit,
                                "tp3_reached": review.tp3_hit,
                            },
                        )
                except Exception as exc:
                    logger.debug("Analyst trade review persistence skipped: %s", exc)
                if review.result in {"LOSS", "BREAKEVEN_WIN", "WIN", "STRONG_WIN"}:
                    self.trade_management_engine._active_setups.pop(setup_id, None)
                    self._save_memory()

    def _send_trade_management_alerts(self, state, current_price: float) -> None:
        if not bool(getattr(state, "lifecycle_alerts_enabled", True)) or not bool(getattr(state, "actionable_trade", True)):
            if state.management_alert_required:
                logger.info(
                    "TP ALERT SUPPRESSED: setup_id=%s reason=lifecycle_alerts_muted imported_from_memory=%s",
                    state.setup_id,
                    getattr(state, "imported_from_memory", False),
                )
            state.management_alert_required = False
            return
        state_dict = state.to_dict()
        alert_plan = []
        if state.current_status in {"tp1_hit", "tp2_hit", "tp3_hit"} and not state.tp1_alert_sent:
            alert_plan.append(("TP1", self.telegram.send_tp1_be_alert))
        if state.current_status in {"tp2_hit", "tp3_hit"} and not state.tp2_alert_sent:
            alert_plan.append(("TP2", self.telegram.send_tp2_alert))
        if state.current_status == "tp3_hit" and not state.tp3_alert_sent:
            alert_plan.append(("TP3", self.telegram.send_tp3_alert))
        if state.current_status == "breakeven_win":
            alert_plan.append(("BE", self.telegram.send_breakeven_exit_alert))
        if state.current_status == "stopped" and not state.sl_alert_sent:
            alert_plan.append(("SL", self.telegram.send_sl_before_tp1_alert))

        for level_name, sender in alert_plan:
            dedupe_key = f"{state.setup_id}:{level_name}"
            if self.trade_management_engine.alert_already_sent(state.setup_id, level_name):
                logger.info("TP ALERT SUPPRESSED: reason=duplicate dedupe_key=%s", dedupe_key)
                continue
            if level_name in {"BE", "SL"}:
                sent = sender(state_dict)
            else:
                sent = sender(state_dict, current_price)
            if sent:
                self.trade_management_engine.mark_alert_sent(state.setup_id, level_name)
                self.trade_management_engine.last_tp_alert_sent = datetime.now(timezone.utc).isoformat()
                if level_name == "TP1":
                    state.tp1_alert_sent = True
                    state.protected_after_tp1 = True
                    state.be_alert_sent = True
                    logger.info("TELEGRAM SEND OK: alert_type=TP1_BE")
                elif level_name == "TP2":
                    state.tp2_alert_sent = True
                    logger.info("TELEGRAM SEND OK: alert_type=TP2")
                elif level_name == "TP3":
                    state.tp3_alert_sent = True
                    logger.info("TELEGRAM SEND OK: alert_type=TP3")
                elif level_name == "BE":
                    state.be_alert_sent = True
                    logger.info("TELEGRAM SEND OK: alert_type=BE")
                elif level_name == "SL":
                    state.sl_alert_sent = True
                    logger.info("TELEGRAM SEND OK: alert_type=SL")
                self._save_memory()
            else:
                reason = getattr(self.telegram, "_last_error", "") or "telegram_send_failed"
                logger.info("MISSED TP ALERT DIAGNOSTIC: setup_id=%s reason=%s", state.setup_id, reason)

    def _handle_market_plan_alerts(self, market_plan, confirmations: list, current_price: float, context, active_signals: list | None = None) -> None:
        plan_dict = market_plan.to_dict() if hasattr(market_plan, "to_dict") else dict(market_plan or {})
        signature = self._market_plan_signature(plan_dict)
        now = datetime.now(timezone.utc)
        session_name = getattr(context, "session_name", "unknown") if context else "unknown"
        primary = plan_dict.get("primary_scenario", {}) or {}
        secondary = plan_dict.get("secondary_scenario", {}) or {}
        self._persist_analyst_market_plan(plan_dict, context)
        self._last_scan_summary["five_layer_status"]["market_analyst"] = {
            "bias": plan_dict.get("dominant_bias"),
            "plan_status": plan_dict.get("plan_status"),
            "primary_scenario": primary.get("watch_zone"),
            "secondary_scenario": secondary.get("watch_zone"),
            "watch_zones": plan_dict.get("active_watch_zones", []),
            "signature": plan_dict.get("market_plan_signature", signature),
            "level_intelligence": plan_dict.get("level_intelligence", {}),
            "deep_context_levels": plan_dict.get("deep_context_levels", {}),
            "activation_pipeline": plan_dict.get("activation_pipeline", {}),
        }
        self._last_scan_summary["five_layer_status"]["level_intelligence"] = plan_dict.get("level_intelligence", {})
        self._last_scan_summary["five_layer_status"]["activation_pipeline"] = plan_dict.get("activation_pipeline", {})

        current_primary_key = f"{primary.get('direction')}:{primary.get('watch_zone')}:{primary.get('invalidation')}"
        should_send_plan = False
        send_reason = ""
        if self._last_market_plan_sent_at is None:
            should_send_plan = True
            send_reason = "startup_complete"
        elif self._last_primary_scenario_key and self._last_primary_scenario_key != current_primary_key:
            should_send_plan = True
            send_reason = "scenario_changed"
        elif session_name in {"london", "new_york"} and self._last_session_market_plan != session_name:
            should_send_plan = True
            send_reason = "session_transition"

        if should_send_plan:
            market_plan_key = self._build_market_plan_event_key(plan_dict)
            allowed, reason = self._alert_dedupe.should_send_alert(
                "market_plan",
                market_plan_key,
                signature,
                cooldown_seconds=PRIMARY_SETUP_ALERT_COOLDOWN_MINUTES * 60,
            )
            if not allowed and not MARKET_PLAN_RESEND_ON_MINOR_PRICE_CHANGE:
                logger.info("MARKET PLAN SKIPPED: %s", reason)
                self._sync_dedupe_summary()
            elif self.telegram.send_market_plan_alert(market_plan):
                self._alert_dedupe.mark_alert_sent(
                    "market_plan",
                    market_plan_key,
                    signature,
                    metadata={"reason": send_reason, "session_name": session_name},
                )
                logger.info("MARKET PLAN SENT: reason=%s", send_reason)
                self._last_scan_summary["last_telegram_status"] = "success"
                self._last_scan_summary["last_telegram_alert_type"] = "market_plan"
                self._last_scan_summary["last_telegram_alert_time"] = now.isoformat()
                self._last_market_plan_sent_at = now
                self._last_market_plan_hash = signature
                self._last_session_market_plan = session_name
                self._save_memory()

        primary_key = current_primary_key
        if self._last_primary_scenario_key and self._last_primary_scenario_key != primary_key:
            from analysis.scenario_classifier import classify_scenario_change

            symbol = plan_dict.get("symbol", "XAUUSD")
            change = classify_scenario_change(
                symbol=symbol,
                previous_primary=self._last_primary_scenario_snapshot or {},
                current_primary=primary,
                previous_secondary=self._last_secondary_scenario_snapshot or {},
                current_secondary=secondary,
                previous_bias=self._last_dominant_bias or plan_dict.get("dominant_bias", "neutral"),
                current_bias=plan_dict.get("dominant_bias", "neutral"),
                previous_bias_strength=self._last_bias_strength or plan_dict.get("bias_strength", "weak"),
                current_bias_strength=plan_dict.get("bias_strength", "weak"),
                current_price=current_price,
            )
            if not change.should_send:
                logger.info(
                    "SCENARIO UPDATE SUPPRESSED: type=%s reason=%s zone_shift=%s",
                    change.change_type,
                    change.reason,
                    change.details.get("zone_shift_pips"),
                )
            else:
                logger.info(
                    "SCENARIO UPDATE CLASSIFIED: type=%s reason=%s severity=%s zone_shift=%s",
                    change.change_type,
                    change.reason,
                    change.severity,
                    change.details.get("zone_shift_pips"),
                )
                self._send_scenario_update(
                    {
                        "symbol": symbol,
                        "message": change.message,
                        "title": change.title,
                        "change_type": change.change_type,
                        "severity": change.severity,
                        "primary": f"{primary.get('direction')} {primary.get('watch_zone')}",
                        "secondary": f"{secondary.get('direction')} {secondary.get('watch_zone')}",
                        "waiting_for": " / ".join(plan_dict.get("confirmation_waiting_for", [])[:4]),
                    },
                    now,
                    event_key=self._build_scenario_update_event_key(
                        symbol=symbol,
                        event_type=change.change_type,
                        old_scenario_key=self._last_primary_scenario_key,
                        new_scenario_key=primary_key,
                    ),
                    change_type=change.change_type,
                    cooldown_minutes=change.cooldown_minutes,
                    dedupe_key=change.dedupe_key,
                )
                self._save_memory()
        self._last_primary_scenario_key = primary_key
        self._last_primary_scenario_snapshot = dict(primary or {})
        self._last_secondary_scenario_snapshot = dict(secondary or {})
        self._last_dominant_bias = plan_dict.get("dominant_bias", "neutral")
        self._last_bias_strength = plan_dict.get("bias_strength", "weak")

        self._send_resume_watch_alerts(plan_dict, primary, secondary, now)

        for zone in plan_dict.get("active_watch_zones", []) or []:
            zone_id = str(zone.get("zone_id"))
            previous = self._watch_zone_state.get(zone_id)
            zone_copy = dict(zone)
            zone_copy["persisted_at"] = now.isoformat()
            self._watch_zone_state[zone_id] = zone_copy
            if previous and previous.get("status") != zone.get("status"):
                logger.info("SCENARIO UPDATED: primary=%s", primary.get("direction"))
                if zone.get("status") == "active":
                    logger.info("WATCH ZONE APPROACHED: zone=%s direction=%s", zone_id, zone.get("direction"))
                    self._send_scenario_update(
                        {
                            "symbol": plan_dict.get("symbol", "XAUUSD"),
                            "message": f"Price entered watch zone {zone.get('level_low', 0.0):.2f}-{zone.get('level_high', 0.0):.2f}.",
                            "primary": f"{primary.get('direction')} {primary.get('watch_zone')}",
                            "secondary": f"{secondary.get('direction')} {secondary.get('watch_zone')}",
                            "waiting_for": " / ".join(plan_dict.get("confirmation_waiting_for", [])[:4]),
                        },
                        now,
                        event_key=self._build_scenario_update_event_key(
                            symbol=plan_dict.get("symbol", "XAUUSD"),
                            event_type="watch_zone_update",
                            new_scenario_key=primary_key,
                            level=f"{zone.get('level_low', 0.0):.2f}-{zone.get('level_high', 0.0):.2f}",
                            status="active",
                        ),
                    )
                elif zone.get("status") == "played_out" and zone.get("played_out_note"):
                    self._send_scenario_update(
                        {
                            "symbol": plan_dict.get("symbol", "XAUUSD"),
                            "message": str(zone.get("played_out_note")),
                            "primary": f"{primary.get('direction')} {primary.get('watch_zone')}",
                            "secondary": f"{secondary.get('direction')} {secondary.get('watch_zone')}",
                            "waiting_for": " / ".join(plan_dict.get("confirmation_waiting_for", [])[:4]),
                        },
                        now,
                        event_key=self._build_scenario_update_event_key(
                            symbol=plan_dict.get("symbol", "XAUUSD"),
                            event_type="played_out",
                            new_scenario_key=primary_key,
                            level=f"{zone.get('level_low', 0.0):.2f}-{zone.get('level_high', 0.0):.2f}",
                            status="played_out",
                        ),
                    )
        self._save_memory()

        for confirmation in confirmations:
            allowed, reason = confirmation_is_fresh(confirmation, current_price)
            if not allowed:
                logger.info("SETUP NOT SENT: already played out / chase distance exceeded")
                logger.info(reason)
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="reject",
                    rejection_reason=reason,
                )
                self._last_scan_summary["five_layer_status"]["confirmation_engine"] = {
                    "last_confirmation": confirmation.to_dict(),
                    "status": "rejected",
                    "reason": reason,
                }
                continue
            if confirmation.grade not in {"A", "A+"}:
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="wait",
                    rejection_reason="grade_b_or_lower",
                )
                self._last_scan_summary["five_layer_status"]["confirmation_engine"] = {
                    "last_confirmation": confirmation.to_dict(),
                    "status": "waiting",
                    "reason": "grade_b_or_lower",
                }
                continue
            scenario_key = f"{confirmation.direction}:{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}"
            confirm_key = self._build_entry_event_key(confirmation, scenario_key)
            trade_idea_key = self._build_trade_idea_dedupe_key(confirmation)
            payload_signature = self._event_signature(
                {
                    "direction": confirmation.direction,
                    "scenario_key": scenario_key,
                    "confirmation_type": confirmation.confirmation_type,
                    "entry": round(float(confirmation.suggested_entry), 2),
                    "sl": round(float(confirmation.suggested_sl), 2),
                    "tp1": round(float(confirmation.suggested_tps.get("tp1", 0.0)), 2),
                    "grade": confirmation.grade,
                }
            )
            strategy_context = self._classify_analyst_setup(confirmation, active_signals or [])
            learning_context = {
                "session_name": session_name,
                "timeframe": "M15",
                "direction": confirmation.direction,
                "dominant_bias": plan_dict.get("dominant_bias", "neutral"),
                "bias_strength": plan_dict.get("bias_strength", "weak"),
                "confirmation_type": confirmation.confirmation_type,
            }
            learning_score = self.scoring_engine.score_confirmation(
                market_plan,
                confirmation,
                strategy_context.get("strategy_type", "analyst_layer"),
                learning_context,
            )
            self._last_scan_summary["five_layer_status"]["learning_score"] = learning_score.to_dict()
            duplicate_trade_idea, existing_trade_idea_key = self._alert_dedupe.find_recent_matching(
                "analyst_entry",
                lambda _event_key, entry: self._same_trade_idea_metadata(entry.get("metadata") or {}, confirmation),
                cooldown_seconds=max(ENTRY_ALERT_COOLDOWN_MINUTES, 60) * 60,
            )
            if duplicate_trade_idea:
                existing_setup_id = str((self._alert_dedupe.sent.get("analyst_entry", {}).get(existing_trade_idea_key or "", {}).get("metadata") or {}).get("setup_id") or existing_trade_idea_key or trade_idea_key)
                self.trade_management_engine.merge_confirmation(existing_setup_id, confirmation.confirmation_type)
                logger.info(
                    "ENTRY ALERT MERGED: same_trade_idea dedupe_key=%s confirmations=%s",
                    trade_idea_key,
                    confirmation.confirmation_type,
                )
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="ignore",
                    rejection_reason="duplicate_trade_idea_merged",
                    learning_score=learning_score,
                )
                self._sync_dedupe_summary()
                self._save_memory()
                continue
            duplicate_zone, existing_key = self._alert_dedupe.find_recent_matching(
                "analyst_entry",
                lambda _event_key, entry: (
                    (entry.get("metadata") or {}).get("symbol") == "XAUUSD"
                    and (entry.get("metadata") or {}).get("direction") == confirmation.direction
                    and (entry.get("metadata") or {}).get("scenario_key") == scenario_key
                    and abs(float((entry.get("metadata") or {}).get("entry", 0.0)) - float(confirmation.suggested_entry)) <= ENTRY_ZONE_DUPLICATE_TOLERANCE_PIPS
                ),
                cooldown_seconds=ENTRY_ALERT_COOLDOWN_MINUTES * 60,
            )
            decision = self.decision_engine.decide(
                market_plan,
                confirmation,
                learning_score,
                duplicate_blocked=duplicate_zone,
                stale_reason="" if allowed else reason,
                gate_context={
                    "symbol": "XAUUSD",
                    "session_name": session_name,
                    "candle_time": getattr(confirmation, "candle_time", ""),
                    "zone_key": scenario_key,
                    "h1_state": getattr(context, "h1_state", "unknown"),
                    "h1_bias": getattr(context, "h1_bias", "neutral"),
                    "h4_bias": getattr(context, "h4_bias", "neutral"),
                    "dominant_bias": getattr(context, "dominant_bias", "neutral"),
                    "market_condition": getattr(context, "market_condition", "unknown"),
                },
            )
            self._last_scan_summary["five_layer_status"]["decision_engine"] = decision.to_dict()
            self._last_scan_summary["five_layer_status"]["pytorch_ai"] = decision.setup_payload.get("ai_prediction", {})
            if decision.action != "send_entry_alert":
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="ignore" if decision.action == "ignore" else "wait",
                    rejection_reason=decision.reason,
                    learning_score=learning_score,
                )
                logger.info("ENTRY ALERT SKIPPED: decision_action=%s reason=%s", decision.action, decision.reason)
                continue
            scenario_compliance = validate_scenario_compliance(
                confirmation,
                market_plan,
                ai_label=str(decision.setup_payload.get("ai_label") or ""),
                current_price=current_price,
            ) if SCENARIO_COMPLIANCE_ENABLED else None
            compliance_dict = scenario_compliance.to_dict() if scenario_compliance is not None else {
                "compliant": True,
                "reason": "scenario_compliance_disabled",
                "severity": "pass",
                "corrected_status": "actionable",
                "actionable": True,
            }
            decision.setup_payload["scenario_compliance"] = compliance_dict
            self._last_scan_summary["five_layer_status"]["scenario_compliance"] = compliance_dict
            if scenario_compliance is not None and not scenario_compliance.actionable:
                manual_payload = {
                    "symbol": "XAUUSD",
                    "direction": confirmation.direction,
                    "entry": confirmation.suggested_entry,
                    "scenario": str(getattr(confirmation, "scenario", "secondary")),
                    "reason": scenario_compliance.reason,
                    "details": scenario_compliance.details,
                    "primary": f"{primary.get('direction')} {primary.get('watch_zone')}",
                    "secondary": f"{secondary.get('direction')} {secondary.get('watch_zone')}",
                    "waiting_for": " / ".join((secondary if str(getattr(confirmation, "scenario", "primary")) == "secondary" else primary).get("trigger_conditions", [])[:4]),
                }
                self.telegram.send_scenario_manual_review_alert(manual_payload)
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="wait",
                    rejection_reason=scenario_compliance.reason,
                    learning_score=learning_score,
                )
                logger.info("ENTRY ALERT DOWNGRADED: scenario_compliance=%s corrected_status=%s", scenario_compliance.reason, scenario_compliance.corrected_status)
                continue
            if duplicate_zone:
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="ignore",
                    rejection_reason="same_zone_cooldown",
                    learning_score=learning_score,
                )
                logger.info("ENTRY ALERT SKIPPED: same zone cooldown")
                self._sync_dedupe_summary()
                continue
            can_send_entry, entry_reason = self._alert_dedupe.should_send_alert(
                "analyst_entry",
                confirm_key,
                payload_signature,
                cooldown_seconds=ENTRY_ALERT_COOLDOWN_MINUTES * 60,
            )
            if not can_send_entry:
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="ignore",
                    rejection_reason=entry_reason,
                    learning_score=learning_score,
                )
                logger.info("ENTRY ALERT SKIPPED: %s", "duplicate confirmation" if entry_reason != "cooldown_active" else "same zone cooldown")
                self._sync_dedupe_summary()
                continue
            logger.info(
                "CONFIRMATION DETECTED: type=%s grade=%s direction=%s",
                confirmation.confirmation_type,
                confirmation.grade,
                confirmation.direction,
            )
            logger.info(
                "ENTRY CONFIRMED: direction=%s entry=%.2f sl=%.2f tp1=%.2f tp2=%.2f tp3=%.2f",
                confirmation.direction,
                confirmation.suggested_entry,
                confirmation.suggested_sl,
                confirmation.suggested_tps.get("tp1", 0.0),
                confirmation.suggested_tps.get("tp2", 0.0),
                confirmation.suggested_tps.get("tp3", 0.0),
            )
            analyst_setup = AnalystTradeSetup(
                strategy_type=strategy_context.get("strategy_type", "analyst_layer"),
                scenario_id=confirmation.watch_zone_id,
                direction=confirmation.direction,
                entry=confirmation.suggested_entry,
                sl=confirmation.suggested_sl,
                tp1=confirmation.suggested_tps.get("tp1", 0.0),
                tp2=confirmation.suggested_tps.get("tp2", 0.0),
                tp3=confirmation.suggested_tps.get("tp3", 0.0),
                confirmation_type=confirmation.confirmation_type,
                confirmation_grade=confirmation.grade,
                entry_reason=confirmation.reason,
                sl_rationale=confirmation.sl_rationale,
                tp_rationale=confirmation.tp_rationale,
                invalidation=confirmation.invalidation,
                no_chase_status="fresh_or_active_only",
                reaction_level=float(getattr(confirmation, "reaction_level", 0.0) or 0.0),
                invalidation_level=float(getattr(confirmation, "invalidation_level", 0.0) or 0.0),
                risk_pips=float(getattr(confirmation, "risk_pips", 0.0) or 0.0),
                tp1_reward_pips=float(getattr(confirmation, "tp1_reward_pips", 0.0) or 0.0),
                tp2_reward_pips=float(getattr(confirmation, "tp2_reward_pips", 0.0) or 0.0),
                tp3_reward_pips=float(getattr(confirmation, "tp3_reward_pips", 0.0) or 0.0),
                tp1_rr=float(getattr(confirmation, "tp1_rr", 0.0) or 0.0),
                tp2_rr=float(getattr(confirmation, "tp2_rr", 0.0) or 0.0),
                tp3_rr=float(getattr(confirmation, "tp3_rr", 0.0) or 0.0),
                sl_source=str(getattr(confirmation, "sl_source", "structure_sl_engine")),
                tp_source=str(getattr(confirmation, "tp_source", "structure_tp_engine")),
                trade_path_source=str(getattr(confirmation, "trade_path_source", "trade_path_engine")),
                trade_path_rationale=str(getattr(confirmation, "trade_path_rationale", "")),
                target_roles=dict(getattr(confirmation, "target_roles", {}) or {}),
                setup_quality_label=quality_label_from_score(float(decision.setup_payload.get("candidate_rank_score", 0.0) or 0.0)),
                candidate_rank_score=float(decision.setup_payload.get("candidate_rank_score", 0.0) or 0.0),
                strategy_suggested_entry=strategy_context.get("strategy_suggested_entry"),
                strategy_suggested_sl=strategy_context.get("strategy_suggested_sl"),
                strategy_suggested_tp1=strategy_context.get("strategy_suggested_tp1"),
                strategy_suggested_tp2=strategy_context.get("strategy_suggested_tp2"),
                strategy_suggested_tp3=strategy_context.get("strategy_suggested_tp3"),
            )
            analyst_alert_payload = analyst_setup.to_dict()
            analyst_alert_payload["ai_prediction"] = decision.setup_payload.get("ai_prediction", {})
            analyst_alert_payload["ai_label"] = decision.setup_payload.get("ai_label")
            analyst_alert_payload["ai_would_block"] = decision.setup_payload.get("ai_would_block", False)
            analyst_alert_payload["scenario"] = str(getattr(confirmation, "scenario", "primary"))
            analyst_alert_payload["scenario_compliance"] = decision.setup_payload.get("scenario_compliance", {})
            analyst_alert_payload["level_intelligence"] = plan_dict.get("level_intelligence", {})
            if self.telegram.send_analyst_entry_alert(analyst_alert_payload):
                actionable_trade = analyst_setup.setup_quality_label != "WATCHLIST ONLY"
                scenario_db_key = f"{confirmation.scenario}:{confirmation.direction}:{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}:{plan_dict.get('market_plan_signature', '')}"
                self._persist_analyst_confirmation(
                    confirmation,
                    context=context,
                    decision="alert",
                    learning_score=learning_score,
                )
                self._alert_dedupe.mark_alert_sent(
                    "analyst_entry",
                    confirm_key,
                    payload_signature,
                    metadata={
                        "symbol": "XAUUSD",
                        "direction": confirmation.direction,
                        "scenario_key": scenario_key,
                        "trade_idea_key": trade_idea_key,
                        "scenario_type": str(getattr(confirmation, "scenario", "primary")),
                        "watch_zone_low": round(float(confirmation.zone_low), 2),
                        "watch_zone_high": round(float(confirmation.zone_high), 2),
                        "sl": round(float(confirmation.suggested_sl), 2),
                        "tp1": round(float(confirmation.suggested_tps.get("tp1", 0.0) or 0.0), 2),
                        "tp2": round(float(confirmation.suggested_tps.get("tp2", 0.0) or 0.0), 2),
                        "tp3": round(float(confirmation.suggested_tps.get("tp3", 0.0) or 0.0), 2),
                        "setup_id": trade_idea_key,
                        "entry": round(float(confirmation.suggested_entry), 2),
                    },
                )
                management_metadata = {
                        "run_id": self._analyst_run_id,
                        "scenario_key": scenario_key,
                        "scenario_db_key": scenario_db_key,
                        "scenario_type": str(getattr(confirmation, "scenario", "primary")),
                        "confirmation_key": confirmation.confirmation_signature,
                        "confirmation_type": confirmation.confirmation_type,
                        "confirmations": [confirmation.confirmation_type],
                        "confirmation_grade": confirmation.grade,
                        "learning_score": learning_score.final_score,
                        "decision_reason": decision.reason,
                        "session_name": session_name,
                        "h4_bias": plan_dict.get("dominant_bias", "neutral"),
                        "h1_bias": plan_dict.get("market_structure_state", "unknown"),
                        "market_condition": getattr(context, "market_condition", "unknown") if context else "unknown",
                        "reaction_level": analyst_setup.reaction_level,
                        "invalidation_level": analyst_setup.invalidation_level,
                        "risk_pips": analyst_setup.risk_pips,
                        "tp1_reward_pips": analyst_setup.tp1_reward_pips,
                        "tp2_reward_pips": analyst_setup.tp2_reward_pips,
                        "tp3_reward_pips": analyst_setup.tp3_reward_pips,
                        "tp1_rr": analyst_setup.tp1_rr,
                        "tp2_rr": analyst_setup.tp2_rr,
                        "tp3_rr": analyst_setup.tp3_rr,
                        "sl_source": analyst_setup.sl_source,
                        "tp_source": analyst_setup.tp_source,
                        "trade_path_source": analyst_setup.trade_path_source,
                        "trade_path_rationale": analyst_setup.trade_path_rationale,
                        "target_roles": analyst_setup.target_roles,
                        "setup_quality_label": analyst_setup.setup_quality_label,
                        "ai_prediction": decision.setup_payload.get("ai_prediction", {}),
                        "ai_label": decision.setup_payload.get("ai_label"),
                        "ai_would_block": decision.setup_payload.get("ai_would_block", False),
                        "scenario_compliance": decision.setup_payload.get("scenario_compliance", {}),
                        "level_intelligence": plan_dict.get("level_intelligence", {}),
                        "sent_at": now.isoformat(),
                        "actionable_trade": actionable_trade,
                        "candle_time": getattr(confirmation, "candle_time", ""),
                    }
                if actionable_trade:
                    management_state = self.trade_management_engine.register_setup(
                        trade_idea_key,
                        analyst_setup,
                        metadata=management_metadata,
                        current_price=current_price,
                        candle_high=getattr(confirmation, "candle_high", None),
                        candle_low=getattr(confirmation, "candle_low", None),
                        candle_time=str(getattr(confirmation, "candle_time", "") or ""),
                    )
                    self._last_scan_summary["five_layer_status"]["risk_management"] = management_state.to_dict()
                else:
                    logger.info("TRADE MANAGEMENT SKIPPED: setup_id=%s reason=watchlist_only_not_actionable", trade_idea_key)
                    self._last_scan_summary["five_layer_status"]["risk_management"] = {
                        "setup_id": trade_idea_key,
                        "current_status": "not_tracked",
                        "actionable_trade": False,
                        "reason": "watchlist_only_not_actionable",
                    }
                candle_time = str(getattr(confirmation, "candle_time", ""))
                date_key = candle_time.split("T", 1)[0] if "T" in candle_time else "unknown_date"
                self.decision_engine.record_entry(
                    symbol="XAUUSD",
                    date_key=date_key,
                    session_name=session_name,
                    zone_key=scenario_key,
                )
                logger.info("ENTRY ALERT SENT: event_key=%s", confirm_key)
                self._last_scan_summary["last_telegram_status"] = "success"
                self._last_scan_summary["last_telegram_alert_type"] = "analyst_entry"
                self._last_scan_summary["last_telegram_alert_time"] = now.isoformat()
                self._save_memory()

    def _merge_near_watchlists_into_market_plan(self, outlook, current_price: float, market_plan) -> list[dict[str, Any]]:
        """Fold overlapping high-quality gap watchlists into active scenario wording."""
        try:
            primary = getattr(market_plan, "primary_scenario", None)
            if not isinstance(primary, dict) or primary.get("scenario_status") == "no_active_intraday":
                return []
            direction = str(primary.get("direction") or "").upper()
            zone_low = float(primary.get("watch_low") or 0.0)
            zone_high = float(primary.get("watch_high") or 0.0)
            if not outlook or not direction or not zone_low or not zone_high:
                return []

            merge_tolerance = 5.0 * PIP_SIZE
            merged: list[dict[str, Any]] = []
            for tfl in getattr(outlook, "timeframe_levels", []) or []:
                tf_pair = f"{getattr(tfl, 'higher_tf', '')}->{getattr(tfl, 'lower_tf', '')}"
                if not self._is_active_tf_pair(tf_pair):
                    continue
                level_groups = (
                    list(getattr(tfl, "levels", []) or [])
                    + list(getattr(tfl, "recent_levels", []) or [])
                    + list(getattr(tfl, "previous_levels", []) or [])
                )
                for level in level_groups:
                    level_price = float(getattr(level, "price", 0.0) or 0.0)
                    if not level_price:
                        continue
                    candidate_direction = self._level_trade_direction(level, current_price)
                    if candidate_direction != direction:
                        continue
                    if level_price < (zone_low - merge_tolerance) or level_price > (zone_high + merge_tolerance):
                        continue
                    watch_score, watch_notes, reject_reason = self._watchlist_score(level, tf_pair, current_price)
                    if reject_reason or float(watch_score or 0.0) < 75.0:
                        continue
                    alert_key = self._watchlist_key(getattr(outlook, "pair", "XAUUSD"), level, candidate_direction, tf_pair)
                    confluences = self._watchlist_confluences(level, watch_notes)
                    if getattr(level, "level_type", "") == "Gap":
                        gap_label = "bullish gap imbalance" if direction == "BUY" else "bearish gap imbalance"
                        if gap_label not in confluences:
                            confluences.append(gap_label)
                    merged.append({
                        "setup_id": alert_key,
                        "level": level_price,
                        "direction": candidate_direction,
                        "quality_score": float(watch_score or 0.0),
                        "confluences": confluences,
                    })

            if not merged:
                return []

            merged.sort(key=lambda item: (item["quality_score"], -abs(current_price - item["level"])), reverse=True)
            old_zone = f"{zone_low:.2f}-{zone_high:.2f}"
            merged_levels = [float(item["level"]) for item in merged]
            new_low = min([zone_low, *merged_levels])
            new_high = max([zone_high, *merged_levels])
            primary["watch_low"] = round(new_low, 2)
            primary["watch_high"] = round(new_high, 2)
            primary["watch_zone"] = f"{new_low:.2f}-{new_high:.2f}"
            primary["reason"] = f"{primary.get('reason', 'Active intraday scenario')} + merged near-price gap/imbalance confluence"
            existing_confluence = list(primary.get("confluence") or [])
            for item in merged:
                for confluence in item.get("confluences") or []:
                    if confluence and confluence not in existing_confluence:
                        existing_confluence.append(confluence)
            primary["confluence"] = existing_confluence[:8]

            pipeline = dict(getattr(market_plan, "activation_pipeline", {}) or {})
            pipeline["watchlist_candidates"] = max(int(pipeline.get("watchlist_candidates", 0) or 0), len(merged))
            pipeline["merged_watchlists"] = merged
            pipeline["merged_watchlist_keys"] = [str(item.get("setup_id") or "") for item in merged]
            pipeline["reason"] = f"{pipeline.get('reason', '')}; merged overlapping near-price watchlist".strip("; ")
            market_plan.activation_pipeline = pipeline
            logger.info(
                "WATCHLIST MERGED INTO ACTIVE SCENARIO: watchlist_level=%.2f active_zone=%s",
                merged[0]["level"],
                old_zone,
            )
            return merged
        except Exception as exc:
            logger.debug("Watchlist merge into market plan skipped: %s", exc)
            return []

    def _send_scenario_update(
        self,
        payload: dict,
        now: datetime,
        event_key: str,
        *,
        change_type: str = "scenario_update",
        cooldown_minutes: int | None = None,
        dedupe_key: str | None = None,
    ) -> None:
        cooldown_minutes = int(cooldown_minutes if cooldown_minutes is not None else SCENARIO_UPDATE_ALERT_COOLDOWN_MINUTES)
        scenario_dedupe_key = dedupe_key or (
            f"{payload.get('symbol', 'XAUUSD')}|{change_type}|{payload.get('primary', '')}|"
            f"{payload.get('secondary', '')}"
        )
        last_for_type = self._last_scenario_update_by_type.get(change_type)
        if last_for_type and (now - last_for_type) < timedelta(minutes=cooldown_minutes):
            logger.info(
                "SCENARIO REFINEMENT SUPPRESSED: reason=cooldown_or_tiny_zone_change type=%s dedupe_key=%s",
                change_type,
                scenario_dedupe_key,
            )
            self._sync_dedupe_summary()
            return
        if self._last_scenario_update_at and (now - self._last_scenario_update_at) < timedelta(minutes=cooldown_minutes):
            recent_duplicate, _ = self._alert_dedupe.find_recent_matching(
                "scenario_update",
                lambda _event_key, entry: (entry.get("metadata") or {}).get("scenario_dedupe_key") == scenario_dedupe_key,
                cooldown_seconds=cooldown_minutes * 60,
            )
            if recent_duplicate:
                logger.info(
                    "SCENARIO UPDATE SUPPRESSED: reason=duplicate_or_cooldown type=%s dedupe_key=%s",
                    change_type,
                    scenario_dedupe_key,
                )
                self._sync_dedupe_summary()
                return
        payload_signature = self._event_signature(payload)
        can_send, reason = self._alert_dedupe.should_send_alert(
            "scenario_update",
            event_key,
            payload_signature,
            cooldown_seconds=cooldown_minutes * 60,
        )
        if not can_send:
            logger.info(
                "SCENARIO UPDATE SUPPRESSED: reason=%s type=%s dedupe_key=%s",
                reason,
                change_type,
                scenario_dedupe_key,
            )
            self._sync_dedupe_summary()
            return
        if self.telegram.send_scenario_update_alert(payload):
            self._last_scenario_update_at = now
            self._last_scenario_update_by_type[change_type] = now
            self._alert_dedupe.mark_alert_sent(
                "scenario_update",
                event_key,
                payload_signature,
                metadata={"scenario_dedupe_key": scenario_dedupe_key, "change_type": change_type},
            )
            logger.info("SCENARIO UPDATE SENT: type=%s event_key=%s", change_type, event_key)
            self._last_scan_summary["last_telegram_status"] = "success"
            self._last_scan_summary["last_telegram_alert_type"] = "scenario_update"
            self._last_scan_summary["last_telegram_alert_time"] = now.isoformat()
            self._save_memory()

    def _read_runtime_control(self) -> dict:
        try:
            if self._runtime_control_file.exists():
                return json.loads(self._runtime_control_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("Runtime control read failed: %s", exc)
        return {
            "status": "offline",
            "active_instance_id": None,
            "runtime_alerts_enabled": False,
            "shutdown_requested": False,
        }

    def can_send_runtime_alert(self, alert_type: str, instance_id: str | None = None) -> bool:
        runtime_control = self._read_runtime_control()
        active_instance_id = str(runtime_control.get("active_instance_id") or "")
        current_instance_id = str(instance_id or self._instance_id)
        if not self._running:
            logger.info("SYSTEM ALERT SKIPPED: bot_not_running alert_type=%s", alert_type)
            return False
        if runtime_control.get("shutdown_requested"):
            logger.info("SYSTEM ALERT SKIPPED: bot_not_running alert_type=%s", alert_type)
            return False
        if self._runtime_alerts_disabled_flag.exists():
            logger.info("SYSTEM ALERT SKIPPED: runtime_alerts_disabled_flag alert_type=%s", alert_type)
            return False
        if not TELEGRAM_RUNTIME_ALERTS_ENABLED or not runtime_control.get("runtime_alerts_enabled", False):
            logger.info("SYSTEM ALERT SKIPPED: runtime_alerts_disabled alert_type=%s", alert_type)
            return False
        if active_instance_id and active_instance_id != current_instance_id:
            logger.info("ALERT SKIPPED: stale instance old=%s active=%s", current_instance_id, active_instance_id)
            return False
        if runtime_control.get("status") not in {"starting", "running", "watching", "analyzing"}:
            logger.info("SYSTEM ALERT SKIPPED: bot_not_running alert_type=%s", alert_type)
            return False
        return True

    def _send_runtime_system_alert(
        self,
        message: str,
        *,
        alert_type: str,
        source_module: str,
        source_function: str,
        cooldown_minutes: int,
    ) -> bool:
        runtime_control_snapshot = self._read_runtime_control()
        logger.info(
            "MT5 NO DATA ALERT SOURCE TRACE: module=%s function=%s instance_id=%s "
            "state_status=%s runtime_alerts_enabled=%s process_id=%d thread=%s bot_running=%s",
            source_module,
            source_function,
            self._instance_id,
            runtime_control_snapshot.get("status", "unknown"),
            str(runtime_control_snapshot.get("runtime_alerts_enabled", False)),
            os.getpid(),
            threading.current_thread().name,
            str(self._running).lower(),
        )
        if not self.can_send_runtime_alert(alert_type, self._instance_id):
            return False
        payload = {"message": message, "alert_type": alert_type}
        event_key = f"system_alert:{alert_type}:{self._instance_id}"
        payload_signature = self._event_signature(payload)
        can_send, reason = self._alert_dedupe.should_send_alert(
            "system_alert",
            event_key,
            payload_signature,
            cooldown_seconds=cooldown_minutes * 60,
        )
        if not can_send:
            if alert_type == "mt5_no_data" and reason == "cooldown_active":
                logger.info("MT5 NO DATA ALERT SKIPPED: cooldown active")
            else:
                logger.info("SYSTEM ALERT SKIPPED: %s alert_type=%s", reason, alert_type)
            self._sync_dedupe_summary()
            return False
        if self.telegram.send_system_alert(message):
            self._alert_dedupe.mark_alert_sent("system_alert", event_key, payload_signature)
            self._save_memory()
            return True
        return False

    def _send_resume_watch_alerts(self, plan_dict: dict, primary: dict, secondary: dict, now: datetime) -> None:
        if self._resume_watch_checked or not SPENCER_RESUME_WATCH_ALERT_ENABLED:
            return

        primary_dir = str(primary.get("direction") or "").upper()
        secondary_dir = str(secondary.get("direction") or "").upper()
        primary_zone = str(primary.get("watch_zone") or "").strip()
        secondary_zone = str(secondary.get("watch_zone") or "").strip()

        active_zones: list[tuple[dict, dict, str]] = []
        for zone in plan_dict.get("active_watch_zones", []) or []:
            zone_id = str(zone.get("zone_id"))
            previous = self._watch_zone_state.get(zone_id) or {}
            previous_status = str(previous.get("status") or "").lower()
            current_status = str(zone.get("status") or "").lower()
            if previous_status not in {"fresh", "active"} or current_status not in {"fresh", "active"}:
                continue
            if previous.get("direction") != zone.get("direction"):
                continue
            active_zones.append((zone, previous, current_status))

        if not active_zones:
            self._resume_watch_checked = True
            return

        # Build a single combined message that always leads with the primary
        # scenario context, then mentions the secondary as reclaim-watch only.
        primary_zone_label = primary_zone or "n/a"
        secondary_zone_label = secondary_zone or "n/a"
        message_lines: list[str] = []
        if primary_dir and primary_zone_label != "n/a":
            message_lines.append(f"Still watching primary {primary_dir} {primary_zone_label}.")
        if secondary_dir and secondary_zone_label and secondary_zone_label != "n/a":
            message_lines.append(
                f"Secondary {secondary_dir} {secondary_zone_label} remains reclaim-watch only."
            )
        if not message_lines:
            # Fallback: surface whichever resumed zone we have without mislabelling.
            zone, _prev, _status = active_zones[0]
            zone_label = f"{zone.get('level_low', 0.0):.2f}-{zone.get('level_high', 0.0):.2f}"
            message_lines.append(f"Still watching {zone.get('direction', '?')} {zone_label}.")
        message_lines.append(
            "Scenario carried over from the last run and remains valid while fresh confirmation is pending."
        )

        zone_label_for_key = (
            primary_zone_label
            if primary_zone_label != "n/a"
            else f"{active_zones[0][0].get('level_low', 0.0):.2f}-{active_zones[0][0].get('level_high', 0.0):.2f}"
        )
        self._send_scenario_update(
            {
                "symbol": plan_dict.get("symbol", "XAUUSD"),
                "title": f"SPENCER SCENARIO STILL WATCHING - {plan_dict.get('symbol', 'XAUUSD')}",
                "change_type": "resume_watch",
                "message": "\n".join(message_lines),
                "primary": f"{primary_dir} {primary_zone_label}".strip(),
                "secondary": f"{secondary_dir} {secondary_zone_label}".strip(),
                "waiting_for": " / ".join(plan_dict.get("confirmation_waiting_for", [])[:4]),
            },
            now,
            event_key=self._build_scenario_update_event_key(
                symbol=plan_dict.get("symbol", "XAUUSD"),
                event_type="resume_watch",
                new_scenario_key=f"{primary_dir}:{primary_zone_label}",
                level=zone_label_for_key,
                status="active",
            ),
            change_type="resume_watch",
        )
        self._resume_watch_checked = True

    @staticmethod
    def _market_plan_signature(plan: dict) -> str:
        raw = json.dumps(
            {
                "bias": plan.get("dominant_bias"),
                "primary": plan.get("primary_scenario"),
                "secondary": plan.get("secondary_scenario"),
                "supports": plan.get("key_supports", [])[:4],
                "resistances": plan.get("key_resistances", [])[:4],
            },
            sort_keys=True,
            default=str,
        )
        return sha1(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _event_signature(payload: dict) -> str:
        return sha1(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    @staticmethod
    def _split_zone_bounds(raw_zone: str) -> tuple[float, float]:
        try:
            cleaned = str(raw_zone).replace(" ", "")
            if "-" not in cleaned:
                value = float(cleaned)
                return value, value
            low, high = cleaned.split("-", 1)
            return float(low), float(high)
        except Exception:
            return 0.0, 0.0

    @staticmethod
    def _parse_numeric_level(raw_value: Any) -> float:
        try:
            match = str(raw_value or "").replace(",", "")
            found = "".join(ch for ch in match if ch.isdigit() or ch in ".-")
            return float(found) if found else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _scenario_zone_bounds(scenario: dict) -> tuple[str, str]:
        zone = str(scenario.get("watch_zone") or "").replace(" ", "")
        if "-" in zone:
            low, high = zone.split("-", 1)
            return low, high
        level = zone or "n/a"
        return level, level

    def _build_market_plan_event_key(self, plan: dict) -> str:
        primary = plan.get("primary_scenario", {}) or {}
        secondary = plan.get("secondary_scenario", {}) or {}
        p_low, p_high = self._scenario_zone_bounds(primary)
        s_low, s_high = self._scenario_zone_bounds(secondary)
        return (
            f"market_plan:{plan.get('symbol', 'XAUUSD')}:{plan.get('dominant_bias', 'neutral')}:"
            f"{primary.get('direction', '?')}:{p_low}:{p_high}:"
            f"{secondary.get('direction', '?')}:{s_low}:{s_high}"
        )

    def _build_scenario_update_event_key(
        self,
        *,
        symbol: str,
        event_type: str,
        old_scenario_key: str = "",
        new_scenario_key: str = "",
        level: str = "",
        status: str = "",
    ) -> str:
        return f"scenario_update:{symbol}:{event_type}:{old_scenario_key}:{new_scenario_key}:{level}:{status}"

    def _build_entry_event_key(self, confirmation, scenario_key: str) -> str:
        zone_level = f"{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}"
        candle_time = str(confirmation.candle_time)
        return (
            f"entry:XAUUSD:{confirmation.direction}:{scenario_key}:{confirmation.confirmation_type}:"
            f"{zone_level}:{candle_time}"
        )

    def _build_trade_idea_dedupe_key(self, confirmation) -> str:
        bucket = max(PIP_SIZE * 2.0, 0.01)
        def _bucket(value: float) -> float:
            return round(round(float(value) / bucket) * bucket, 2)

        tps = getattr(confirmation, "suggested_tps", {}) or {}
        return (
            f"trade_idea:XAUUSD:{confirmation.direction}:"
            f"{getattr(confirmation, 'scenario', 'primary')}:"
            f"{float(getattr(confirmation, 'zone_low', 0.0)):.2f}-"
            f"{float(getattr(confirmation, 'zone_high', 0.0)):.2f}:"
            f"entry={_bucket(float(getattr(confirmation, 'suggested_entry', 0.0)))}:"
            f"sl={_bucket(float(getattr(confirmation, 'suggested_sl', 0.0)))}:"
            f"tp1={round(float(tps.get('tp1', 0.0) or 0.0), 2)}:"
            f"tp2={round(float(tps.get('tp2', 0.0) or 0.0), 2)}:"
            f"tp3={round(float(tps.get('tp3', 0.0) or 0.0), 2)}"
        )

    @staticmethod
    def _same_trade_idea_metadata(metadata: dict, confirmation) -> bool:
        try:
            tps = getattr(confirmation, "suggested_tps", {}) or {}
            return (
                metadata.get("symbol") == "XAUUSD"
                and metadata.get("direction") == confirmation.direction
                and str(metadata.get("scenario_type", "primary")) == str(getattr(confirmation, "scenario", "primary"))
                and abs(float(metadata.get("watch_zone_low", 0.0)) - float(confirmation.zone_low)) <= 0.01
                and abs(float(metadata.get("watch_zone_high", 0.0)) - float(confirmation.zone_high)) <= 0.01
                and abs(float(metadata.get("entry", 0.0)) - float(confirmation.suggested_entry)) <= (2 * PIP_SIZE)
                and abs(float(metadata.get("sl", 0.0)) - float(confirmation.suggested_sl)) <= (2 * PIP_SIZE)
                and round(float(metadata.get("tp1", 0.0)), 2) == round(float(tps.get("tp1", 0.0) or 0.0), 2)
                and round(float(metadata.get("tp2", 0.0)), 2) == round(float(tps.get("tp2", 0.0) or 0.0), 2)
                and round(float(metadata.get("tp3", 0.0)), 2) == round(float(tps.get("tp3", 0.0) or 0.0), 2)
            )
        except Exception:
            return False

    @staticmethod
    def _build_watch_zone_key(symbol: str, direction: str, zone_low: float, zone_high: float, status: str) -> str:
        return f"watch_zone:{symbol}:{direction}:{zone_low:.2f}:{zone_high:.2f}:{status}"

    @staticmethod
    def _build_gap_watchlist_key(symbol: str, direction: str, level_price: float, timeframe_pair: str) -> str:
        return f"gap_watchlist:{symbol}:{direction}:{level_price:.2f}:{timeframe_pair}"

    def _load_memory(self) -> None:
        try:
            if not self._memory_file.exists():
                self._save_memory()
                return
            payload = json.loads(self._memory_file.read_text(encoding="utf-8"))
            self._last_market_plan_hash = payload.get("last_market_plan_hash")
            self._last_session_market_plan = payload.get("last_session_market_plan")
            self._last_primary_scenario_key = payload.get("last_primary_scenario_key")
            self._watch_zone_state = {
                str(key): value for key, value in (payload.get("watch_zone_state") or {}).items()
            }
            self.trade_management_engine.load_states(
                payload.get("active_trade_setups") or {},
                payload.get("trade_management_sent_alert_keys") or [],
            )
            self._last_scan_summary["five_layer_status"]["risk_management"] = {
                **self.trade_management_engine.tracking_summary(),
                "active_trades": len(self.trade_management_engine._active_setups),
            }
            dedupe_payload = payload.get("alert_dedupe")
            if dedupe_payload:
                self._alert_dedupe.load_payload(dedupe_payload)
            else:
                now_iso = datetime.now(timezone.utc).isoformat()
                legacy_sent = {
                    "scenario_update": {
                        key: {"signature": key, "sent_at": value or now_iso, "metadata": {}}
                        for key, value in self._normalize_memory_key_map(payload.get("analyst_scenario_update_keys")).items()
                    },
                    "analyst_entry": {
                        key: {"signature": key, "sent_at": value or now_iso, "metadata": {}}
                        for key, value in self._normalize_memory_key_map(payload.get("analyst_alerted_confirmations")).items()
                    },
                    "gap_watchlist": {
                        key: {"signature": key, "sent_at": now_iso, "metadata": {}}
                        for key in (payload.get("watchlist_keys") or [])
                    },
                }
                self._alert_dedupe.load_payload({"sent": legacy_sent})
            self._prune_memory()
            self._sync_dedupe_summary()
            logger.info(
                "SPENCER MEMORY LOADED: market_plans=%d scenario_updates=%d entries=%d watchlists=%d watch_zones=%d",
                len(self._alert_dedupe.sent.get("market_plan", {})),
                len(self._alert_dedupe.sent.get("scenario_update", {})),
                len(self._alert_dedupe.sent.get("analyst_entry", {})),
                len(self._alert_dedupe.sent.get("gap_watchlist", {})),
                len(self._watch_zone_state),
            )
        except Exception as exc:
            try:
                broken = self._memory_file.with_suffix(".broken.json")
                if self._memory_file.exists():
                    self._memory_file.replace(broken)
            except Exception:
                pass
            logger.debug("Spencer memory load failed: %s", exc)
            self._save_memory()

    def _save_memory(self) -> None:
        try:
            self._prune_memory()
            watch_zone_items = list((self._watch_zone_state or {}).items())[-100:]
            payload = {
                "last_market_plan_hash": self._last_market_plan_hash,
                "last_session_market_plan": self._last_session_market_plan,
                "last_primary_scenario_key": self._last_primary_scenario_key,
                "watch_zone_state": dict(watch_zone_items),
                "alert_dedupe": self._alert_dedupe.to_payload(),
                "market_plan_signatures": self._alert_dedupe.sent.get("market_plan", {}),
                "scenario_keys": self._alert_dedupe.sent.get("scenario_update", {}),
                "watch_zone_states": dict(watch_zone_items),
                "confirmation_keys": self._alert_dedupe.sent.get("analyst_entry", {}),
                "decision_keys": self._alert_dedupe.sent.get("decision_engine", {}),
                "active_trade_setups": {
                    key: value.to_dict() for key, value in self.trade_management_engine._active_setups.items()
                },
                "trade_management_keys": self._alert_dedupe.sent.get("trade_management", {}),
                "trade_management_sent_alert_keys": self.trade_management_engine.sent_alert_keys(),
                "trade_tracking": self.trade_management_engine.tracking_summary(),
                "scenario_outcomes": {},
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self._memory_file.write_text(json.dumps(payload), encoding="utf-8")
            logger.info(
                "SPENCER MEMORY SAVED: market_plans=%d scenario_updates=%d entries=%d watchlists=%d",
                len(self._alert_dedupe.sent.get("market_plan", {})),
                len(self._alert_dedupe.sent.get("scenario_update", {})),
                len(self._alert_dedupe.sent.get("analyst_entry", {})),
                len(self._alert_dedupe.sent.get("gap_watchlist", {})),
            )
        except Exception as exc:
            logger.debug("Spencer memory save failed: %s", exc)

    @staticmethod
    def _normalize_memory_key_map(raw) -> Dict[str, str]:
        now_iso = datetime.now(timezone.utc).isoformat()
        if isinstance(raw, dict):
            return {str(key): str(value) for key, value in raw.items()}
        if isinstance(raw, list):
            return {str(item): now_iso for item in raw}
        return {}

    @staticmethod
    def _parse_iso_timestamp(raw: str | None) -> Optional[datetime]:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None

    def _prune_memory(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=SPENCER_MEMORY_RETENTION_HOURS)
        removed = self._alert_dedupe.cleanup()
        pruned_watch_zones: Dict[str, dict] = {}
        for key, value in (self._watch_zone_state or {}).items():
            persisted_at = self._parse_iso_timestamp((value or {}).get("persisted_at"))
            if persisted_at is None or persisted_at >= cutoff:
                pruned_watch_zones[str(key)] = value
        self._watch_zone_state = pruned_watch_zones
        if removed:
            logger.info("SPENCER MEMORY CLEANUP: removed=%d expired_keys", removed)

    def _sync_dedupe_summary(self) -> None:
        skipped = self._alert_dedupe.skipped
        self._last_scan_summary["alert_dedupe"] = {
            "market_plan_skipped_duplicate": int(skipped.get("market_plan_unchanged_signature", 0) or 0),
            "scenario_update_skipped_duplicate": int(skipped.get("scenario_update_unchanged_signature", 0) or 0),
            "entry_skipped_duplicate": int(skipped.get("analyst_entry_unchanged_signature", 0) or 0) + int(skipped.get("analyst_entry_cooldown_active", 0) or 0),
            "watchlist_skipped_duplicate": int(skipped.get("gap_watchlist_unchanged_signature", 0) or 0) + int(skipped.get("gap_watchlist_cooldown_active", 0) or 0),
            "last_skip_reason": self._alert_dedupe.last_skip_reason,
        }

    @staticmethod
    def _classify_analyst_setup(confirmation, active_signals: list) -> dict:
        best = None
        best_distance = 999999.0
        for sig in active_signals or []:
            if getattr(sig, "direction", "").upper() != confirmation.direction.upper():
                continue
            setup = getattr(sig, "setup", None)
            if setup is None:
                continue
            sig_level = float(getattr(setup.level, "price", 0.0) or 0.0)
            distance = min(
                abs(sig_level - confirmation.zone_low),
                abs(sig_level - confirmation.zone_high),
                abs(sig_level - confirmation.suggested_entry),
            )
            if distance < best_distance:
                best_distance = distance
                best = sig

        if best and best_distance <= LEVEL_CROWDING_PIPS:
            setup = getattr(best, "setup", None)
            strategy_name = getattr(best, "strategy_name", "analyst_layer")
            logger.info(
                "STRATEGY CLASSIFIED SETUP: strategy=%s analyst_scenario=%s_%s",
                strategy_name,
                confirmation.direction,
                f"{confirmation.zone_low:.2f}_{confirmation.zone_high:.2f}",
            )
            return {
                "strategy_type": strategy_name,
                "strategy_suggested_entry": float(getattr(getattr(setup, "confirmation", None), "entry_price", confirmation.suggested_entry) or confirmation.suggested_entry),
                "strategy_suggested_sl": float(getattr(getattr(setup, "confirmation", None), "sl_price", confirmation.suggested_sl) or confirmation.suggested_sl),
                "strategy_suggested_tp1": float(getattr(getattr(setup, "confirmation", None), "tp1", confirmation.suggested_tps.get("tp1", 0.0)) or confirmation.suggested_tps.get("tp1", 0.0)),
                "strategy_suggested_tp2": float(getattr(getattr(setup, "confirmation", None), "tp2", confirmation.suggested_tps.get("tp2", 0.0)) or confirmation.suggested_tps.get("tp2", 0.0)),
                "strategy_suggested_tp3": float(getattr(getattr(setup, "confirmation", None), "tp3", confirmation.suggested_tps.get("tp3", 0.0)) or confirmation.suggested_tps.get("tp3", 0.0)),
            }
        return {"strategy_type": "analyst_layer"}

    def _write_heartbeat(self, in_silent_phase: bool, signals: list, ctx, current_price: Optional[float], tick: Optional[dict]) -> None:
        """Write bot_heartbeat.json so the API can surface analyzing/watching status."""
        try:
            runtime_control = self._read_runtime_control()
            status = "analyzing" if in_silent_phase else "watching"
            message = "Spencer is analyzing charts" if in_silent_phase else "Spencer is watching the market"
            session = getattr(ctx, "session_name", None) if ctx else None
            ss = self._last_scan_summary
            alerts_sent = ss.get("last_alerts_sent", 0)
            reject_reason = ss.get("last_reject_reason", "")
            scan_result = (
                f"{alerts_sent} watchlist alert(s) sent"
                if alerts_sent > 0
                else f"all current setups already alerted" if reject_reason in {"all_alerted", "duplicate"}
                else f"no setups — {reject_reason}" if reject_reason else "watching for setups"
            )
            telegram_block = {
                "last_status": ss.get("last_telegram_status", "none"),
                "last_alert_type": ss.get("last_telegram_alert_type") or None,
                "last_alert_time": ss.get("last_telegram_alert_time") or None,
                "last_error": ss.get("last_telegram_error") or None,
            }
            scan_summary = {
                "levels_detected": ss.get("levels_detected", 0),
                "gap_levels": ss.get("gap_levels", 0),
                "bias_passed": ss.get("bias_passed", 0),
                "sweep_confirmed": ss.get("sweep_confirmed", 0),
                "session_passed": ss.get("session_passed", 0),
                "distance_passed": ss.get("distance_passed", 0),
                "watchlist_candidates": ss.get("watchlist_candidates", 0),
                "alerts_sent": ss.get("last_alerts_sent", 0),
                "alerts_failed": ss.get("last_alerts_failed", 0),
                "reject_reasons": {
                    ss.get("last_reject_reason", "unknown") or "unknown": 1,
                    "duplicate": ss.get("dedupe_rejections", 0),
                },
            }
            ai_prediction = ss.get("five_layer_status", {}).get("pytorch_ai") or {}
            ai_predictive_layer = {
                "model_enabled": bool(ai_prediction.get("model_enabled", PYTORCH_AI_ENABLED)),
                "model_type": ai_prediction.get("model_type", PYTORCH_AI_MODEL_TYPE),
                "model_version": ai_prediction.get("model_version", PYTORCH_AI_MODEL_VERSION),
                "model_path": ai_prediction.get("model_path", PYTORCH_AI_MODEL_PATH),
                "schema_path": ai_prediction.get("schema_path", PYTORCH_AI_SCHEMA_PATH),
                "ai_recommendation": ai_prediction.get("ai_recommendation", "allow"),
                "ai_label": ai_prediction.get("ai_label", "AI-ALLOWED SETUP"),
                "tp1_probability": ai_prediction.get("tp1_probability"),
                "sl_probability": ai_prediction.get("sl_probability"),
                "expected_pips": ai_prediction.get("expected_pips"),
                "model_confidence": ai_prediction.get("model_confidence"),
                "ai_mode": "blocking" if PYTORCH_AI_BLOCKING_MODE else "advisory",
                "advisory_or_blocking": "blocking" if PYTORCH_AI_BLOCKING_MODE else "advisory",
                "would_block": bool(ai_prediction.get("would_block", False)),
                "feature_health": {
                    "missing_features": ai_prediction.get("missing_features_count", 0),
                    "unknown_categories": ai_prediction.get("unknown_categories_count", 0),
                    "schema_match": ai_prediction.get("schema_match", True),
                },
                "last_prediction_timestamp": ai_prediction.get("last_prediction_at"),
            }
            plan_for_feed = self._market_plan or {}
            primary_feed = (plan_for_feed.get("primary_scenario") or {}) if isinstance(plan_for_feed, dict) else {}
            secondary_feed = (plan_for_feed.get("secondary_scenario") or {}) if isinstance(plan_for_feed, dict) else {}
            active_trade_feed = next(iter(self.trade_management_engine._active_setups.values()), None)
            active_trade_dict = active_trade_feed.to_dict() if active_trade_feed else {}
            primary_level = primary_feed.get("watch_low") or primary_feed.get("watch_high")
            price_feed = {
                "currentPrice": current_price,
                "latestCandles": [],
                "timeframe": "M15",
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
                "activeLevel": primary_level,
                "primaryZone": primary_feed.get("watch_zone"),
                "alternativeZone": secondary_feed.get("watch_zone"),
                "sl": active_trade_dict.get("sl") or active_trade_dict.get("virtual_sl") or primary_feed.get("invalidation_level"),
                "tp1": active_trade_dict.get("tp1") or ((primary_feed.get("market_plan_targets") or [None])[0] if primary_feed.get("market_plan_targets") else None),
                "tp2": active_trade_dict.get("tp2") or ((primary_feed.get("market_plan_targets") or [None, None])[1] if len(primary_feed.get("market_plan_targets") or []) > 1 else None),
                "tp3": active_trade_dict.get("tp3") or ((primary_feed.get("market_plan_targets") or [None, None, None])[2] if len(primary_feed.get("market_plan_targets") or []) > 2 else None),
            }
            data = {
                "status":               status,
                "message":              message,
                "timestamp":            datetime.now(timezone.utc).isoformat(),
                "last_scan_at":         datetime.now(timezone.utc).isoformat(),
                "last_scan_result":     scan_result,
                "current_session":      session,
                "last_scan_symbol":     "XAUUSD",
                "last_candidates_count": ss.get("last_candidates_count", 0),
                "last_alerts_sent":     ss.get("last_alerts_sent", 0),
                "last_alerts_failed":   ss.get("last_alerts_failed", 0),
                "last_reject_reason":   ss.get("last_reject_reason", ""),
                "last_telegram_status": ss.get("last_telegram_status", "none"),
                "last_telegram_error":  ss.get("last_telegram_error", ""),
                "last_telegram_alert_type": ss.get("last_telegram_alert_type", ""),
                "last_telegram_alert_time": ss.get("last_telegram_alert_time", ""),
                "last_scan_number":     ss.get("last_scan_number", self._scan_count),
                "session_blocking":     ss.get("session_blocking", False),
                "instance_id":          self._instance_id,
                "process_id":           os.getpid(),
                "current_symbol":       "XAUUSD",
                "current_price":        current_price,
                "bid":                  tick.get("bid") if tick else current_price,
                "ask":                  tick.get("ask") if tick else current_price,
                "spread":               tick.get("spread") if tick else None,
                "spread_pips":          tick.get("spread_pips") if tick else None,
                "d1_bias":              getattr(ctx, "d1_bias", "neutral") if ctx else "neutral",
                "h4_bias":              getattr(ctx, "h4_bias", "neutral") if ctx else "neutral",
                "h1_bias":              getattr(ctx, "h1_bias", "neutral") if ctx else "neutral",
                "dominant_bias":        getattr(ctx, "dominant_bias", "neutral") if ctx else "neutral",
                "bias_strength":        getattr(ctx, "bias_strength", "weak") if ctx else "weak",
                "bot_window_active":    True,
                "session_name":         getattr(ctx, "session_name", None) if ctx else None,
                "local_time":           getattr(ctx, "local_time", "") if ctx else "",
                "active_until":         "24/7",
                "operating_mode":       "24_7",
                "last_market_update_at": datetime.now(timezone.utc).isoformat(),
                "live_enabled_strategies": list(LIVE_ENABLED_STRATEGIES),
                "research_only_strategies": list(RESEARCH_ONLY_STRATEGIES),
                "market_session":       session,
                "scan_allowed":         True,
                "last_scan_summary":    scan_summary,
                "instance_totals":      ss.get("instance_totals", {}),
                "strategy_scans":       ss.get("strategy_scans", {}),
                "market_plan":          self._market_plan,
                "alert_dedupe":         ss.get("alert_dedupe", {}),
                "five_layer_status":    ss.get("five_layer_status", {}),
                "active_trade_state":   {
                    key: value.to_dict() for key, value in self.trade_management_engine._active_setups.items()
                },
                "trade_tracking":        self.trade_management_engine.tracking_summary(),
                "ai_prediction":         ai_prediction,
                "ai_predictive_layer":   ai_predictive_layer,
                "priceFeed":             price_feed,
                "active_instance_id":   runtime_control.get("active_instance_id"),
                "background_tasks_active": sum(1 for active in self._background_tasks.values() if active),
                "runtime_alerts_enabled": bool(
                    TELEGRAM_RUNTIME_ALERTS_ENABLED
                    and runtime_control.get("runtime_alerts_enabled", False)
                    and not self._runtime_alerts_disabled_flag.exists()
                ),
                "shutdown_event_set":   bool(runtime_control.get("shutdown_requested", False)),
                "telegram":             telegram_block,
            }
            self._heartbeat_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception as exc:
            logger.debug("Heartbeat write failed: %s", exc)

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
        _session_name = getattr(ctx, "session_name", "unknown") if ctx else "unknown"

        runtime_logger.info(
            "WATCHLIST LOOP STARTED: symbol=%s | session=%s | timeframe_groups=%d",
            outlook.pair, _session_name, len(outlook.timeframe_levels),
        )

        # Diagnostic counters — tracking only, no strategy logic change
        _levels_scanned = 0
        _gap_levels = 0
        _already_alerted = 0
        _already_confirmed = 0
        _rejected_count = 0
        _primary_reject_reason = ""

        candidates = []
        for tfl in outlook.timeframe_levels:
            tf_pair = f"{tfl.higher_tf}->{tfl.lower_tf}"
            if not self._is_active_tf_pair(tf_pair):
                logger.info("WATCHLIST SKIPPED: disabled timeframe pair %s", tf_pair)
                continue
            horizon = self._watchlist_horizon(tf_pair)
            for level in tfl.levels + tfl.recent_levels + tfl.previous_levels:
                _levels_scanned += 1
                if getattr(level, "level_type", "") == "Gap":
                    _gap_levels += 1

                direction = self._level_trade_direction(level, current_price)
                alert_key = self._watchlist_key(outlook.pair, level, direction, tf_pair)
                level_id = self._watch_key(outlook.pair, level.price)
                already_alerted, _ = self._alert_dedupe.find_recent_matching(
                    "gap_watchlist",
                    lambda event_key, _entry: event_key == alert_key,
                    cooldown_seconds=24 * 60 * 60,
                )
                runtime_logger.info(
                    "WATCHLIST DEDUPE CHECK: setup_key=%s | already_alerted=%s",
                    alert_key,
                    str(already_alerted).lower(),
                )

                if already_alerted:
                    _already_alerted += 1
                    runtime_logger.info(
                        "WATCHLIST ALERT SKIPPED: duplicate | setup_key=%s", alert_key
                    )
                    continue
                if level_id in self._confirmed_levels:
                    _already_confirmed += 1
                    continue

                watch_score, watch_notes, reject_reason = self._watchlist_score(
                    level=level,
                    tf_pair=tf_pair,
                    current_price=current_price,
                )
                base_score = self._level_selection_score(level)
                distance_pips = abs(current_price - level.price) / PIP_SIZE

                if reject_reason:
                    _rejected_count += 1
                    if "too far" in reject_reason.lower():
                        _primary_reject_reason = "distance_rejected"
                    elif not _primary_reject_reason:
                        _primary_reject_reason = "score_too_low"
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
            if _levels_scanned == 0 or _gap_levels == 0:
                _no_reason = "no_gap_level"
            elif (_already_alerted + _already_confirmed) >= _levels_scanned and _rejected_count == 0:
                _no_reason = "all_alerted"
            elif _primary_reject_reason:
                _no_reason = _primary_reject_reason
            else:
                _no_reason = "unknown"
            runtime_logger.info(
                "NO WATCHLIST SETUPS FOUND: symbol=%s | reason=%s | levels=%d | gap=%d"
                " | alerted=%d | confirmed=%d | rejected=%d",
                outlook.pair, _no_reason,
                _levels_scanned, _gap_levels,
                _already_alerted, _already_confirmed, _rejected_count,
            )
            self._last_scan_summary.update({
                "last_candidates_count": 0,
                "last_alerts_sent": 0,
                "last_alerts_failed": 0,
                "last_reject_reason": _no_reason,
                "last_scan_number": self._scan_count,
                "session_blocking": False,
                "levels_detected": _levels_scanned,
                "gap_levels": _gap_levels,
                "bias_passed": 0,
                "sweep_confirmed": 0,
                "session_passed": 0 if _no_reason == "session_blocked" else 1,
                "distance_passed": 0,
                "watchlist_candidates": 0,
                "dedupe_rejections": _already_alerted,
            })
            self._last_scan_summary["instance_totals"]["duplicates_blocked"] += _already_alerted
            runtime_logger.info(
                "SCAN SUMMARY: %s",
                json.dumps({
                    "symbol": outlook.pair,
                    "levels_detected": _levels_scanned,
                    "gap_levels": _gap_levels,
                    "bias_passed": 0,
                    "sweep_confirmed": 0,
                    "session_passed": 0 if _no_reason == "session_blocked" else 1,
                    "distance_passed": 0,
                    "watchlist_candidates": 0,
                    "alerts_sent": 0,
                    "alerts_failed": 0,
                    "reject_reasons": {
                        "no_gap_level": 1 if _no_reason == "no_gap_level" else 0,
                        "session_blocked": 1 if _no_reason == "session_blocked" else 0,
                        "distance_rejected": 1 if _no_reason == "distance_rejected" else 0,
                        "approach_filter_failed": 1 if _no_reason == "score_too_low" else 0,
                        "unknown": 1 if _no_reason not in {"no_gap_level", "session_blocked", "distance_rejected", "score_too_low"} else 0,
                    },
                }),
            )
            runtime_logger.info(
                "DASHBOARD STATUS UPDATED: alerts_sent_this_scan=%d total_alerts=%d telegram=%s",
                0,
                self._last_scan_summary["instance_totals"]["alerts_sent"],
                self._last_scan_summary.get("last_telegram_status", "none"),
            )
            return 0

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

        merged_keys = set(
            str(key) for key in (
                (self._last_scan_summary.get("five_layer_status", {}) or {})
                .get("activation_pipeline", {})
                .get("merged_watchlist_keys", [])
            )
        )
        if not merged_keys:
            merged_keys = set(
                str(key) for key in (
                    (self._last_scan_summary.get("activation_pipeline", {}) or {})
                    .get("merged_watchlist_keys", [])
                )
            )
        if merged_keys:
            unmerged_ranked = []
            for candidate in ranked:
                if str(candidate.get("alert_key") or "") in merged_keys:
                    logger.info(
                        "WATCHLIST ALERT SUPPRESSED: reason=merged_into_active_scenario setup_key=%s",
                        candidate.get("alert_key"),
                    )
                    continue
                unmerged_ranked.append(candidate)
            ranked = unmerged_ranked

        promoted = [
            candidate for candidate in ranked
            if float(candidate.get("distance_pips", 999.0)) <= 80.0
            and float(candidate.get("score", 0.0)) >= 75.0
        ]
        if promoted:
            best = promoted[0]
            logger.info(
                "WATCHLIST PROMOTED TO ACTIVE SCENARIO: setup_id=%s level=%.2f reason=near_price_high_quality_bias_aligned",
                best.get("alert_key"),
                float(getattr(best.get("level"), "price", 0.0) or 0.0),
            )
            self._last_scan_summary["activation_pipeline"] = {
                **dict(self._last_scan_summary.get("activation_pipeline") or {}),
                "watchlist_candidates": len(ranked),
                "promoted_watchlist_scenario": {
                    "setup_id": best.get("alert_key"),
                    "direction": best.get("direction"),
                    "level": float(getattr(best.get("level"), "price", 0.0) or 0.0),
                    "distance_pips": float(best.get("distance_pips", 0.0) or 0.0),
                    "quality_score": float(best.get("score", 0.0) or 0.0),
                    "scenario_type": "active_liquidity_sweep_reversal" if "sweep" in " ".join(best.get("watch_notes", [])).lower() else "active_continuation_retest",
                    "reason": "near_price_high_quality_bias_aligned",
                },
            }

        sent_count = 0
        failed_count = 0
        horizon_counts: Dict[str, int] = {}
        bias = self._watchlist_bias(outlook)

        if ranked:
            runtime_logger.info(
                "WATCHLIST ALERT CALLING TELEGRAM: candidates=%d | symbol=%s",
                len(ranked), outlook.pair,
            )

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
                self._alert_dedupe.mark_alert_sent(
                    "gap_watchlist",
                    candidate["alert_key"],
                    candidate["alert_key"],
                    metadata={"symbol": outlook.pair, "direction": candidate["direction"], "timeframe_pair": candidate["tf_pair"]},
                )
                logger.info("WATCHLIST MEMORY SAVED: setup_key=%s", candidate["alert_key"])
                self._save_memory()
                self._persist_gap_watchlist_candidate(outlook, candidate, bias)
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
                runtime_logger.info(
                    "WATCHLIST ALERT SENT: %s %s %.2f | %s",
                    outlook.pair, candidate["direction"], level.price, candidate["tf_pair"],
                )
            else:
                failed_count += 1
                runtime_logger.info(
                    "WATCHLIST ALERT FAILED: telegram returned False for %s %s %.2f",
                    outlook.pair, candidate["direction"], level.price,
                )

        if sent_count:
            logger.info(
                "WATCHLIST ALERT SENT: count=%d shortlisted level(s).",
                sent_count,
            )
            runtime_logger.info("WATCHLIST ALERT SENT: count=%d total", sent_count)
        if failed_count:
            runtime_logger.info(
                "WATCHLIST ALERT FAILED: count=%d total alerts failed", failed_count
            )

        _tg_status = "success" if sent_count > 0 else ("failed" if failed_count > 0 else "none")
        _tg_error = "telegram returned False" if failed_count > 0 and sent_count == 0 else ""
        self._last_scan_summary.update({
            "last_candidates_count": len(ranked),
            "last_alerts_sent":      sent_count,
            "last_alerts_failed":    failed_count,
            "last_reject_reason":    "" if sent_count > 0 else ("telegram_failed" if failed_count > 0 else ""),
            "last_telegram_status":  _tg_status,
            "last_telegram_error":   _tg_error,
            "last_telegram_alert_type": "watchlist" if (sent_count or failed_count) else self._last_scan_summary.get("last_telegram_alert_type", ""),
            "last_telegram_alert_time": datetime.now(timezone.utc).isoformat() if (sent_count or failed_count) else self._last_scan_summary.get("last_telegram_alert_time", ""),
            "last_scan_number":      self._scan_count,
            "session_blocking":      False,
            "levels_detected":       _levels_scanned,
            "gap_levels":            _gap_levels,
            "bias_passed":           len(candidates),
            "sweep_confirmed":       0,
            "session_passed":        1,
            "distance_passed":       len(ranked),
            "watchlist_candidates":  len(ranked),
            "dedupe_rejections":     _already_alerted,
        })
        self._last_scan_summary["instance_totals"]["total_candidates_found"] += len(candidates)
        self._last_scan_summary["instance_totals"]["watchlist_candidates"] += len(ranked)
        self._last_scan_summary["instance_totals"]["alerts_sent"] += sent_count
        self._last_scan_summary["instance_totals"]["alerts_failed"] += failed_count
        self._last_scan_summary["instance_totals"]["duplicates_blocked"] += _already_alerted
        runtime_logger.info(
            "SCAN SUMMARY: %s",
            json.dumps({
                "symbol": outlook.pair,
                "levels_detected": _levels_scanned,
                "gap_levels": _gap_levels,
                "bias_passed": len(candidates),
                "sweep_confirmed": 0,
                "session_passed": 1,
                "distance_passed": len(ranked),
                "watchlist_candidates": len(ranked),
                "alerts_sent": sent_count,
                "alerts_failed": failed_count,
                "reject_reasons": {
                    "distance_rejected": _rejected_count if _primary_reject_reason == "distance_rejected" else 0,
                    "approach_filter_failed": _rejected_count if _primary_reject_reason == "score_too_low" else 0,
                    "duplicate": _already_alerted,
                },
            }),
        )
        runtime_logger.info(
            "DASHBOARD STATUS UPDATED: alerts_sent_this_scan=%d total_alerts=%d telegram=%s",
            sent_count,
            self._last_scan_summary["instance_totals"]["alerts_sent"],
            _tg_status,
        )

        return sent_count

    def _persist_gap_watchlist_candidate(self, outlook, candidate: dict, bias: str) -> None:
        try:
            level = candidate["level"]
            context = getattr(outlook, "context", None)
            payload = {
                "setup_key": candidate["alert_key"],
                "source": "live_bot",
                "strategy_type": "gap_liquidity_sweep_reclaim",
                "alert_stage": "watchlist",
                "setup_status": "watching",
                "symbol": outlook.pair,
                "direction": candidate["direction"],
                "level_type": getattr(level, "level_type", "Gap"),
                "level_price": float(getattr(level, "price", 0.0) or 0.0),
                "level_high": float(getattr(level, "zone_high", getattr(level, "price", 0.0)) or 0.0),
                "level_low": float(getattr(level, "zone_low", getattr(level, "price", 0.0)) or 0.0),
                "timeframe": getattr(level, "timeframe", ""),
                "timeframe_pair": candidate["tf_pair"],
                "session_name": getattr(context, "session_name", "off_session") if context else "off_session",
                "dominant_bias": getattr(context, "dominant_bias", "neutral") if context else "neutral",
                "bias_strength": getattr(context, "bias_strength", "weak") if context else "weak",
                "pd_location": "",
                "distance_to_level_pips": round(float(candidate["distance_pips"]), 2),
                "quality_score": round(float(candidate["score"]), 2),
                "learning_score": 0.0,
                "learning_context": "",
                "watchlist_alert_sent": True,
                "watchlist_alert_sent_at": datetime.now(timezone.utc).isoformat(),
                "entry_alert_sent": False,
                "telegram_alert_sent": True,
                "telegram_alert_sent_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.db.upsert_live_setup(payload)
        except Exception as exc:
            logger.debug("Gap watchlist persistence skipped: %s", exc)

    def _record_live_alert(self, strategy_name: str, trade, *, alert_stage: str) -> None:
        if self.learning is not None:
            profile = self.learning.get_strategy_learning_profile(
                strategy_name,
                {
                    "session_name": getattr(trade, "session_name", ""),
                    "timeframe": getattr(trade, "lower_tf", ""),
                    "direction": getattr(trade, "direction", ""),
                    "dominant_bias": getattr(trade, "dominant_bias", ""),
                    "bias_strength": getattr(trade, "bias_strength", ""),
                    "confirmation_type": getattr(trade, "confirmation_type", ""),
                },
            )
        else:
            profile = {
                "sample_size": 0,
                "win_rate": 0.0,
                "net_pips": 0.0,
                "confidence_tier": "low",
                "recommended_weight": 0.85,
                "warning": "low sample",
            }

        learning_context = (
            f"Historical profile: {strategy_name} | sample={profile.get('sample_size', 0)} "
            f"| WR={profile.get('win_rate', 0.0)}% | net={profile.get('net_pips', 0.0):+}p "
            f"| confidence={profile.get('warning') or profile.get('confidence_tier', 'low')}"
        )
        setattr(trade, "learning_context", learning_context)

        try:
            if strategy_name == "gap_liquidity_sweep_reclaim":
                setup_key = f"{trade.pair}_{trade.direction}_{round(float(getattr(trade, 'level_price', getattr(trade, 'entry_price', 0.0)) or 0.0), 2)}"
            else:
                setup_key = f"{trade.pair}_{trade.direction}_{round(float(getattr(trade, 'entry_price', 0.0) or 0.0), 2)}_{strategy_name}"
            payload = {
                "setup_key": setup_key,
                "source": "live_bot",
                "strategy_type": strategy_name,
                "alert_stage": alert_stage,
                "setup_status": "entry_confirmed" if alert_stage == "entry" else "watching",
                "symbol": trade.pair,
                "direction": trade.direction,
                "entry": trade.entry_price,
                "sl": trade.sl_price,
                "tp1": trade.tp1,
                "tp2": trade.tp2,
                "tp3": trade.tp3,
                "level_type": getattr(trade, "level_type", ""),
                "level_price": getattr(trade, "level_price", None),
                "level_high": getattr(trade, "level_price", None),
                "level_low": getattr(trade, "level_price", None),
                "timeframe": getattr(trade, "lower_tf", ""),
                "timeframe_pair": f"{getattr(trade, 'higher_tf', '')}->{getattr(trade, 'lower_tf', '')}",
                "session_name": getattr(trade, "session_name", ""),
                "dominant_bias": getattr(trade, "dominant_bias", ""),
                "bias_strength": getattr(trade, "bias_strength", ""),
                "confirmation_type": getattr(trade, "confirmation_type", ""),
                "confirmation_score": getattr(trade, "confirmation_score", 0.0),
                "pd_location": getattr(trade, "pd_location", ""),
                "learning_score": profile.get("recommended_weight", 0.85),
                "learning_context": learning_context,
                "quality_rejection_count": getattr(trade, "quality_rejection_count", 0),
                "structure_break_count": getattr(trade, "structure_break_count", 0),
                "entry_alert_sent": alert_stage == "entry",
                "entry_alert_sent_at": datetime.now(timezone.utc).isoformat() if alert_stage == "entry" else None,
                "telegram_alert_sent": True,
                "telegram_alert_sent_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.db.upsert_live_setup(payload)
        except Exception as exc:
            logger.debug("Live setup persistence skipped: %s", exc)

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
        normalized = (
            str(tf_pair)
            .replace("->", "-")
            .replace("→", "-")
            .replace("â†’", "-")
            .replace(" ", "")
        )
        if normalized in ACTIVE_TIMEFRAME_PAIR_LABELS:
            return True
        return normalized in {f"{tf}-{tf}" for tf in ENGULF_ALLOWED_LIVE_TIMEFRAMES}

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
    def _watchlist_key(symbol: str, level, direction: str, timeframe_pair: str) -> str:
        """
        Deduplication key for pre-confirmation setup/watchlist alerts.
        This is separate from signal fingerprints because selected levels are
        not full trade signals yet. It intentionally stays stable across
        timeframe-group reshuffles so the same price/direction is only
        announced once per bot instance; the next alert for that setup should
        be the confirmation / pending-order flow.
        """
        return AlphaPulse._build_gap_watchlist_key(symbol, direction, float(level.price), timeframe_pair)

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
        self._background_tasks["scan_loop"] = False
        self._background_tasks["heartbeat_writer"] = False
        self._background_tasks["runtime_alerts"] = False
        self._background_tasks["market_analyst_loop"] = False
        self._background_tasks["watchlist_loop"] = False
        logger.info("BACKGROUND TASK CANCELLED: name=scan_loop")
        logger.info("BACKGROUND TASK CANCELLED: name=heartbeat_writer")
        logger.info("BACKGROUND TASK CANCELLED: name=runtime_alerts")
        logger.info("BACKGROUND TASK CANCELLED: name=market_analyst_loop")
        logger.info("BACKGROUND TASK CANCELLED: name=watchlist_loop")
        logger.info("MT5 MONITOR STOPPED")
        logger.info("MARKET DATA MONITOR STOPPED")
        logger.info("Shutting down AlphaPulse...")
        runtime_logger.info("BOT INSTANCE STOPPED: instance_id=%s", self._instance_id)
        runtime_logger.info("BOT STOPPED")
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
