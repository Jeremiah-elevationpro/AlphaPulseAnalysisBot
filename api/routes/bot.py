"""
Bot lifecycle control endpoints.

POST /api/bot/start    — spawn main.py as a subprocess
POST /api/bot/stop     — gracefully shut it down + send Telegram stop alert
POST /api/bot/restart  — stop then start
GET  /api/bot/status   — read state; enriched from heartbeat file while running
"""
import json
import logging
import os
import signal
import subprocess
import sys
import time

from fastapi import APIRouter

import api.state as state

router = APIRouter()
logger = logging.getLogger("alphapulse.api.bot")

# How stale (seconds) a heartbeat may be before the UI warns
HEARTBEAT_STALE_SECONDS = 180

INSTANCE_LOCK_FILE = state.ROOT_DIR / "spencer_instance.lock"


def _write_instance_lock(pid: int, instance_id: str) -> None:
    try:
        INSTANCE_LOCK_FILE.write_text(
            json.dumps({"pid": pid, "started_at": state.now_iso(), "instance_id": instance_id}),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("INSTANCE LOCK WRITE FAILED: %s", exc)


def _remove_instance_lock() -> None:
    try:
        if INSTANCE_LOCK_FILE.exists():
            INSTANCE_LOCK_FILE.unlink()
    except Exception as exc:
        logger.warning("INSTANCE LOCK REMOVE FAILED: %s", exc)


def _check_instance_lock() -> int | None:
    """Return PID from lock file if it exists and the process is alive, else None."""
    try:
        if not INSTANCE_LOCK_FILE.exists():
            return None
        data = json.loads(INSTANCE_LOCK_FILE.read_text(encoding="utf-8"))
        pid = int(data.get("pid") or 0)
        if pid and _pid_exists(pid):
            return pid
        logger.warning("STALE INSTANCE LOCK REMOVED: pid=%s was not alive", pid)
        _remove_instance_lock()
    except Exception as exc:
        logger.warning("INSTANCE LOCK CHECK FAILED: %s", exc)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_process_running() -> bool:
    return state.bot_process is not None and state.bot_process.poll() is None


def _pid_exists(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    except Exception:
        return False
    return True


def _active_process_id() -> int | None:
    if _is_process_running() and state.bot_process is not None:
        return state.bot_process.pid

    hb = state.read_heartbeat()
    hb_pid = hb.get("process_id") if hb else None
    if hb_pid and _pid_exists(hb_pid):
        return int(hb_pid)

    if hb_pid and not _pid_exists(hb_pid):
        logger.warning("STALE HEARTBEAT CLEARED: process_id=%s is no longer running", hb_pid)
        state.clear_heartbeat()
    return None


def _sync_process_state() -> None:
    """
    Reconcile state.bot_state with actual process state + heartbeat file.
    Called before every response so callers always see fresh truth.
    """
    # Never override replay status from process polling
    if state.bot_state["status"] == "running_replay":
        return

    if _is_process_running():
        # Process alive — enrich from heartbeat when available
        hb = state.read_heartbeat()
        if hb:
            runtime_control = state.read_runtime_control()
            hb_status = hb.get("status", "online")
            # Only accept richer lifecycle statuses from heartbeat
            if hb_status in ("analyzing", "watching", "online", "error"):
                state.bot_state["status"]          = hb_status
                state.bot_state["message"]         = hb.get("message", f"Spencer is {hb_status}")
            elif state.bot_state["status"] not in ("starting", "stopping"):
                state.bot_state["status"]  = "online"
                state.bot_state["message"] = "Spencer is online"

            state.bot_state["last_heartbeat_at"]     = hb.get("timestamp")
            state.bot_state["last_scan_at"]          = hb.get("last_scan_at")
            state.bot_state["last_scan_result"]      = hb.get("last_scan_result")
            state.bot_state["session"]               = hb.get("current_session")
            state.bot_state["last_scan_symbol"]      = hb.get("last_scan_symbol", "XAUUSD")
            state.bot_state["last_candidates_count"] = hb.get("last_candidates_count", 0)
            state.bot_state["last_alerts_sent"]      = hb.get("last_alerts_sent", 0)
            state.bot_state["last_alerts_failed"]    = hb.get("last_alerts_failed", 0)
            state.bot_state["last_reject_reason"]    = hb.get("last_reject_reason")
            state.bot_state["last_telegram_status"]  = hb.get("last_telegram_status")
            state.bot_state["last_telegram_error"]   = hb.get("last_telegram_error")
            state.bot_state["last_telegram_alert_type"] = hb.get("last_telegram_alert_type")
            state.bot_state["last_telegram_alert_time"] = hb.get("last_telegram_alert_time")
            state.bot_state["last_scan_number"]      = hb.get("last_scan_number", 0)
            state.bot_state["session_blocking"]      = hb.get("session_blocking", False)
            state.bot_state["instance_id"]           = hb.get("instance_id")
            state.bot_state["scan_allowed"]          = hb.get("scan_allowed", True)
            state.bot_state["levels_detected"]       = (hb.get("last_scan_summary") or {}).get("levels_detected", 0)
            state.bot_state["gap_levels"]            = (hb.get("last_scan_summary") or {}).get("gap_levels", 0)
            state.bot_state["bias_passed"]           = (hb.get("last_scan_summary") or {}).get("bias_passed", 0)
            state.bot_state["sweep_confirmed"]       = (hb.get("last_scan_summary") or {}).get("sweep_confirmed", 0)
            state.bot_state["session_passed"]        = (hb.get("last_scan_summary") or {}).get("session_passed", 0)
            state.bot_state["distance_passed"]       = (hb.get("last_scan_summary") or {}).get("distance_passed", 0)
            state.bot_state["watchlist_candidates"]  = (hb.get("last_scan_summary") or {}).get("watchlist_candidates", 0)
            state.bot_state["alerts_sent_this_scan"] = (hb.get("last_scan_summary") or {}).get("alerts_sent", 0)
            state.bot_state["alerts_failed_this_scan"] = (hb.get("last_scan_summary") or {}).get("alerts_failed", 0)
            state.bot_state["dedupe_rejections"]     = (hb.get("last_scan_summary") or {}).get("reject_reasons", {}).get("duplicate", 0)
            instance_totals = hb.get("instance_totals") or {}
            state.bot_state["total_scans"]           = instance_totals.get("total_scans", 0)
            state.bot_state["total_candidates_found"] = instance_totals.get("total_candidates_found", 0)
            state.bot_state["total_watchlist_candidates"] = instance_totals.get("watchlist_candidates", 0)
            state.bot_state["total_alerts_sent"]     = instance_totals.get("alerts_sent", 0)
            state.bot_state["total_alerts_failed"]   = instance_totals.get("alerts_failed", 0)
            state.bot_state["total_duplicates_blocked"] = instance_totals.get("duplicates_blocked", 0)
            state.bot_state["total_manual_alerts_sent"] = instance_totals.get("manual_alerts_sent", 0)
            state.bot_state["total_confirmation_alerts_sent"] = instance_totals.get("confirmation_alerts_sent", 0)
            state.bot_state["bot_window_active"]     = hb.get("bot_window_active")
            state.bot_state["local_time"]            = hb.get("local_time")
            state.bot_state["active_until"]          = hb.get("active_until")
            state.bot_state["current_price"]         = hb.get("current_price")
            state.bot_state["bid"]                   = hb.get("bid")
            state.bot_state["ask"]                   = hb.get("ask")
            state.bot_state["spread"]                = hb.get("spread")
            state.bot_state["spread_pips"]           = hb.get("spread_pips")
            state.bot_state["d1_bias"]               = hb.get("d1_bias")
            state.bot_state["h4_bias_detail"]        = hb.get("h4_bias")
            state.bot_state["h1_bias"]               = hb.get("h1_bias")
            state.bot_state["dominant_bias"]         = hb.get("dominant_bias")
            state.bot_state["bias_strength"]         = hb.get("bias_strength")
            state.bot_state["last_market_update_at"] = hb.get("last_market_update_at")
            state.bot_state["live_enabled_strategies"]     = hb.get("live_enabled_strategies", state.bot_state.get("live_enabled_strategies"))
            state.bot_state["research_only_strategies"]    = hb.get("research_only_strategies", state.bot_state.get("research_only_strategies"))
            state.bot_state["strategy_scans"]              = hb.get("strategy_scans", state.bot_state.get("strategy_scans", {}))
            state.bot_state["operating_mode"]              = hb.get("operating_mode", "24_7")
            state.bot_state["process_id"]                  = hb.get("process_id", state.bot_state.get("process_id"))
            state.bot_state["market_plan"]                 = hb.get("market_plan", state.bot_state.get("market_plan"))
            state.bot_state["priceFeed"]                   = hb.get("priceFeed", state.bot_state.get("priceFeed", {}))
            state.bot_state["five_layer_status"]           = hb.get("five_layer_status", state.bot_state.get("five_layer_status"))
            state.bot_state["active_trade_state"]          = hb.get("active_trade_state", state.bot_state.get("active_trade_state", {}))
            state.bot_state["trade_tracking"]              = hb.get("trade_tracking", state.bot_state.get("trade_tracking", {}))
            five_layer = state.bot_state.get("five_layer_status") or {}
            state.bot_state["level_intelligence"]          = five_layer.get("level_intelligence", state.bot_state.get("level_intelligence", {}))
            state.bot_state["scenario_compliance"]         = five_layer.get("scenario_compliance", state.bot_state.get("scenario_compliance", {}))
            state.bot_state["ai_prediction"]               = hb.get("ai_prediction", state.bot_state.get("ai_prediction", {}))
            state.bot_state["ai_predictive_layer"]          = hb.get("ai_predictive_layer", state.bot_state.get("ai_predictive_layer", {}))
            state.bot_state["alert_dedupe"]               = hb.get("alert_dedupe", state.bot_state.get("alert_dedupe"))
            state.bot_state["active_instance_id"]          = runtime_control.get("active_instance_id") or hb.get("instance_id")
            state.bot_state["bot_process_alive"]           = True
            state.bot_state["background_tasks_active"]     = hb.get("background_tasks_active", 1)
            state.bot_state["runtime_alerts_enabled"]      = bool(runtime_control.get("runtime_alerts_enabled", False))
            state.bot_state["last_shutdown_time"]          = runtime_control.get("last_shutdown_time")
            if hb.get("last_error"):
                state.bot_state["last_error"] = hb["last_error"]
        else:
            # No heartbeat yet (process just started) — keep starting/online
            if state.bot_state["status"] not in ("starting", "stopping"):
                state.bot_state["status"]  = "online"
                state.bot_state["message"] = "Spencer is online"
            runtime_control = state.read_runtime_control()
            state.bot_state["active_instance_id"] = runtime_control.get("active_instance_id")
            state.bot_state["bot_process_alive"] = True
            state.bot_state["background_tasks_active"] = 1
            state.bot_state["runtime_alerts_enabled"] = bool(runtime_control.get("runtime_alerts_enabled", False))
            state.bot_state["last_shutdown_time"] = runtime_control.get("last_shutdown_time")
    else:
        # Process not running
        prev = state.bot_state["status"]
        if prev in ("online", "analyzing", "watching", "starting"):
            # Died unexpectedly
            logger.warning("BOT PROCESS CRASHED — previous status: %s", prev)
            state.bot_state["status"]       = "error"
            state.bot_state["message"]      = "Spencer process exited unexpectedly"
            state.bot_state["process_id"]   = None
            state.bot_state["instance_id"]  = None
            state.bot_state["error_message"] = "Process exited with no stop command"
        elif prev not in ("stopping", "error", "offline"):
            state.bot_state["status"]  = "offline"
            state.bot_state["message"] = "Spencer is offline"
        runtime_control = state.read_runtime_control()
        state.bot_state["active_instance_id"] = runtime_control.get("active_instance_id")
        state.bot_state["bot_process_alive"] = False
        state.bot_state["background_tasks_active"] = 0
        state.bot_state["runtime_alerts_enabled"] = bool(runtime_control.get("runtime_alerts_enabled", False))
        state.bot_state["last_shutdown_time"] = runtime_control.get("last_shutdown_time")


def _telegram_health_snapshot() -> dict:
    """Read the Telegram health file (best-effort). Surface as systemHealth field."""
    try:
        from notifications.telegram_health import read_health
        return read_health()
    except Exception:
        return {}


def _structured_blocks(s: dict) -> dict:
    """Derive Spencer v1.1.0 layered status blocks from the legacy state dict.

    Pure shaping only — no DB calls, no business logic. Old fields stay in
    ``developerDiagnostics`` for back-compat.
    """
    plan = s.get("market_plan") or {}
    five = s.get("five_layer_status") or {}
    market_layer = (five.get("market_analyst") or {}) if isinstance(five, dict) else {}
    confirmation_layer = (five.get("confirmation_engine") or {}) if isinstance(five, dict) else {}
    learning_layer = (five.get("learning_score") or {}) if isinstance(five, dict) else {}
    pytorch_layer = (five.get("pytorch_ai") or {}) if isinstance(five, dict) else {}
    decision_layer = (five.get("decision_engine") or {}) if isinstance(five, dict) else {}
    risk_layer = (five.get("risk_management") or {}) if isinstance(five, dict) else {}
    ai_layer = s.get("ai_predictive_layer") or s.get("ai_prediction") or pytorch_layer or {}
    tg_health = _telegram_health_snapshot()
    primary = (plan.get("primary_scenario") or {}) if isinstance(plan, dict) else {}
    secondary = (plan.get("secondary_scenario") or {}) if isinstance(plan, dict) else {}

    spencer_status = {
        "running": s.get("status") in ("online", "analyzing", "watching", "starting"),
        "status": s.get("status"),
        "mode": s.get("strategy_mode"),
        "symbol": s.get("symbol"),
        "currentPrice": s.get("current_price"),
        "currentSession": s.get("session"),
        "lastScanAt": s.get("last_scan_at"),
        "lastMarketPlanAt": (plan or {}).get("last_updated") if isinstance(plan, dict) else None,
        "lastEntryAlertAt": s.get("last_telegram_alert_time") if s.get("last_telegram_alert_type") in {"ANALYST ENTRY", "PENDING ORDER ALERT"} else None,
        "lastHeartbeatAt": s.get("last_heartbeat_at"),
    }

    market_analyst = {
        "h4Context": (plan or {}).get("h4_context") if isinstance(plan, dict) else None,
        "h1Context": (plan or {}).get("h1_context") if isinstance(plan, dict) else None,
        "m15Context": (plan or {}).get("m15_context") if isinstance(plan, dict) else None,
        "dominantBias": (plan or {}).get("dominant_bias") or s.get("dominant_bias"),
        "biasStrength": (plan or {}).get("bias_strength") or s.get("bias_strength"),
        "marketCondition": market_layer.get("market_condition") or market_layer.get("plan_status"),
        "primaryScenario": primary or None,
        "secondaryScenario": secondary or None,
        "watchZones": (plan or {}).get("active_watch_zones") if isinstance(plan, dict) else None,
        "psychologicalLevels": (plan or {}).get("actionable_psych_levels") or (plan or {}).get("psychological_levels") if isinstance(plan, dict) else None,
        "keySupports": (plan or {}).get("key_supports") if isinstance(plan, dict) else None,
        "keyResistances": (plan or {}).get("key_resistances") if isinstance(plan, dict) else None,
        "scenarioStatus": (plan or {}).get("plan_status") if isinstance(plan, dict) else market_layer.get("plan_status"),
    }

    confirmation_engine = {
        "watchingFor": (plan or {}).get("confirmation_waiting_for") if isinstance(plan, dict) else None,
        "lastConfirmation": confirmation_layer.get("last_confirmation"),
        "confirmationType": confirmation_layer.get("confirmation_type"),
        "confirmationGrade": confirmation_layer.get("confirmation_grade") or confirmation_layer.get("grade"),
        "confirmationScore": confirmation_layer.get("confirmation_score") or confirmation_layer.get("score"),
        "confirmationStatus": confirmation_layer.get("status"),
        "staleConfirmationBlocked": bool(confirmation_layer.get("stale_blocked", False)),
        "tp1AlreadyReachedBlocked": bool(confirmation_layer.get("tp1_already_reached_blocked", False)),
        "chaseBlocked": bool(confirmation_layer.get("chase_blocked", False)),
    }

    learning_scoring = {
        "learningScore": learning_layer.get("final_score") or learning_layer.get("score"),
        "learningAction": learning_layer.get("recommended_action"),
        "profileUsed": learning_layer.get("profile_used"),
        "sampleSize": learning_layer.get("sample_size"),
        "confidenceTier": learning_layer.get("confidence_tier"),
        "historicalWinRate": learning_layer.get("historical_win_rate"),
        "scoreAdjustment": learning_layer.get("score_adjustment"),
        "reason": learning_layer.get("reason"),
    }

    ai_predictive_layer = {
        "enabled": bool(ai_layer.get("model_enabled", ai_layer.get("enabled", False))),
        "modelType": ai_layer.get("model_type") or ai_layer.get("modelType"),
        "modelVersion": ai_layer.get("model_version") or ai_layer.get("modelVersion"),
        "aiLabel": ai_layer.get("ai_label") or ai_layer.get("ai_recommendation"),
        "recommendation": ai_layer.get("ai_recommendation"),
        "tp1Probability": ai_layer.get("tp1_probability"),
        "slProbability": ai_layer.get("sl_probability"),
        "expectedPips": ai_layer.get("expected_pips"),
        "modelConfidence": ai_layer.get("model_confidence"),
        "advisoryOrBlocking": ai_layer.get("advisory_or_blocking", "advisory"),
        "blockingMode": str(ai_layer.get("advisory_or_blocking", "advisory")).lower() == "blocking",
        "wouldBlock": bool(ai_layer.get("would_block", False)),
        "schemaMatch": bool(ai_layer.get("schema_match", True)),
        "missingFeaturesCount": int(ai_layer.get("missing_features_count", 0) or 0),
        "unknownCategoriesCount": int(ai_layer.get("unknown_categories_count", 0) or 0),
        "lastPredictionAt": ai_layer.get("last_prediction_at") or ai_layer.get("last_prediction_timestamp"),
        "reason": ai_layer.get("reason"),
    }

    decision_engine = {
        "finalDecision": decision_layer.get("action") or decision_layer.get("final_decision"),
        "decisionReason": decision_layer.get("reason"),
        "candidateRankScore": decision_layer.get("candidate_rank_score"),
        "setupQualityLabel": decision_layer.get("setup_quality_label"),
        "blockedReason": decision_layer.get("blocked_reason"),
        "alertAllowed": bool(decision_layer.get("alert_allowed", True)),
        "advisoryNotes": decision_layer.get("advisory_notes"),
        "scenarioComplianceStatus": (s.get("scenario_compliance") or {}).get("corrected_status"),
        "scenarioComplianceReason": (s.get("scenario_compliance") or {}).get("reason"),
        "correctedStatus": (s.get("scenario_compliance") or {}).get("corrected_status"),
    }
    level_intelligence = s.get("level_intelligence") or {}

    active_trade_state = s.get("active_trade_state") or {}
    trade_tracking = s.get("trade_tracking") or {}
    first_active_trade = next(iter(active_trade_state.values()), {}) if isinstance(active_trade_state, dict) and active_trade_state else {}
    risk_source = first_active_trade or risk_layer
    risk_trade_management = {
        "activeTrade": first_active_trade or risk_layer.get("active_trade"),
        "activeTrades": active_trade_state,
        "direction": risk_source.get("direction"),
        "entry": risk_source.get("entry"),
        "sl": risk_source.get("virtual_sl") or risk_source.get("sl"),
        "beStatus": "protected" if risk_source.get("protected_after_tp1") else "not_protected",
        "tp1": risk_source.get("tp1"),
        "tp2": risk_source.get("tp2"),
        "tp3": risk_source.get("tp3"),
        "riskPips": risk_layer.get("risk_pips"),
        "tp1RewardPips": risk_layer.get("tp1_reward_pips"),
        "tp1RR": risk_layer.get("tp1_rr"),
        "tp1Hit": bool(risk_source.get("tp1_alert_sent", False) or risk_source.get("current_status") in {"tp1_hit", "tp2_hit", "tp3_hit"}),
        "tp2Hit": bool(risk_source.get("tp2_alert_sent", False) or risk_source.get("current_status") in {"tp2_hit", "tp3_hit"}),
        "tp3Hit": bool(risk_source.get("tp3_alert_sent", False) or risk_source.get("current_status") == "tp3_hit"),
        "protectedAfterTp1": bool(risk_source.get("protected_after_tp1", False)),
        "moveToBEStatus": "protected" if risk_source.get("protected_after_tp1") else risk_layer.get("move_to_be_status"),
        "tradeStatus": risk_source.get("current_status") or risk_source.get("trade_status"),
        "lastTradeManagementAlert": risk_source.get("last_trade_management_alert"),
        "tradeTrackingStartedAt": trade_tracking.get("trade_tracking_started_at"),
        "activeTradesCount": trade_tracking.get("active_trades_count", len(active_trade_state) if isinstance(active_trade_state, dict) else 0),
        "duplicateTradesMergedCount": trade_tracking.get("duplicate_trades_merged_count", 0),
        "recoveredTradesMutedCount": trade_tracking.get("recovered_trades_muted_count", 0),
        "lifecycleAlertsEnabledCount": trade_tracking.get("lifecycle_alerts_enabled_count", 0),
        "lastTpAlertSent": trade_tracking.get("last_tp_alert_sent"),
        "invalidationLevel": risk_source.get("invalidation_level"),
    }

    last_telegram_status = (tg_health or {}).get("last_send_status") or s.get("last_telegram_status")
    telegram_failure_count = int((tg_health or {}).get("failure_count", 0) or 0)
    suppression_status = (
        "silenced"
        if (state.RUNTIME_ALERTS_DISABLED_FLAG.exists() if hasattr(state, "RUNTIME_ALERTS_DISABLED_FLAG") else False)
        else "active"
        if s.get("runtime_alerts_enabled", False)
        else "paused"
    )
    system_health = {
        "mt5Connected": bool(s.get("current_price") is not None and s.get("status") in ("online", "analyzing", "watching")),
        "telegramConnected": bool((tg_health or {}).get("connected", False)),
        "telegramConfigured": bool((tg_health or {}).get("configured", False)),
        "telegramConfigValid": bool((tg_health or {}).get("config_valid", False)),
        "telegramConfigErrors": (tg_health or {}).get("config_errors") or [],
        "telegramConfigWarnings": (tg_health or {}).get("config_warnings") or [],
        "telegramBotUsername": (tg_health or {}).get("bot_username"),
        "telegramLastSendStatus": last_telegram_status,
        "telegramLastAlertType": (tg_health or {}).get("last_alert_type") or s.get("last_telegram_alert_type"),
        "telegramLastError": (tg_health or {}).get("last_error") or s.get("last_telegram_error"),
        "telegramLastSuccessAt": (tg_health or {}).get("last_success_at"),
        "telegramLastFailureAt": (tg_health or {}).get("last_failure_at"),
        "telegramFailureCount": telegram_failure_count,
        "telegramSuccessCount": int((tg_health or {}).get("success_count", 0) or 0),
        "supabaseConnected": bool(state.db_ready),
        "dataFeedStatus": "healthy" if s.get("current_price") is not None else "unavailable",
        "lastError": s.get("last_error") or s.get("error_message"),
        "alertSuppressionStatus": suppression_status,
    }

    developer_diagnostics = {
        "activeInstanceId": s.get("active_instance_id") or s.get("instance_id"),
        "lastScanResult": s.get("last_scan_result"),
        "candidatesFound": s.get("last_candidates_count", 0),
        "rawAlertsSent": s.get("last_alerts_sent", 0),
        "rawAlertsFailed": s.get("last_alerts_failed", 0),
        "rejectReason": s.get("last_reject_reason"),
        "memoryState": s.get("alert_dedupe", {}),
        "duplicateSuppressionKeys": (s.get("alert_dedupe") or {}).get("entry_skipped_duplicate", 0)
            + (s.get("alert_dedupe") or {}).get("watchlist_skipped_duplicate", 0)
            + (s.get("alert_dedupe") or {}).get("market_plan_skipped_duplicate", 0)
            + (s.get("alert_dedupe") or {}).get("scenario_update_skipped_duplicate", 0),
        "totalScans": s.get("total_scans", 0),
        "totalCandidatesFound": s.get("total_candidates_found", 0),
        "totalAlertsSent": s.get("total_alerts_sent", 0),
        "totalAlertsFailed": s.get("total_alerts_failed", 0),
        "totalDuplicatesBlocked": s.get("total_duplicates_blocked", 0),
        "lastScanNumber": s.get("last_scan_number", 0),
        "sessionBlocking": s.get("session_blocking", False),
        "scanAllowed": s.get("scan_allowed", True),
        "backgroundTasksActive": s.get("background_tasks_active", 0),
        "runtimeAlertsEnabled": s.get("runtime_alerts_enabled", False),
        "lastShutdownTime": s.get("last_shutdown_time"),
    }

    return {
        "spencerStatus": spencer_status,
        "marketAnalyst": market_analyst,
        "confirmationEngine": confirmation_engine,
        "learningScoring": learning_scoring,
        "aiPredictiveLayer": ai_predictive_layer,
        "decisionEngine": decision_engine,
        "riskTradeManagement": risk_trade_management,
        "systemHealth": system_health,
        "developerDiagnostics": developer_diagnostics,
    }


def _payload(message: str) -> dict:
    _sync_process_state()
    s = state.bot_state
    return {
        "success":         True,
        "status":          s["status"],
        "message":         message,
        "timestamp":       state.now_iso(),
        # Flat fields kept for backward-compat
        "last_started_at": s["last_started_at"],
        "last_stopped_at": s["last_stopped_at"],
        "strategy_mode":   s["strategy_mode"],
        "symbol":          s["symbol"],
        "session":         s["session"],
        "backend_connected": state.db_ready,
        # Rich data block (matches spec contract)
        "data": {
            "processId":           s["process_id"],
            "startedAt":           s["last_started_at"],
            "stoppedAt":           s["last_stopped_at"],
            "lastHeartbeatAt":     s["last_heartbeat_at"],
            "lastScanAt":          s["last_scan_at"],
            "lastScanResult":      s["last_scan_result"],
            "lastError":           s["last_error"],
            "errorMessage":        s["error_message"],
            "currentSymbol":       s["symbol"],
            "currentSession":      s["session"],
            "marketSession":       s["session"],
            "strategyMode":        s["strategy_mode"],
            "scanAllowed":         s.get("scan_allowed", True),
            "botWindowActive":     s["bot_window_active"],
            "localTime":           s["local_time"],
            "activeUntil":         s["active_until"],
            "currentPrice":        s["current_price"],
            "bid":                 s["bid"],
            "ask":                 s["ask"],
            "spread":              s["spread"],
            "spreadPips":          s["spread_pips"],
            "d1Bias":              s["d1_bias"],
            "h4Bias":              s["h4_bias_detail"],
            "h1Bias":              s["h1_bias"],
            "dominantBias":        s["dominant_bias"],
            "biasStrength":        s["bias_strength"],
            "lastMarketUpdateAt":  s["last_market_update_at"],
            # Scan pipeline summary
            "lastScanSymbol":      s["last_scan_symbol"],
            "lastCandidatesCount": s["last_candidates_count"],
            "lastAlertsSent":      s["last_alerts_sent"],
            "lastAlertsFailed":    s.get("last_alerts_failed", 0),
            "lastRejectReason":    s["last_reject_reason"],
            "lastTelegramStatus":  s["last_telegram_status"],
            "lastTelegramError":   s["last_telegram_error"],
            "lastTelegramAlertType": s.get("last_telegram_alert_type"),
            "lastTelegramAlertTime": s.get("last_telegram_alert_time"),
            "lastScanNumber":      s["last_scan_number"],
            "sessionBlocking":        s["session_blocking"],
            "instanceId":             s["instance_id"],
            "levelsDetected":         s.get("levels_detected", 0),
            "gapLevels":              s.get("gap_levels", 0),
            "biasPassed":             s.get("bias_passed", 0),
            "sweepConfirmed":         s.get("sweep_confirmed", 0),
            "sessionPassed":          s.get("session_passed", 0),
            "distancePassed":         s.get("distance_passed", 0),
            "watchlistCandidates":    s.get("watchlist_candidates", 0),
            "alertsSentThisScan":     s.get("alerts_sent_this_scan", 0),
            "alertsFailedThisScan":   s.get("alerts_failed_this_scan", 0),
            "dedupeRejections":       s.get("dedupe_rejections", 0),
            "totalScans":             s.get("total_scans", 0),
            "totalCandidatesFound":   s.get("total_candidates_found", 0),
            "totalWatchlistCandidates": s.get("total_watchlist_candidates", 0),
            "totalAlertsSent":        s.get("total_alerts_sent", 0),
            "totalAlertsFailed":      s.get("total_alerts_failed", 0),
            "totalDuplicatesBlocked": s.get("total_duplicates_blocked", 0),
            "totalManualAlertsSent":  s.get("total_manual_alerts_sent", 0),
            "totalConfirmationAlertsSent": s.get("total_confirmation_alerts_sent", 0),
            "liveEnabledStrategies":  s.get("live_enabled_strategies"),
            "researchOnlyStrategies": s.get("research_only_strategies"),
            "strategyScans":          s.get("strategy_scans", {}),
            "operatingMode":          s.get("operating_mode", "24_7"),
            "marketPlan":             s.get("market_plan"),
            "priceFeed":              s.get("priceFeed", {}),
            "fiveLayerStatus":        s.get("five_layer_status", {}),
            "activeTradeState":       s.get("active_trade_state", {}),
            "tradeTracking":          s.get("trade_tracking", {}),
            "levelIntelligence":      (s.get("five_layer_status") or {}).get("level_intelligence", s.get("level_intelligence", {})),
            "scenarioCompliance":     s.get("scenario_compliance", {}),
            "aiPrediction":           s.get("ai_prediction", {}),
            "aiPredictiveLayer":      s.get("ai_predictive_layer", {}),
            "activeInstanceId":       s.get("active_instance_id"),
            "botProcessAlive":        s.get("bot_process_alive", False),
            "backgroundTasksActive":  s.get("background_tasks_active", 0),
            "runtimeAlertsEnabled":   s.get("runtime_alerts_enabled", False),
            "lastShutdownTime":       s.get("last_shutdown_time"),
            "alertDedupe":            s.get("alert_dedupe", {}),
            "sessionLiquidity":       (s.get("market_plan") or {}).get("session_liquidity") or s.get("session_liquidity") or {},
            # Spencer v1.1.0 layered status blocks (preferred shape for new
            # frontend cards). Old fields above remain for back-compat and
            # are surfaced by Developer Diagnostics.
            **_structured_blocks(s),
        },
    }


def _send_telegram(method_name: str, *args, **kwargs) -> None:
    """
    Fire a Telegram alert from the API process.
    Used as a safety net so lifecycle alerts reach Telegram regardless of
    whether the bot process handles signals cleanly.
    Failures are swallowed — Telegram must never break the API response.
    """
    try:
        from notifications.telegram_bot import TelegramBot
        tg = TelegramBot()
        getattr(tg, method_name)(*args, **kwargs)
    except Exception as exc:
        logger.warning("Telegram %s failed from API: %s", method_name, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/bot/status")
def bot_status():
    _sync_process_state()
    status_messages = {
        "online":                 "Spencer is online",
        "offline":                "Spencer is offline",
        "starting":               "Spencer is starting",
        "analyzing":              "Spencer is analyzing charts",
        "watching":               "Spencer is watching the market",
        "stopping":               "Spencer is stopping",
        "error":                  "Spencer encountered an error",
        "running_replay":         "Spencer is running replay analysis",
        "tracking_manual_setup":  "Spencer is tracking active setups",
    }
    msg = status_messages.get(
        state.bot_state["status"],
        f"Spencer status: {state.bot_state['status']}"
    )
    if state.bot_state.get("status") in ("watching", "online") and state.bot_state.get("bot_window_active") is False:
        msg = "Spencer online — outside active trading window"
    return _payload(msg)


@router.post("/bot/start")
def start_bot():
    active_pid = _active_process_id()
    if active_pid:
        logger.info("BOT PROCESS ALREADY RUNNING (pid=%s)", active_pid)
        state.bot_state["status"]  = "online"
        state.bot_state["message"] = "Spencer is already running"
        state.bot_state["process_id"] = active_pid
        return _payload("Spencer is already running")

    # Check persistent instance lock (catches stale processes missed by _active_process_id)
    lock_pid = _check_instance_lock()
    if lock_pid:
        logger.warning("SPENCER INSTANCE BLOCKED: another instance is already running pid=%s", lock_pid)
        return _payload(f"Spencer is already running (instance lock pid={lock_pid})")

    # Remove the emergency silence flag so alerts work when the bot starts
    try:
        if state.RUNTIME_ALERTS_DISABLED_FLAG.exists():
            state.RUNTIME_ALERTS_DISABLED_FLAG.unlink()
            logger.info("RUNTIME ALERTS RE-ENABLED: emergency flag removed on start")
    except Exception as exc:
        logger.warning("Failed to remove runtime_alerts_disabled.flag: %s", exc)

    # Clear any stale heartbeat from a previous run
    state.clear_heartbeat()
    try:
        state.RUNTIME_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        state.RUNTIME_LOG_FILE.write_text("", encoding="utf-8")
    except Exception:
        logger.debug("Unable to clear runtime log before start.", exc_info=True)

    state.bot_state["status"]       = "starting"
    state.bot_state["message"]      = "Spencer is starting"
    state.bot_state["error_message"] = None
    state.bot_state["last_error"]   = None
    state.write_runtime_control(
        status="starting",
        active_instance_id=None,
        runtime_alerts_enabled=False,
        shutdown_requested=False,
        last_shutdown_time=state.bot_state.get("last_shutdown_at"),
    )

    python_exe = state.ROOT_DIR / "venv" / "Scripts" / "python.exe"
    command = (
        [str(python_exe), "main.py"]
        if python_exe.exists()
        else [sys.executable, "main.py"]
    )

    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    try:
        state.bot_process = subprocess.Popen(
            command,
            cwd=state.ROOT_DIR,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,          # prevent inheriting uvicorn's port-8000 socket on Windows
            creationflags=creationflags,
        )
    except Exception as exc:
        logger.error("BOT PROCESS FAILED TO SPAWN: %s", exc)
        state.bot_state["status"]        = "error"
        state.bot_state["message"]       = "Spencer failed to start"
        state.bot_state["error_message"] = str(exc)
        return _payload("Spencer failed to start")

    # Give the process a moment to initialise before polling
    time.sleep(1.5)

    if _is_process_running():
        pid = state.bot_process.pid
        state.bot_state["status"]          = "starting"
        state.bot_state["last_started_at"] = state.now_iso()
        state.bot_state["process_id"]      = pid
        state.bot_state["instance_id"]     = str(pid)
        state.bot_state["active_instance_id"] = str(pid)
        state.bot_state["bot_process_alive"] = True
        state.bot_state["background_tasks_active"] = 1
        state.bot_state["runtime_alerts_enabled"] = True
        state.bot_state["message"]         = "Spencer started successfully"
        state.write_runtime_control(
            status="running",
            active_instance_id=str(pid),
            runtime_alerts_enabled=True,
            shutdown_requested=False,
            last_shutdown_time=state.bot_state.get("last_shutdown_time"),
        )
        _write_instance_lock(pid, str(pid))
        logger.info("BOT PROCESS STARTED: pid=%d", pid)
        return _payload("Spencer started successfully")

    logger.error("BOT PROCESS CRASHED immediately after spawn")
    state.bot_state["status"]        = "error"
    state.bot_state["message"]       = "Spencer failed to start"
    state.bot_state["error_message"] = "Process exited immediately after spawn"
    return _payload("Spencer failed to start")


@router.post("/bot/stop")
def stop_bot():
    active_pid = _active_process_id()
    if not active_pid:
        # Might be in error state with no process — normalise to offline
        if state.bot_state["status"] not in ("offline",):
            state.bot_state["status"]  = "offline"
            state.bot_state["message"] = "Spencer is offline"
        logger.info("BOT PROCESS ALREADY OFFLINE")
        return _payload("Spencer is already offline")

    logger.info("BOT STOP REQUESTED: instance_id=%s", state.bot_state.get("instance_id"))
    state.bot_state["status"]  = "stopping"
    state.bot_state["message"] = "Spencer is stopping"

    # Disable runtime alerts BEFORE sending the stop signal — this is the
    # primary barrier preventing the subprocess from firing another alert
    # during the shutdown window.
    logger.info("RUNTIME ALERTS DISABLED BEFORE SHUTDOWN")
    try:
        state.RUNTIME_ALERTS_DISABLED_FLAG.touch()
        logger.info("EMERGENCY SILENCE FLAG CREATED: runtime_alerts_disabled.flag")
    except Exception as exc:
        logger.warning("Failed to create runtime_alerts_disabled.flag: %s", exc)

    state.write_runtime_control(
        status="stopping",
        active_instance_id=state.bot_state.get("instance_id"),
        runtime_alerts_enabled=False,
        shutdown_requested=True,
        last_shutdown_time=state.now_iso(),
    )
    logger.info("SHUTDOWN EVENT SET")
    logger.info("TELEGRAM RUNTIME ALERTS DISABLED")
    if _is_process_running():
        assert state.bot_process is not None
        try:
            if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
                state.bot_process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                state.bot_process.send_signal(signal.SIGTERM)
        except Exception:
            state.bot_process.terminate()

        try:
            state.bot_process.wait(timeout=12)
        except subprocess.TimeoutExpired:
            logger.warning("Bot did not exit gracefully — terminating")
            state.bot_process.terminate()
            logger.info("BOT PROCESS TERMINATED")
            try:
                state.bot_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                state.bot_process.kill()
                logger.info("BOT PROCESS FORCE KILLED")
                state.bot_process.wait(timeout=5)
    elif os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(active_pid), "/T", "/F"], capture_output=True, text=True)
        logger.info("BOT PROCESS FORCE KILLED")
    else:
        try:
            os.kill(active_pid, signal.SIGTERM)
        except Exception:
            logger.warning("Unable to signal heartbeat-only process pid=%s", active_pid, exc_info=True)

    if _pid_exists(active_pid) and os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(active_pid), "/T", "/F"], capture_output=True, text=True)
        logger.info("BOT PROCESS FORCE KILLED")

    pid = active_pid
    state.bot_process = None
    state.clear_heartbeat()
    _remove_instance_lock()

    state.bot_state["status"]          = "offline"
    state.bot_state["last_stopped_at"] = state.now_iso()
    state.bot_state["process_id"]      = None
    state.bot_state["instance_id"]     = None
    state.bot_state["active_instance_id"] = None
    state.bot_state["bot_process_alive"] = False
    state.bot_state["background_tasks_active"] = 0
    state.bot_state["runtime_alerts_enabled"] = False
    state.bot_state["last_shutdown_time"] = state.now_iso()
    state.bot_state["message"]         = "Spencer stopped successfully"
    state.write_runtime_control(
        status="offline",
        active_instance_id=None,
        runtime_alerts_enabled=False,
        shutdown_requested=True,
        last_shutdown_time=state.bot_state["last_shutdown_time"],
    )

    logger.info("BOT PROCESS STOPPED: pid=%s", pid)
    logger.info("BOT STOP COMPLETE: no background loops active")
    # Note: runtime_alerts_disabled.flag intentionally remains until next start
    # so any stale subprocess remnants cannot send Telegram alerts.

    # Send Telegram stop alert from the API side — this fires even when the
    # bot process is hard-killed and its own shutdown handler cannot run.
    # This uses send_bot_stopped_alert which is a lifecycle alert (not guarded).
    _send_telegram("send_bot_stopped_alert")

    return _payload("Spencer stopped successfully")


