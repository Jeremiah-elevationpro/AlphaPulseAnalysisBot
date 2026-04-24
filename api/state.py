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
    "last_reject_reason":     None,
    "last_telegram_status":   None,
    "last_telegram_error":    None,
    "last_scan_number":       0,
    "session_blocking":       False,
    "instance_id":            None,
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
    "last_market_update_at":  None,
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
