"""
Bot lifecycle control endpoints.

POST /api/bot/start    — spawn main.py as a subprocess
POST /api/bot/stop     — gracefully shut it down + send Telegram stop alert
POST /api/bot/restart  — stop then start
GET  /api/bot/status   — read state; enriched from heartbeat file while running
"""
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
            state.bot_state["last_reject_reason"]    = hb.get("last_reject_reason")
            state.bot_state["last_telegram_status"]  = hb.get("last_telegram_status")
            state.bot_state["last_telegram_error"]   = hb.get("last_telegram_error")
            state.bot_state["last_scan_number"]      = hb.get("last_scan_number", 0)
            state.bot_state["session_blocking"]      = hb.get("session_blocking", False)
            state.bot_state["instance_id"]           = hb.get("instance_id")
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
            state.bot_state["process_id"]            = hb.get("process_id", state.bot_state.get("process_id"))
            if hb.get("last_error"):
                state.bot_state["last_error"] = hb["last_error"]
        else:
            # No heartbeat yet (process just started) — keep starting/online
            if state.bot_state["status"] not in ("starting", "stopping"):
                state.bot_state["status"]  = "online"
                state.bot_state["message"] = "Spencer is online"
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
            "strategyMode":        s["strategy_mode"],
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
            "lastRejectReason":    s["last_reject_reason"],
            "lastTelegramStatus":  s["last_telegram_status"],
            "lastTelegramError":   s["last_telegram_error"],
            "lastScanNumber":      s["last_scan_number"],
            "sessionBlocking":     s["session_blocking"],
            "instanceId":          s["instance_id"],
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

    python_exe = state.ROOT_DIR / "venv" / "Scripts" / "python.exe"
    command = (
        [str(python_exe), "main.py"]
        if python_exe.exists()
        else [sys.executable, "main.py"]
    )

    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        state.bot_process = subprocess.Popen(
            command,
            cwd=state.ROOT_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
        state.bot_state["message"]         = "Spencer started successfully"
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

    state.bot_state["status"]  = "stopping"
    state.bot_state["message"] = "Spencer is stopping"
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
            try:
                state.bot_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                state.bot_process.kill()
                state.bot_process.wait(timeout=5)
    elif os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(active_pid), "/T", "/F"], capture_output=True, text=True)
    else:
        try:
            os.kill(active_pid, signal.SIGTERM)
        except Exception:
            logger.warning("Unable to signal heartbeat-only process pid=%s", active_pid, exc_info=True)

    pid = active_pid
    state.bot_process = None
    state.clear_heartbeat()

    state.bot_state["status"]          = "offline"
    state.bot_state["last_stopped_at"] = state.now_iso()
    state.bot_state["process_id"]      = None
    state.bot_state["instance_id"]     = None
    state.bot_state["message"]         = "Spencer stopped successfully"

    logger.info("BOT PROCESS STOPPED: pid=%s", pid)

    # Send Telegram stop alert from the API side — this fires even when the
    # bot process is hard-killed and its own shutdown handler cannot run.
    _send_telegram("send_bot_stopped_alert")

    return _payload("Spencer stopped successfully")


@router.post("/bot/restart")
def restart_bot():
    was_running = _active_process_id() is not None

    if was_running:
        logger.info("BOT RESTART: stopping current process first")
        stop_bot()

    # Send restart Telegram alert
    _send_telegram("send_system_alert", "🔄 Spencer restarting — AlphaPulse engine restarting now.")

    time.sleep(1)
    return start_bot()