@router.post("/bot/restart")
def restart_bot():
    was_running = _active_process_id() is not None

    if was_running:
        logger.info("BOT RESTART: stopping current process first")
        stop_bot()

    # Send restart Telegram alert — lifecycle method, not guarded by runtime_alerts_enabled
    _send_telegram("send_restart_alert")

    time.sleep(1)
    return start_bot()


# ─────────────────────────────────────────────────────────────────────────────
# Emergency runtime alert silence / enable
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/bot/silence-runtime-alerts")
def silence_runtime_alerts():
    """
    Instantly block all runtime Telegram alerts by creating the emergency flag file.
    Does NOT stop the bot. Use when alert spam occurs without a full restart.
    """
    try:
        state.RUNTIME_ALERTS_DISABLED_FLAG.touch()
    except Exception as exc:
        logger.warning("Failed to create runtime_alerts_disabled.flag: %s", exc)
        return {"success": False, "message": f"Flag creation failed: {exc}"}
    state.bot_state["runtime_alerts_enabled"] = False
    logger.info("EMERGENCY SILENCE ACTIVATED: runtime_alerts_disabled.flag created")
    return {
        "success": True,
        "message": "Runtime alerts silenced. Flag file created. Alerts will resume after /enable-runtime-alerts or next bot start.",
    }


