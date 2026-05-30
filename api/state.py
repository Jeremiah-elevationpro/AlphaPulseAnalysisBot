"""
Shared application state for the API server.
The Database instance is initialised once at startup and reused across all requests.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from subprocess import Popen
from typing import Optional

from db.database import Database

db = Database()
db_ready = False
ROOT_DIR = Path(__file__).resolve().parents[1]

# File the bot process writes to after every scan — read by the API to surface
# richer status (analyzing / watching) without needing shared memory.
HEARTBEAT_FILE = ROOT_DIR / "bot_heartbeat.json"
RUNTIME_LOG_FILE = ROOT_DIR / "logs" / "spencer_runtime.log"
BOT_RUNTIME_CONTROL_FILE = ROOT_DIR / "bot_runtime_control.json"
RUNTIME_ALERTS_DISABLED_FLAG = ROOT_DIR / "runtime_alerts_disabled.flag"

bot_process: Optional[Popen] = None

bot_state: dict = {
    "status":                 "offline",
    "message":                "Spencer is offline",
    "last_started_at":        None,
    "last_stopped_at":        None,
    "last_heartbeat_at":      None,
    "last_scan_at":           None,
    "last_scan_result":       None,
    "last_error":             None,
    "process_id":             None,
    "strategy_mode":          "Gap + Sweep Reclaim",
    "symbol":                 "XAUUSD",
    "session":                None,
    "error_message":          None,
    # Scan pipeline summary (synced from heartbeat)
    "last_scan_symbol":       "XAUUSD",
    "last_candidates_count":  0,
    "last_alerts_sent":       0,
    "last_alerts_failed":     0,
    "last_reject_reason":     None,
    "last_telegram_status":   None,
    "last_telegram_error":    None,
    "last_telegram_alert_type": None,
    "last_telegram_alert_time": None,
    "last_scan_number":       0,
    "session_blocking":       False,
    "instance_id":            None,
    "scan_allowed":           True,
    "levels_detected":        0,
    "gap_levels":             0,
    "bias_passed":            0,
    "sweep_confirmed":        0,
    "session_passed":         0,
    "distance_passed":        0,
    "watchlist_candidates":   0,
    "alerts_sent_this_scan":  0,
    "alerts_failed_this_scan": 0,
    "dedupe_rejections":      0,
    "total_scans":            0,
    "total_candidates_found": 0,
    "total_watchlist_candidates": 0,
    "total_alerts_sent":      0,
    "total_alerts_failed":    0,
    "total_duplicates_blocked": 0,
    "total_manual_alerts_sent": 0,
    "total_confirmation_alerts_sent": 0,
    "bot_window_active":      None,
    "local_time":             None,
    "active_until":           None,
    "current_price":          None,
    "bid":                    None,
    "ask":                    None,
    "spread":                 None,
    "spread_pips":            None,
    "d1_bias":                None,
    "h4_bias_detail":         None,
    "h1_bias":                None,
    "dominant_bias":          None,
    "bias_strength":          None,
    "last_market_update_at":      None,
    "live_enabled_strategies":    ["gap_liquidity_sweep_reclaim", "engulfing_rejection", "standard_break_retest"],
    "research_only_strategies":   ["failed_engulf_break_retest"],
    "strategy_scans":             {},
    "operating_mode":             "24_7",
    "market_plan":                None,
    "priceFeed":                  {},
    "five_layer_status":          {},
    "active_trade_state":         {},
    "trade_tracking":             {},
    "level_intelligence":         {},
    "scenario_compliance":        {},
    "ai_prediction":              {},
    "ai_predictive_layer":        {},
    "active_instance_id":         None,
    "bot_process_alive":          False,
    "background_tasks_active":    0,
    "runtime_alerts_enabled":     False,
    "last_shutdown_time":         None,
    "alert_dedupe":               {
        "market_plan_skipped_duplicate": 0,
        "scenario_update_skipped_duplicate": 0,
        "entry_skipped_duplicate": 0,
        "watchlist_skipped_duplicate": 0,
        "last_skip_reason": None,
    },
    # Spencer Core Strategy Engine — populated by main.py heartbeat
    "core_strategy_engine":       {},
    "use_legacy_strategies":      False,
    "allowed_strategy_types":     [
        "supply_demand_retest",
        "session_liquidity_sweep_reversal",
        "break_retest_continuation",
    ],
}

replay_runs: dict[int, dict] = {}
latest_replay_run_id: int | None = None
_replay_counter = 1000

manual_setups: list[dict] = []
_setup_counter = 1


def next_replay_run_id() -> int:
    global _replay_counter
    _replay_counter += 1
    return _replay_counter


def next_setup_id() -> int:
    global _setup_counter
    _setup_counter += 1
    return _setup_counter


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_heartbeat() -> Optional[dict]:
    """
    Read the heartbeat JSON written by the bot process.
    Returns None if the file is missing, unreadable, or malformed.
    """
    try:
        if HEARTBEAT_FILE.exists():
            return json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def clear_heartbeat() -> None:
    """Remove the heartbeat file — called on clean stop so stale data isn't shown."""
    try:
        if HEARTBEAT_FILE.exists():
            HEARTBEAT_FILE.unlink()
    except Exception:
        pass


def read_runtime_control() -> dict:
    try:
        if BOT_RUNTIME_CONTROL_FILE.exists():
            return json.loads(BOT_RUNTIME_CONTROL_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "status": "offline",
        "active_instance_id": None,
        "runtime_alerts_enabled": False,
        "shutdown_requested": False,
        "last_shutdown_time": None,
    }


def write_runtime_control(
    *,
    status: str,
    active_instance_id: str | None,
    runtime_alerts_enabled: bool,
    shutdown_requested: bool,
    last_shutdown_time: str | None = None,
) -> None:
    payload = {
        "status": status,
        "active_instance_id": active_instance_id,
        "runtime_alerts_enabled": runtime_alerts_enabled,
        "shutdown_requested": shutdown_requested,
        "last_shutdown_time": last_shutdown_time,
        "updated_at": now_iso(),
    }
    BOT_RUNTIME_CONTROL_FILE.write_text(json.dumps(payload), encoding="utf-8")


def mark_db_failure() -> None:
    """Call when a DB query fails at request time — resets the healthy flag so
    subsequent endpoint guards skip DB calls instead of raising 500."""
    global db_ready
    db_ready = False
