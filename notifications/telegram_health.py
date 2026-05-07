"""Shared Telegram health/state recorder.

Stores last-known Telegram send status in a JSON file so the API server
(running in a different process from the bot) can read it for the
``/api/bot/telegram/health`` endpoint and the dashboard System Health card.

The recorder is safe to call from any process — writes are best-effort and
never raise.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
HEALTH_FILE = ROOT_DIR / "telegram_health.json"

_DEFAULT_STATE: dict[str, Any] = {
    "configured": False,
    "config_valid": False,
    "config_errors": [],
    "config_warnings": [],
    "connected": False,
    "bot_username": None,
    "chat_id_configured": False,
    "last_send_status": "unknown",
    "last_alert_type": None,
    "last_error": None,
    "last_response_text": None,
    "last_success_at": None,
    "last_failure_at": None,
    "failure_count": 0,
    "success_count": 0,
    "duplicate_suppressed_count": 0,
    "alert_type_counts": {},
    "recent_failures": [],
    "updated_at": None,
}

_VALID_STATUSES = {
    "success",
    "send_failed",
    "duplicate_suppressed",
    "config_invalid",
    "disabled",
    "timeout",
    "connection_error",
    "http_error",
    "rate_limited",
    "api_error",
    "unknown",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> dict[str, Any]:
    try:
        if HEALTH_FILE.exists():
            data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged = dict(_DEFAULT_STATE)
                merged.update(data)
                return merged
    except Exception:
        pass
    return dict(_DEFAULT_STATE)


def _write(state: dict[str, Any]) -> None:
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEALTH_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def read_health() -> dict[str, Any]:
    return _read()


def update_health(**fields: Any) -> dict[str, Any]:
    state = _read()
    state.update(fields)
    state["updated_at"] = _now_iso()
    _write(state)
    return state


def record_config(*, valid: bool, errors: list[str], warnings: list[str], chat_id_configured: bool) -> None:
    state = _read()
    state["configured"] = bool(valid)
    state["config_valid"] = bool(valid)
    state["config_errors"] = list(errors or [])
    state["config_warnings"] = list(warnings or [])
    state["chat_id_configured"] = bool(chat_id_configured)
    if not valid:
        state["connected"] = False
        state["last_send_status"] = "config_invalid"
        state["last_error"] = "; ".join(errors) if errors else "config_invalid"
    state["updated_at"] = _now_iso()
    _write(state)


def record_success(alert_type: str, *, bot_username: str | None = None, response_text: str | None = None) -> None:
    state = _read()
    state["connected"] = True
    state["last_send_status"] = "success"
    state["last_alert_type"] = alert_type
    state["last_success_at"] = _now_iso()
    state["success_count"] = int(state.get("success_count", 0) or 0) + 1
    state["last_error"] = None
    state["last_response_text"] = response_text
    counts = dict(state.get("alert_type_counts") or {})
    counts[alert_type] = int(counts.get(alert_type, 0) or 0) + 1
    state["alert_type_counts"] = counts
    if bot_username:
        state["bot_username"] = bot_username
    state["updated_at"] = _now_iso()
    _write(state)


def record_failure(
    alert_type: str,
    *,
    status_code: int | None = None,
    error: str | None = None,
    failure_reason: str = "send_failed",
    response_text: str | None = None,
) -> None:
    if failure_reason not in _VALID_STATUSES:
        failure_reason = "send_failed"
    state = _read()
    state["last_send_status"] = failure_reason
    state["last_alert_type"] = alert_type
    state["last_failure_at"] = _now_iso()
    state["failure_count"] = int(state.get("failure_count", 0) or 0) + 1
    state["last_error"] = error or failure_reason
    state["last_response_text"] = response_text
    state["connected"] = False
    recent = list(state.get("recent_failures") or [])
    recent.insert(
        0,
        {
            "alert_type": alert_type,
            "status_code": status_code,
            "failure_reason": failure_reason,
            "error": error,
            "at": _now_iso(),
        },
    )
    state["recent_failures"] = recent[:20]
    state["updated_at"] = _now_iso()
    _write(state)


def record_duplicate(alert_type: str, dedupe_key: str | None = None) -> None:
    state = _read()
    state["last_send_status"] = "duplicate_suppressed"
    state["last_alert_type"] = alert_type
    state["duplicate_suppressed_count"] = int(state.get("duplicate_suppressed_count", 0) or 0) + 1
    state["updated_at"] = _now_iso()
    _write(state)


def validate_config(token: str | None, chat_id: str | None) -> tuple[bool, list[str], list[str]]:
    """Returns (is_valid, errors, warnings) for the given Telegram credentials."""
    errors: list[str] = []
    warnings: list[str] = []
    token_stripped = (token or "").strip()
    chat_stripped = (chat_id or "").strip()

    if not token_stripped:
        errors.append("missing TELEGRAM_BOT_TOKEN")
    elif not re.match(r"^\d+:[A-Za-z0-9_\-]{30,}$", token_stripped):
        errors.append("TELEGRAM_BOT_TOKEN format invalid (expected '<digits>:<35+ char secret>')")

    if not chat_stripped:
        errors.append("missing TELEGRAM_CHAT_ID")
    elif not re.match(r"^-?\d+$", chat_stripped):
        errors.append(f"TELEGRAM_CHAT_ID must be an integer (got '{chat_stripped}')")
    else:
        # Heuristic: supergroup IDs are 13 digits beginning with 100… and must be negative.
        if chat_stripped.startswith("100") and len(chat_stripped) >= 13 and not chat_stripped.startswith("-"):
            warnings.append(
                f"TELEGRAM_CHAT_ID '{chat_stripped}' looks like a supergroup id missing leading '-'. "
                f"If sends fail with 'chat not found', try '-{chat_stripped}'."
            )

    return (len(errors) == 0, errors, warnings)