@router.get("/bot/telegram/health")
def telegram_health(probe: bool = False):
    """Return current Telegram health.

    By default this only reads the cached state file written by the bot
    process — no Telegram API calls are made on every dashboard refresh.
    Pass ``?probe=true`` to actively call ``getMe`` and refresh the state.
    """
    try:
        from notifications.telegram_health import read_health
        if probe:
            from notifications.telegram_bot import check_telegram_connection
            probe_result = check_telegram_connection()
        else:
            probe_result = None
        snapshot = read_health()
        return {
            "success": True,
            "connected": bool(snapshot.get("connected", False)),
            "configured": bool(snapshot.get("configured", False)),
            "configValid": bool(snapshot.get("config_valid", False)),
            "configErrors": snapshot.get("config_errors") or [],
            "configWarnings": snapshot.get("config_warnings") or [],
            "botUsername": snapshot.get("bot_username"),
            "chatIdConfigured": bool(snapshot.get("chat_id_configured", False)),
            "lastSendStatus": snapshot.get("last_send_status"),
            "lastAlertType": snapshot.get("last_alert_type"),
            "lastError": snapshot.get("last_error"),
            "lastSuccessAt": snapshot.get("last_success_at"),
            "lastFailureAt": snapshot.get("last_failure_at"),
            "failureCount": int(snapshot.get("failure_count", 0) or 0),
            "successCount": int(snapshot.get("success_count", 0) or 0),
            "duplicateSuppressedCount": int(snapshot.get("duplicate_suppressed_count", 0) or 0),
            "alertTypeCounts": snapshot.get("alert_type_counts") or {},
            "recentFailures": snapshot.get("recent_failures") or [],
            "updatedAt": snapshot.get("updated_at"),
            "probe": probe_result,
        }
    except Exception as exc:
        logger.warning("TELEGRAM HEALTH READ FAILED: %s", exc)
        return {
            "success": False,
            "connected": False,
            "configured": False,
            "lastError": str(exc),
            "lastSendStatus": "unknown",
        }


