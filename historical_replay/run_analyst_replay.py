from __future__ import annotations

import argparse
import json
import os
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from analysis.gold_confirmation_engine import AnalystTradeSetup, GoldConfirmationEngine, confirmation_is_fresh, quality_label_from_score
from analysis.market_analyst import MarketAnalyst
from config.settings import (
    ANALYST_ALLOWED_CONFIRMATIONS,
    ANALYST_MIN_LEARNING_SCORE,
    ANALYST_QUALITY_GATE_ENABLED,
    ANALYST_REPLAY_BATCH_DB_WRITES,
    ANALYST_REPLAY_BATCH_SIZE,
    ANALYST_REPLAY_CONFIRMATION_CHECK_EVERY_CANDLE,
    ANALYST_REPLAY_ENTRY_COOLDOWN_CANDLES,
    ANALYST_REPLAY_MARKET_PLAN_INTERVAL_CANDLES,
    ANALYST_REPLAY_MAX_CONFIRMATIONS_PER_ZONE,
    ANALYST_REPLAY_STORE_ONLY_EVENTS,
    ANALYST_REPLAY_STORE_REJECTIONS,
    ANALYST_REPLAY_VERBOSE_LOGS,
    DECISION_WINDOW_CANDLES,
)
from data.mt5_client import MT5Client
from db.database import Database
from execution.decision_engine import DecisionEngine
from learning.scoring_engine import ScoringEngine
from learning.stats_learner import StatisticalLearner
from learning.rl_engine import LearningEngine
from strategies.filters import MarketContextEngine
from utils.logger import get_logger

logger = get_logger("historical_replay.analyst_replay")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AnalystReplayEngine:
    CONFIRMATION_PRIORITY = {
        "break_retest_close_confirmation": 1,
        "failed_retest_confirmation": 2,
        "sweep_reclaim_confirmation": 3,
        "engulfing_level_confirmation": 4,
        "structure_shift_confirmation": 5,
        "displacement_confirmation": 6,
    }

    def __init__(
        self,
        *,
        db: Database | None = None,
        mt5: MT5Client | None = None,
        verbose: bool = False,
        use_ai_model: bool = False,
        ai_model: str = "",
        ai_schema: str = "",
    ):
        self.db = db or Database()
        self.mt5 = mt5 or MT5Client()
        self.context_engine = MarketContextEngine()
        self.market_analyst = MarketAnalyst()
        self.confirmation_engine = GoldConfirmationEngine()
        self.stats_learner = StatisticalLearner(self.db)
        self.learning = LearningEngine(self.db, self.stats_learner)
        self.scoring_engine = ScoringEngine(self.learning)
        self.decision_engine = DecisionEngine()
        self.verbose = verbose or ANALYST_REPLAY_VERBOSE_LOGS
        self.use_ai_model = use_ai_model
        self.ai_model = ai_model
        self.ai_schema = ai_schema
        self._stop_requested = False
        self._learning_profile_cache: dict[str, dict[str, Any]] = {}
        self._scenario_rows: list[dict[str, Any]] = []
        self._confirmation_rows: list[dict[str, Any]] = []
        self._review_rows: list[dict[str, Any]] = []
        self._last_plan_signature = ""
        self._last_plan_index = -999999
        self._last_material_state = ""
        self._plan_cache = None
        self._confirmation_cooldowns: dict[str, int] = {}
        self._confirmation_counts: dict[str, int] = {}
        self._seen_scenario_keys: set[str] = set()
        self._pending_trades: list[dict[str, Any]] = []
        self._processed_candles = 0
        self._scenarios_stored = 0
        self._confirmations_stored = 0
        self._trades_stored = 0
        self._entries_simulated = 0
        self._raw_confirmation_count = 0
        self._raw_entry_candidate_count = 0
        self._started_at = time.time()
        self._decision_window_candidates: list[dict[str, Any]] = []
        self._last_window_index = -1
        self._install_signal_handlers()

    def run_last_months(self, *, symbol: str = "XAUUSD", months: int = 2, export_learning: bool = False, max_candles: int | None = None) -> dict[str, Any]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, months) * 30)
        return self.run(start=start, end=end, symbol=symbol, export_learning=export_learning, max_candles=max_candles, months=months)

    def run(
        self,
        *,
        start: datetime,
        end: datetime,
        symbol: str = "XAUUSD",
        export_learning: bool = False,
        max_candles: int | None = None,
        months: int | None = None,
    ) -> dict[str, Any]:
        os.environ["ALPHAPULSE_ANALYST_REPLAY"] = "1"
        os.environ["ALPHAPULSE_ANALYST_REPLAY_VERBOSE"] = "1" if self.verbose else "0"
        self.db.init()
        self.mt5.connect()
        run_id = self.db.create_analyst_replay_run(
            {
                "symbol": symbol,
                "source": "historical_replay",
                "status": "running",
                "replay_start": start.isoformat(),
                "replay_end": end.isoformat(),
                "months": months if months is not None else max(1, int((end - start).days / 30)),
            }
        )
        warmup_start = start - timedelta(days=14)
        h4 = self.mt5.get_ohlcv_range("H4", warmup_start, end)
        h1 = self.mt5.get_ohlcv_range("H1", warmup_start, end)
        m15 = self.mt5.get_ohlcv_range("M15", warmup_start, end)
        if m15.empty or h1.empty or h4.empty:
            result = {"run_id": run_id, "error": "missing historical data"}
            if run_id:
                self.db.update_analyst_replay_run(run_id, {"status": "failed", "completed_at": datetime.now(timezone.utc).isoformat(), "summary": result})
            return result

        live_window = m15[m15["time"] >= start].reset_index(drop=True)
        if max_candles:
            live_window = live_window.head(max_candles).reset_index(drop=True)
        total_candles = len(live_window)

        try:
            for idx, row in live_window.iterrows():
                if self._stop_requested:
                    logger.warning("ANALYST REPLAY STOP REQUESTED: flushing pending rows")
                    break
                current_time = pd.Timestamp(row["time"]).to_pydatetime().astimezone(timezone.utc)
                data = {
                    "H4": h4[h4["time"] <= current_time].reset_index(drop=True),
                    "H1": h1[h1["time"] <= current_time].reset_index(drop=True),
                    "M15": m15[m15["time"] <= current_time].reset_index(drop=True),
                }
                if len(data["H4"]) < 20 or len(data["H1"]) < 40 or len(data["M15"]) < 30:
                    continue
                self._processed_candles += 1
                current_price = float(data["M15"].iloc[-1]["close"])
                context = self.context_engine.analyze(data, utc_dt=current_time)

                plan, plan_reused = self._get_market_plan(symbol, data, current_price, context, idx)
                if plan_reused and self.verbose:
                    logger.info("ANALYST REPLAY PLAN REUSED: candle_time=%s reason=no_material_change", current_time.isoformat())
                plan_dict = plan.to_dict()
                self._store_plan_events(run_id, plan_dict, context, current_time, idx)
                self._update_pending_trades(run_id, current_time, current_price, context)

                confirmations = self.confirmation_engine.analyze(data["M15"], plan, current_price) if ANALYST_REPLAY_CONFIRMATION_CHECK_EVERY_CANDLE or not plan_reused else []
                self._raw_confirmation_count += len(confirmations)
                confirmations = self._select_prioritized_confirmations(confirmations)
                self._raw_entry_candidate_count += len(confirmations)
                for confirmation in confirmations:
                    dedupe_key = self._confirmation_dedupe_key(symbol, confirmation)
                    cooldown_hit = self._confirmation_cooldowns.get(dedupe_key)
                    if cooldown_hit is not None and (idx - cooldown_hit) < ANALYST_REPLAY_ENTRY_COOLDOWN_CANDLES:
                        if self.verbose:
                            logger.info("ANALYST REPLAY CONFIRMATION SKIPPED: duplicate zone cooldown")
                        if ANALYST_REPLAY_STORE_REJECTIONS:
                            self._queue_confirmation_row(run_id, confirmation, context, current_time, "ignore", "duplicate_zone_cooldown")
                        continue
                    if self._confirmation_counts.get(dedupe_key, 0) >= ANALYST_REPLAY_MAX_CONFIRMATIONS_PER_ZONE:
                        continue

                    allowed, freshness_reason = confirmation_is_fresh(confirmation, current_price)
                    if confirmation.grade not in {"A", "A+"} or not allowed or plan.plan_status not in {"fresh", "active"}:
                        if self.verbose:
                            logger.info("ANALYST REPLAY SCORING SKIPPED: reason=%s", "weak" if confirmation.grade not in {"A", "A+"} else freshness_reason or "inactive_plan")
                        if ANALYST_REPLAY_STORE_REJECTIONS:
                            self._queue_confirmation_row(
                                run_id,
                                confirmation,
                                context,
                                current_time,
                                "reject" if not allowed else "wait",
                                freshness_reason or "weak_confirmation",
                            )
                        continue
                    if ANALYST_QUALITY_GATE_ENABLED and (
                        confirmation.confirmation_type not in ANALYST_ALLOWED_CONFIRMATIONS
                        or confirmation.confirmation_type in {"structure_shift_confirmation", "displacement_confirmation"}
                    ):
                        if self.verbose:
                            logger.info("ANALYST REPLAY SCORING SKIPPED: reason=weak")
                        if ANALYST_REPLAY_STORE_REJECTIONS:
                            block_reason = "structure_shift_alone_not_allowed" if confirmation.confirmation_type == "structure_shift_confirmation" else "displacement_alone_not_allowed" if confirmation.confirmation_type == "displacement_confirmation" else "weak_confirmation"
                            self._queue_confirmation_row(run_id, confirmation, context, current_time, "reject", block_reason)
                        continue

                    learning_score = self._score_confirmation_cached(plan, confirmation, context)
                    gate_context = self._build_gate_context(symbol, plan, confirmation, context)
                    decision = self.decision_engine.decide(
                        plan,
                        confirmation,
                        learning_score,
                        gate_context=gate_context,
                    )
                    if decision.action == "send_entry_alert":
                        self._decision_window_candidates.append(
                            {
                                "run_id": run_id,
                                "plan": plan,
                                "confirmation": confirmation,
                                "learning_score": learning_score,
                                "decision": decision,
                                "context": context,
                                "current_time": current_time,
                                "idx": idx,
                                "dedupe_key": dedupe_key,
                                "gate_context": gate_context,
                            }
                        )
                        if self.verbose:
                            logger.info("DECISION WINDOW CANDIDATE ADDED")
                    else:
                        self._queue_confirmation_row(
                            run_id,
                            confirmation,
                            context,
                            current_time,
                            decision.action,
                            "" if decision.action == "send_entry_alert" else decision.reason,
                            candidate_rank_score=decision.setup_payload.get("candidate_rank_score"),
                            candidate_rank_reason=decision.setup_payload.get("candidate_rank_reason", ""),
                        )

                if self._should_flush_decision_window(idx, context):
                    self._finalize_decision_window(symbol)

                if self._processed_candles % 500 == 0:
                    self._log_progress(total_candles)
                self._flush_batches_if_needed()

            self._finalize_decision_window(symbol)
            self._flush_all_batches()
            status = "stopped" if self._stop_requested else "completed"
            summary = self._build_summary(run_id, symbol, export_learning, total_candles)
            if run_id:
                self.db.update_analyst_replay_run(
                    run_id,
                    {
                        "status": status,
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "total_scenarios": self._scenarios_stored,
                        "total_confirmations": self._confirmations_stored,
                        "total_entries": self._entries_simulated,
                        "total_reviews": self._trades_stored,
                        "net_pips": summary["net_pips"],
                        "summary": summary,
                    },
                )
            if self._stop_requested:
                logger.warning("ANALYST REPLAY STOPPED CLEANLY")
            return summary
        except KeyboardInterrupt:
            self._stop_requested = True
            logger.warning("ANALYST REPLAY STOP REQUESTED: flushing pending rows")
            self._flush_all_batches()
            if run_id:
                partial = self._build_summary(run_id, symbol, export_learning, total_candles)
                self.db.update_analyst_replay_run(
                    run_id,
                    {
                        "status": "stopped",
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "summary": partial,
                    },
                )
            logger.warning("ANALYST REPLAY STOPPED CLEANLY")
            raise

    def _get_market_plan(self, symbol: str, data: dict[str, pd.DataFrame], current_price: float, context, idx: int):
        force_refresh = idx == 0 or self._plan_cache is None or (idx - self._last_plan_index) >= ANALYST_REPLAY_MARKET_PLAN_INTERVAL_CANDLES
        material_state = self._build_material_state(data, current_price, context)
        if not force_refresh and material_state == self._last_material_state:
            return self._plan_cache, True
        plan = self.market_analyst.analyze(symbol, data, current_price, context=context)
        self._plan_cache = plan
        self._last_plan_signature = getattr(plan, "market_plan_signature", "")
        self._last_plan_index = idx
        self._last_material_state = material_state
        return plan, False

    def _build_material_state(self, data: dict[str, pd.DataFrame], current_price: float, context) -> str:
        h4_last = data["H4"].tail(3)
        h1_last = data["H1"].tail(4)
        dominant_bias = getattr(context, "dominant_bias", "neutral")
        session_name = getattr(context, "session_name", "unknown")
        market_condition = getattr(context, "market_condition", "unknown")
        return "|".join(
            [
                dominant_bias,
                session_name,
                market_condition,
                f"{float(h4_last.iloc[-1]['close']):.2f}" if not h4_last.empty else "0",
                f"{float(h1_last.iloc[-1]['close']):.2f}" if not h1_last.empty else "0",
                f"{round(current_price / 20.0) * 20:.0f}",
            ]
        )

    def _score_confirmation_cached(self, plan, confirmation, context):
        cache_key = "|".join(
            [
                "analyst_layer",
                confirmation.confirmation_type,
                getattr(context, "session_name", "unknown"),
                confirmation.direction,
                f"{getattr(plan, 'dominant_bias', 'neutral')}/{getattr(plan, 'bias_strength', 'weak')}",
            ]
        )
        cached_profile = self._learning_profile_cache.get(cache_key)
        if cached_profile is not None:
            if self.verbose:
                logger.info("LEARNING PROFILE CACHE HIT: key=%s", cache_key)
            original = self.scoring_engine._learning
            self.scoring_engine._learning = None
            result = self.scoring_engine.score_confirmation(
                plan,
                confirmation,
                "analyst_layer",
                {
                    "session_name": getattr(context, "session_name", "unknown"),
                    "timeframe": "M15",
                    "direction": confirmation.direction,
                    "dominant_bias": getattr(plan, "dominant_bias", "neutral"),
                    "bias_strength": getattr(plan, "bias_strength", "weak"),
                    "confirmation_type": confirmation.confirmation_type,
                    "market_condition": getattr(context, "market_condition", "unknown"),
                },
            )
            self.scoring_engine._learning = original
            result.sample_size = int(cached_profile.get("sample_size", result.sample_size))
            result.confidence_tier = str(cached_profile.get("confidence_tier", result.confidence_tier))
            result.historical_win_rate = float(cached_profile.get("win_rate", result.historical_win_rate))
            result.recommended_action = str(cached_profile.get("recommended_action", result.recommended_action))
            result.profile_used = str(cached_profile.get("profile_used", result.profile_used))
            return result

        result = self.scoring_engine.score_confirmation(
            plan,
            confirmation,
            "analyst_layer",
            {
                "session_name": getattr(context, "session_name", "unknown"),
                "timeframe": "M15",
                "direction": confirmation.direction,
                "dominant_bias": getattr(plan, "dominant_bias", "neutral"),
                "bias_strength": getattr(plan, "bias_strength", "weak"),
                "confirmation_type": confirmation.confirmation_type,
                "market_condition": getattr(context, "market_condition", "unknown"),
            },
        )
        self._learning_profile_cache[cache_key] = result.to_dict()
        return result

    def _build_gate_context(self, symbol: str, plan, confirmation, context) -> dict[str, Any]:
        zone_key = f"{confirmation.direction}:{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}"
        confirmation_type = str(getattr(confirmation, "confirmation_type", ""))
        structure_flip_confirmed = confirmation_type in {"break_retest_close_confirmation", "sweep_reclaim_confirmation"}
        key_levels = {
            round(float(level), 2)
            for level in (
                (getattr(plan, "key_supports", []) or [])
                + (getattr(plan, "key_resistances", []) or [])
                + (getattr(plan, "actionable_psych_levels", []) or [])
            )
        }
        key_level_aligned = round(float(getattr(confirmation, "level", 0.0)), 2) in key_levels
        if not key_level_aligned:
            key_level_aligned = any(abs(float(level) - float(getattr(confirmation, "level", 0.0))) <= 5.0 for level in key_levels)
        opposing_levels = getattr(plan, "key_supports", []) if str(getattr(confirmation, "direction", "")).upper() == "BUY" else getattr(plan, "key_resistances", [])
        entry = float(getattr(confirmation, "suggested_entry", 0.0) or 0.0)
        if str(getattr(confirmation, "direction", "")).upper() == "BUY":
            opposing_candidates = [abs(float(level) - entry) for level in opposing_levels if float(level) > entry]
        else:
            opposing_candidates = [abs(entry - float(level)) for level in opposing_levels if float(level) < entry]
        opposing_structure_distance = min(opposing_candidates) if opposing_candidates else 999.0
        zone_reference = float(confirmation.zone_high if str(getattr(confirmation, "direction", "")).upper() == "BUY" else confirmation.zone_low)
        close_away_distance = abs(entry - zone_reference)
        return {
            "symbol": symbol,
            "session_name": getattr(context, "session_name", "unknown"),
            "candle_time": getattr(confirmation, "candle_time", ""),
            "zone_key": zone_key,
            "structure_flip_confirmed": structure_flip_confirmed,
            "key_level_aligned": key_level_aligned,
            "opposing_structure_distance_pips": opposing_structure_distance,
            "close_away_distance_pips": close_away_distance,
            "use_ai_model": self.use_ai_model,
            # Mirror analyst-review storage so AI features match training semantics.
            "h1_state": getattr(context, "h1_state", "unknown"),
            "h1_bias": getattr(context, "h1_bias", "neutral"),
            "h4_bias": getattr(context, "h4_bias", "neutral"),
            "dominant_bias": getattr(context, "dominant_bias", "neutral"),
            "market_condition": getattr(context, "market_condition", "unknown"),
            "ai_model_path": self.ai_model,
            "ai_schema_path": self.ai_schema,
        }

    def _should_flush_decision_window(self, idx: int, context) -> bool:
        if not self._decision_window_candidates:
            return False
        if self._last_window_index < 0:
            self._last_window_index = idx
            logger.info("DECISION WINDOW OPENED")
            return False
        window_elapsed = (idx - self._last_window_index) >= max(1, DECISION_WINDOW_CANDLES - 1)
        if window_elapsed:
            return True
        if len({str(getattr(candidate.get("context"), "session_name", "unknown")) for candidate in self._decision_window_candidates}) > 1:
            return True
        current_session = str(getattr(context, "session_name", "unknown"))
        if current_session not in {
            str(getattr(candidate.get("context"), "session_name", "unknown"))
            for candidate in self._decision_window_candidates
        }:
            return True
        return False

    def _finalize_decision_window(self, symbol: str) -> None:
        if not self._decision_window_candidates:
            return
        candidates = sorted(
            self._decision_window_candidates,
            key=lambda item: (
                float(item["decision"].setup_payload.get("candidate_rank_score", 0.0)),
                float(item["learning_score"].final_score),
            ),
            reverse=True,
        )
        winner = candidates[0]
        logger.info("DECISION WINDOW WINNER SELECTED")
        for dropped in candidates[1:]:
            self._queue_confirmation_row(
                dropped["run_id"],
                dropped["confirmation"],
                dropped["context"],
                dropped["current_time"],
                "ignore",
                "lower_ranked_candidate",
                candidate_rank_score=dropped["decision"].setup_payload.get("candidate_rank_score"),
                candidate_rank_reason=dropped["decision"].setup_payload.get("candidate_rank_reason", ""),
            )
            if self.verbose:
                logger.info("CANDIDATE REJECTED: lower_ranked_candidate")

        confirmation = winner["confirmation"]
        decision = winner["decision"]
        context = winner["context"]
        current_time = winner["current_time"]
        idx = int(winner["idx"])
        dedupe_key = winner["dedupe_key"]
        self._queue_confirmation_row(
            winner["run_id"],
            confirmation,
            context,
            current_time,
            decision.action,
            "",
            candidate_rank_score=decision.setup_payload.get("candidate_rank_score"),
            candidate_rank_reason=decision.setup_payload.get("candidate_rank_reason", ""),
        )
        self._confirmation_cooldowns[dedupe_key] = idx
        self._confirmation_counts[dedupe_key] = self._confirmation_counts.get(dedupe_key, 0) + 1
        self._entries_simulated += 1
        candle_time = str(getattr(confirmation, "candle_time", ""))
        date_key = candle_time.split("T", 1)[0] if "T" in candle_time else "unknown_date"
        self.decision_engine.record_entry(
            symbol=symbol,
            date_key=date_key,
            session_name=getattr(context, "session_name", "unknown"),
            zone_key=f"{confirmation.direction}:{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}",
        )
        self._pending_trades.append(
            {
                "setup": AnalystTradeSetup(
                    strategy_type="analyst_layer",
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
                ),
                "confirmation": confirmation,
                "learning_score": winner["learning_score"].final_score,
                "candidate_rank_score": decision.setup_payload.get("candidate_rank_score", 0.0),
                "ai_prediction": decision.setup_payload.get("ai_prediction", {}),
                "opened_index": idx,
                "opened_at": current_time,
                "context": context,
                "scenario_key": f"{confirmation.scenario}:{confirmation.direction}:{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}",
                "tp1_touched": False,
            }
        )
        self._decision_window_candidates = []
        self._last_window_index = idx

    def _store_plan_events(self, run_id: int | None, plan_dict: dict[str, Any], context, current_time: datetime, idx: int) -> None:
        for label in ("primary", "secondary"):
            scenario = plan_dict.get(f"{label}_scenario", {}) or {}
            if not scenario:
                continue
            scenario_key = f"{label}:{scenario.get('direction', '')}:{scenario.get('watch_zone', '')}:{plan_dict.get('market_plan_signature', '')}"
            if ANALYST_REPLAY_STORE_ONLY_EVENTS and scenario_key in self._seen_scenario_keys:
                continue
            self._seen_scenario_keys.add(scenario_key)
            zone_low, zone_high = self._split_zone_bounds(str(scenario.get("watch_zone", "")))
            self._scenario_rows.append(
                {
                    "run_id": run_id,
                    "symbol": plan_dict.get("symbol", "XAUUSD"),
                    "scenario_key": scenario_key,
                    "source": "historical_replay",
                    "primary_or_secondary": label,
                    "scenario_type": f"{scenario.get('direction', '').lower()}_{label}",
                    "direction": scenario.get("direction", ""),
                    "zone_low": zone_low,
                    "zone_high": zone_high,
                    "trigger_conditions": scenario.get("triggers", []),
                    "invalidation_level": self._parse_numeric_level(scenario.get("invalidation")),
                    "tp_targets": scenario.get("targets", []),
                    "h4_bias": plan_dict.get("dominant_bias", "neutral"),
                    "h1_bias": plan_dict.get("market_structure_state", "unknown"),
                    "dominant_bias": plan_dict.get("dominant_bias", "neutral"),
                    "bias_strength": plan_dict.get("bias_strength", "weak"),
                    "session_name": getattr(context, "session_name", "unknown"),
                    "psychological_level_context": plan_dict.get("actionable_psych_levels", []),
                    "market_condition": getattr(context, "market_condition", "unknown"),
                    "status": plan_dict.get("plan_status", "active"),
                    "created_at": current_time.isoformat(),
                    "notes": scenario.get("reason", ""),
                }
            )

    def _queue_confirmation_row(
        self,
        run_id: int | None,
        confirmation,
        context,
        current_time: datetime,
        decision: str,
        rejection_reason: str,
        *,
        candidate_rank_score: float | None = None,
        candidate_rank_reason: str = "",
    ) -> None:
        self._confirmation_rows.append(
            {
                "run_id": run_id,
                "symbol": "XAUUSD",
                "scenario_key": f"{confirmation.scenario}:{confirmation.direction}:{confirmation.zone_low:.2f}-{confirmation.zone_high:.2f}",
                "confirmation_key": confirmation.confirmation_signature,
                "source": "historical_replay",
                "scenario_type": confirmation.scenario,
                "confirmation_type": confirmation.confirmation_type,
                "confirmation_grade": confirmation.grade,
                "confirmation_score": confirmation.score,
                "direction": confirmation.direction,
                "level": confirmation.level,
                "entry": confirmation.suggested_entry,
                "sl": confirmation.suggested_sl,
                "tp1": confirmation.suggested_tps.get("tp1", 0.0),
                "tp2": confirmation.suggested_tps.get("tp2", 0.0),
                "tp3": confirmation.suggested_tps.get("tp3", 0.0),
                "decision": decision,
                "rejection_reason": rejection_reason,
                "session_name": getattr(context, "session_name", "unknown"),
                "timeframe": "M15",
                "created_at": current_time.isoformat(),
                "candidate_rank_score": candidate_rank_score,
                "candidate_rank_reason": candidate_rank_reason,
            }
        )

    def _update_pending_trades(self, run_id: int | None, current_time: datetime, current_price: float, context) -> None:
        remaining: list[dict[str, Any]] = []
        for trade in self._pending_trades:
            setup = trade["setup"]
            tp1_touched = trade.get("tp1_touched", False)
            result = None
            if setup.direction.upper() == "SELL":
                if tp1_touched:
                    # Running after TP1 protected: BE-SL = entry
                    if current_price >= setup.entry:
                        result = ("BREAKEVEN_WIN", 0.0, True, False, False)
                    elif current_price <= setup.tp3:
                        result = ("STRONG_WIN", round(setup.entry - setup.tp3, 2), True, True, True)
                    elif current_price <= setup.tp2:
                        result = ("WIN", round(setup.entry - setup.tp2, 2), True, True, False)
                else:
                    if current_price >= setup.sl:
                        result = ("LOSS", round(setup.entry - setup.sl, 2), False, False, False)
                    elif current_price <= setup.tp3:
                        result = ("STRONG_WIN", round(setup.entry - setup.tp3, 2), True, True, True)
                    elif current_price <= setup.tp2:
                        result = ("WIN", round(setup.entry - setup.tp2, 2), True, True, False)
                    elif current_price <= setup.tp1:
                        trade["tp1_touched"] = True
            else:
                if tp1_touched:
                    # Running after TP1 protected: BE-SL = entry
                    if current_price <= setup.entry:
                        result = ("BREAKEVEN_WIN", 0.0, True, False, False)
                    elif current_price >= setup.tp3:
                        result = ("STRONG_WIN", round(setup.tp3 - setup.entry, 2), True, True, True)
                    elif current_price >= setup.tp2:
                        result = ("WIN", round(setup.tp2 - setup.entry, 2), True, True, False)
                else:
                    if current_price <= setup.sl:
                        result = ("LOSS", round(setup.sl - setup.entry, 2), False, False, False)
                    elif current_price >= setup.tp3:
                        result = ("STRONG_WIN", round(setup.tp3 - setup.entry, 2), True, True, True)
                    elif current_price >= setup.tp2:
                        result = ("WIN", round(setup.tp2 - setup.entry, 2), True, True, False)
                    elif current_price >= setup.tp1:
                        trade["tp1_touched"] = True
            if result is None and (self._processed_candles - trade["opened_index"]) < 32:
                remaining.append(trade)
                continue
            if result is None:
                if trade.get("tp1_touched"):
                    result = ("EXPIRED_AFTER_TP1", 0.0, True, False, False)
                else:
                    result = ("EXPIRED_BEFORE_TP", 0.0, False, False, False)
            final_result, pips_result, tp1_hit, tp2_hit, tp3_hit = result
            self._review_rows.append(
                {
                    "run_id": run_id,
                    "setup_id": trade["confirmation"].confirmation_signature,
                    "symbol": "XAUUSD",
                    "scenario_key": trade["scenario_key"],
                    "scenario_type": trade["confirmation"].scenario,
                    "direction": setup.direction,
                    "entry": setup.entry,
                    "sl": setup.sl,
                    "tp1": setup.tp1,
                    "tp2": setup.tp2,
                    "tp3": setup.tp3,
                    "confirmation_type": setup.confirmation_type,
                    "confirmation_grade": setup.confirmation_grade,
                    "learning_score": trade["learning_score"],
                    "candidate_rank_score": float(trade.get("candidate_rank_score", 0.0) or 0.0),
                    "decision_reason": setup.entry_reason,
                    "session_name": getattr(context, "session_name", "unknown"),
                    "h4_bias": getattr(context, "dominant_bias", "neutral"),
                    "h1_bias": getattr(context, "h1_state", "unknown"),
                    "market_condition": getattr(context, "market_condition", "unknown"),
                    "reaction_level": float(getattr(setup, "reaction_level", 0.0) or 0.0),
                    "invalidation_level": float(getattr(setup, "invalidation_level", 0.0) or 0.0),
                    "risk_pips": float(getattr(setup, "risk_pips", 0.0) or 0.0),
                    "tp1_reward_pips": float(getattr(setup, "tp1_reward_pips", 0.0) or 0.0),
                    "tp2_reward_pips": float(getattr(setup, "tp2_reward_pips", 0.0) or 0.0),
                    "tp3_reward_pips": float(getattr(setup, "tp3_reward_pips", 0.0) or 0.0),
                    "tp1_rr": float(getattr(setup, "tp1_rr", 0.0) or 0.0),
                    "tp2_rr": float(getattr(setup, "tp2_rr", 0.0) or 0.0),
                    "tp3_rr": float(getattr(setup, "tp3_rr", 0.0) or 0.0),
                    "sl_source": str(getattr(setup, "sl_source", "structure_sl_engine")),
                    "tp_source": str(getattr(setup, "tp_source", "structure_tp_engine")),
                    "trade_path_source": str(getattr(setup, "trade_path_source", "trade_path_engine")),
                    "trade_path_rationale": str(getattr(setup, "trade_path_rationale", "")),
                    "target_roles": dict(getattr(setup, "target_roles", {}) or {}),
                    "setup_quality_label": str(getattr(setup, "setup_quality_label", "QUALITY SETUP")),
                    "result": final_result,
                    "pips_result": pips_result,
                    "tp1_hit": tp1_hit or trade.get("tp1_touched", False),
                    "tp2_hit": tp2_hit,
                    "tp3_hit": tp3_hit,
                    "protected_after_tp1": tp1_hit or trade.get("tp1_touched", False),
                    "review_notes": json.dumps({"ai_prediction": trade.get("ai_prediction", {}), "use_ai_model": self.use_ai_model}, default=str) if self.use_ai_model else "",
                    "created_at": trade["opened_at"].isoformat(),
                    "closed_at": current_time.isoformat(),
                }
            )
            if final_result == "LOSS":
                self.decision_engine.record_loss(symbol="XAUUSD", date_key=trade["opened_at"].date().isoformat())
        self._pending_trades = remaining

    def _flush_batches_if_needed(self) -> None:
        if not ANALYST_REPLAY_BATCH_DB_WRITES:
            self._flush_all_batches()
            return
        if len(self._scenario_rows) >= ANALYST_REPLAY_BATCH_SIZE or len(self._confirmation_rows) >= ANALYST_REPLAY_BATCH_SIZE or len(self._review_rows) >= ANALYST_REPLAY_BATCH_SIZE:
            self._flush_all_batches()

    def _flush_all_batches(self) -> None:
        scenarios = len(self._scenario_rows)
        confirmations = len(self._confirmation_rows)
        reviews = len(self._review_rows)
        if scenarios == confirmations == reviews == 0:
            return
        self._scenarios_stored += self.db.bulk_insert_analyst_scenarios(self._scenario_rows)
        self._confirmations_stored += self.db.bulk_insert_analyst_confirmations(self._confirmation_rows)
        self._trades_stored += self.db.bulk_insert_analyst_trade_reviews(self._review_rows)
        logger.info(
            "ANALYST REPLAY BATCH FLUSH: scenarios=%d confirmations=%d reviews=%d",
            scenarios,
            confirmations,
            reviews,
        )
        self._scenario_rows = []
        self._confirmation_rows = []
        self._review_rows = []

    def _log_progress(self, total_candles: int) -> None:
        elapsed = max(time.time() - self._started_at, 1.0)
        pct = (self._processed_candles / max(total_candles, 1)) * 100.0
        rate = self._processed_candles / elapsed
        remaining = max(total_candles - self._processed_candles, 0)
        eta = remaining / rate if rate > 0 else 0.0
        logger.info(
            "ANALYST REPLAY PROGRESS: processed=%d total=%d percent=%.1f elapsed=%.1fs eta=%.1fs scenarios=%d confirmations=%d trades=%d",
            self._processed_candles,
            total_candles,
            pct,
            elapsed,
            eta,
            self._scenarios_stored + len(self._scenario_rows),
            self._confirmations_stored + len(self._confirmation_rows),
            self._trades_stored + len(self._review_rows),
        )

    def _build_summary(self, run_id: int | None, symbol: str, export_learning: bool, total_candles: int) -> dict[str, Any]:
        all_reviews = self.db.get_analyst_trade_reviews(run_id=run_id) if run_id else []
        net_pips = round(sum(float(row.get("pips_result") or 0.0) for row in all_reviews), 2)
        return {
            "run_id": run_id,
            "symbol": symbol,
            "processed_candles": self._processed_candles,
            "total_candles": total_candles,
            "total_scenarios_generated": self._scenarios_stored,
            "total_confirmations": self._confirmations_stored,
            "raw_confirmations": self._raw_confirmation_count,
            "raw_entry_candidates": self._raw_entry_candidate_count,
            "total_entries": self._entries_simulated,
            "total_reviews": self._trades_stored,
            "net_pips": net_pips,
            "wins": sum(1 for row in all_reviews if row.get("result") in {"WIN", "STRONG_WIN", "BREAKEVEN_WIN"}),
            "losses": sum(1 for row in all_reviews if row.get("result") == "LOSS"),
            "expired_before_tp": sum(1 for row in all_reviews if row.get("result") == "EXPIRED_BEFORE_TP"),
            "expired_after_tp1": sum(1 for row in all_reviews if row.get("result") == "EXPIRED_AFTER_TP1"),
            "tp1_rate": round((sum(1 for row in all_reviews if row.get("tp1_hit")) / max(len(all_reviews), 1)) * 100.0, 2) if all_reviews else 0.0,
            "tp2_rate": round((sum(1 for row in all_reviews if row.get("tp2_hit")) / max(len(all_reviews), 1)) * 100.0, 2) if all_reviews else 0.0,
            "tp3_rate": round((sum(1 for row in all_reviews if row.get("tp3_hit")) / max(len(all_reviews), 1)) * 100.0, 2) if all_reviews else 0.0,
            "export_learning": export_learning,
            "use_ai_model": self.use_ai_model,
            "status": "stopped" if self._stop_requested else "completed",
        }

    def _select_prioritized_confirmations(self, confirmations: list) -> list:
        grouped: dict[str, list] = {}
        for confirmation in confirmations:
            key = ":".join(
                [
                    str(getattr(confirmation, "candle_time", "")),
                    str(getattr(confirmation, "direction", "")),
                    f"{getattr(confirmation, 'zone_low', 0.0):.2f}",
                    f"{getattr(confirmation, 'zone_high', 0.0):.2f}",
                ]
            )
            grouped.setdefault(key, []).append(confirmation)

        selected: list = []
        for items in grouped.values():
            ordered = sorted(
                items,
                key=lambda conf: (
                    self.CONFIRMATION_PRIORITY.get(getattr(conf, "confirmation_type", ""), 99),
                    -1 * (1 if str(getattr(conf, "grade", "")).upper() == "A+" else 0),
                ),
            )
            best = ordered[0]
            selected.append(best)
            if self.verbose:
                logger.info(
                    "CONFIRMATION SELECTED: type=%s priority=%d",
                    getattr(best, "confirmation_type", ""),
                    self.CONFIRMATION_PRIORITY.get(getattr(best, "confirmation_type", ""), 99),
                )
            for dropped in ordered[1:]:
                if self.verbose:
                    logger.info(
                        "CONFIRMATION DROPPED: type=%s reason=lower_priority_same_zone",
                        getattr(dropped, "confirmation_type", ""),
                    )
        return selected

    def _confirmation_dedupe_key(self, symbol: str, confirmation) -> str:
        return ":".join(
            [
                symbol,
                confirmation.direction,
                str(getattr(confirmation, "scenario_id", "")),
                str(getattr(confirmation, "watch_zone_id", "")),
                confirmation.confirmation_type,
                f"{confirmation.zone_low:.2f}",
                f"{confirmation.zone_high:.2f}",
            ]
        )

    def _install_signal_handlers(self) -> None:
        def _handle_stop(_signum, _frame):
            self._stop_requested = True

        signal.signal(signal.SIGINT, _handle_stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_stop)

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Spencer analyst replay.")
    parser.add_argument("--symbol", type=str, default="XAUUSD")
    parser.add_argument("--months", type=int, default=2)
    parser.add_argument("--days", type=int, default=0)
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--max-candles", type=int, default=0)
    parser.add_argument("--export-learning", action="store_true", dest="export_learning")
    parser.add_argument("--no-export-learning", action="store_true", dest="no_export_learning")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--use-ai-model", action="store_true", dest="use_ai_model")
    parser.add_argument("--ai-model", type=str, default="")
    parser.add_argument("--ai-schema", type=str, default="")
    args = parser.parse_args()

    export_learning = False if args.no_export_learning else args.export_learning
    engine = AnalystReplayEngine(
        verbose=args.verbose,
        use_ai_model=args.use_ai_model,
        ai_model=args.ai_model,
        ai_schema=args.ai_schema,
    )
    if args.start and args.end:
        result = engine.run(
            start=_parse_utc(args.start),
            end=_parse_utc(args.end),
            symbol=args.symbol,
            export_learning=export_learning,
            max_candles=args.max_candles or None,
            months=args.months,
        )
    elif args.days:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=max(1, args.days))
        result = engine.run(
            start=start,
            end=end,
            symbol=args.symbol,
            export_learning=export_learning,
            max_candles=args.max_candles or None,
            months=max(1, int(args.days / 30) or 1),
        )
    else:
        result = engine.run_last_months(
            symbol=args.symbol,
            months=args.months,
            export_learning=export_learning,
            max_candles=args.max_candles or None,
        )
    print(result)


if __name__ == "__main__":
    main()