@router.post("/bot/enable-runtime-alerts")
def enable_runtime_alerts():
    """
    Remove the emergency silence flag and re-enable runtime alerts if the bot is running.
    Has no effect if the bot is stopped.
    """
    try:
        if state.RUNTIME_ALERTS_DISABLED_FLAG.exists():
            state.RUNTIME_ALERTS_DISABLED_FLAG.unlink()
            logger.info("EMERGENCY SILENCE REMOVED: runtime_alerts_disabled.flag deleted")
    except Exception as exc:
        logger.warning("Failed to remove runtime_alerts_disabled.flag: %s", exc)
        return {"success": False, "message": f"Flag removal failed: {exc}"}

    bot_is_running = state.bot_state.get("status") in ("online", "analyzing", "watching", "running", "starting")
    if bot_is_running:
        state.bot_state["runtime_alerts_enabled"] = True
        state.write_runtime_control(
            status=state.bot_state["status"],
            active_instance_id=state.bot_state.get("active_instance_id"),
            runtime_alerts_enabled=True,
            shutdown_requested=False,
            last_shutdown_time=state.bot_state.get("last_shutdown_time"),
        )
        logger.info("RUNTIME ALERTS RE-ENABLED: bot is running")
        return {"success": True, "message": "Runtime alerts enabled."}

    logger.info("RUNTIME ALERTS FLAG REMOVED: bot is not running — alerts will enable on next start")
    return {
        "success": True,
        "message": "Flag removed. Runtime alerts will re-enable automatically when Spencer starts.",
    }
