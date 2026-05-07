"""
AlphaPulse - Telegram Notification System
==========================================
Manual-execution alert architecture — 6 alert types only.

Alert types (in chronological order for any given setup):
  1. STARTUP        — bot online + analyzing charts (two messages)
  2. SETUP_ALERT    — high-quality setup identified (entry zone / SL / TPs)
  3. WATCH_LEVEL    — price actively approaching a key level
  4. CONFIRMATION   — first rejection confirmed; set pending order
  5. TRADE_UPDATE   — TP hit / SL hit / trade completed (simulated tracking)
  6. SHUTDOWN       — bot going offline

Operational error messages (system_alert) are separate and kept minimal.
No automatic execution. No capital management. Pure analysis assistant.
"""

from dataclasses import asdict, dataclass, field
from typing import Optional
from datetime import datetime, timezone
import json
import os
import time
from pathlib import Path

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN as _SETTINGS_TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID as _SETTINGS_TELEGRAM_CHAT_ID,
    TELEGRAM_VERBOSE_MARKET_PLAN,
)


def _resolve_telegram_token() -> str:
    """Read token at call-time so corrections to .env / defaults take effect
    without requiring an API server restart."""
    return (os.getenv("TELEGRAM_BOT_TOKEN") or _SETTINGS_TELEGRAM_BOT_TOKEN or "").strip()


def _resolve_telegram_chat_id() -> str:
    """Read chat id at call-time so corrections to .env / defaults take effect
    without requiring an API server restart."""
    return (os.getenv("TELEGRAM_CHAT_ID") or _SETTINGS_TELEGRAM_CHAT_ID or "").strip()


# Module-level constants kept for backward-compat (some callers still read
# these directly). They reflect the value at import time.
TELEGRAM_BOT_TOKEN = _SETTINGS_TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID = _SETTINGS_TELEGRAM_CHAT_ID
from db.models import Trade
from notifications.telegram_health import (
    record_config,
    record_duplicate,
    record_failure,
    record_success,
    update_health,
    validate_config,
)
from utils.helpers import price_to_pips, trade_direction_emoji
from utils.logger import get_logger, get_runtime_logger
from utils.strategy_registry import canonical_strategy_type, strategy_display_name

logger = get_logger(__name__)
runtime_logger = get_runtime_logger()

# Telegram API limits (sendMessage text max length).
TELEGRAM_MAX_TEXT_LENGTH = 4096
TELEGRAM_TRUNCATION_SUFFIX = "\n... [truncated]"
TELEGRAM_REQUEST_TIMEOUT = 10
# Retry only for transient errors. Backoff in seconds.
TELEGRAM_RETRY_BACKOFF = (2, 5, 10)
# Status codes that warrant a retry (server-side hiccups + rate limit).
TELEGRAM_RETRIABLE_STATUS = {429, 500, 502, 503, 504}
# In-memory dedupe so the wrapper can suppress duplicates by key without
# needing the caller to thread state through. Per-process — that's enough,
# since each process owns its own alert pipeline.
_RECENT_DEDUPE_KEYS: dict[str, float] = {}
_DEDUPE_TTL_SECONDS = 60.0


@dataclass
class TelegramSendResult:
    ok: bool
    alert_type: str
    status_code: Optional[int] = None
    error: Optional[str] = None
    response_text: Optional[str] = None
    sent_at: Optional[str] = None
    failure_reason: Optional[str] = None  # success|send_failed|duplicate_suppressed|config_invalid|disabled|timeout|connection_error|http_error|rate_limited|api_error
    attempts: int = 0
    truncated: bool = False
    message_length: int = 0
    chat_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _record_config_state() -> tuple[bool, list[str], list[str]]:
    token = _resolve_telegram_token()
    chat_id = _resolve_telegram_chat_id()
    valid, errors, warnings = validate_config(token, chat_id)
    record_config(
        valid=valid,
        errors=errors,
        warnings=warnings,
        chat_id_configured=bool(chat_id),
    )
    return valid, errors, warnings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(message: str) -> tuple[str, bool]:
    if len(message) <= TELEGRAM_MAX_TEXT_LENGTH:
        return message, False
    cut = TELEGRAM_MAX_TEXT_LENGTH - len(TELEGRAM_TRUNCATION_SUFFIX)
    return message[:cut] + TELEGRAM_TRUNCATION_SUFFIX, True


def _strip_markdown(message: str) -> str:
    cleaned = message
    for token in ("*", "_", "`"):
        cleaned = cleaned.replace(token, "")
    return cleaned


def _safe_telegram_text(message: str) -> str:
    cleaned = message or ""
    for bad, good in {
        "â€”": "-",
        "Ã¢â‚¬â€": "-",
        "â†’": "->",
        "\u2014": "-",
        "\u2013": "-",
    }.items():
        cleaned = cleaned.replace(bad, good)
    return cleaned


def _classify_response(status_code: int, body: str) -> tuple[str, bool, str]:
    """Returns (failure_reason, retriable, error_message)."""
    body_text = body or ""
    body_lower = body_text.lower()
    if status_code == 429:
        return "rate_limited", True, f"rate_limited: {body_text[:200]}"
    if 500 <= status_code <= 599:
        return "http_error", True, f"http_{status_code}: {body_text[:200]}"
    if status_code == 401:
        return "config_invalid", False, "unauthorized: invalid TELEGRAM_BOT_TOKEN"
    if status_code == 403:
        return "send_failed", False, "forbidden: bot blocked or kicked from chat"
    if status_code == 400 and ("chat not found" in body_lower or "chat_id" in body_lower):
        return "send_failed", False, f"chat_not_found: verify TELEGRAM_CHAT_ID ({body_text[:200]})"
    if status_code == 400:
        return "api_error", False, f"bad_request: {body_text[:200]}"
    return "api_error", False, f"api_{status_code}: {body_text[:200]}"


def _is_duplicate(dedupe_key: Optional[str]) -> bool:
    if not dedupe_key:
        return False
    now = time.time()
    # Drop stale entries
    for key in [k for k, ts in _RECENT_DEDUPE_KEYS.items() if (now - ts) > _DEDUPE_TTL_SECONDS]:
        _RECENT_DEDUPE_KEYS.pop(key, None)
    if dedupe_key in _RECENT_DEDUPE_KEYS:
        return True
    _RECENT_DEDUPE_KEYS[dedupe_key] = now
    return False


def send_telegram_message(
    message: str,
    alert_type: str,
    *,
    dedupe_key: Optional[str] = None,
    priority: str = "normal",
    parse_mode: Optional[str] = "Markdown",
    disable_web_page_preview: bool = True,
    enabled: bool = True,
) -> TelegramSendResult:
    """Centralised Telegram send wrapper.

    Validates config, suppresses duplicates, retries transient failures,
    truncates oversized payloads, falls back to plain text on parse errors,
    and records every outcome in the shared health state.
    """
    sent_at = _now_iso()
    if not enabled:
        record_failure(alert_type, failure_reason="disabled", error="caller disabled telegram delivery")
        runtime_logger.info("TELEGRAM SEND SKIPPED: alert_type=%s reason=disabled", alert_type)
        return TelegramSendResult(
            ok=False,
            alert_type=alert_type,
            failure_reason="disabled",
            error="disabled",
            sent_at=sent_at,
            message_length=len(message or ""),
        )

    if _is_duplicate(dedupe_key):
        record_duplicate(alert_type, dedupe_key)
        runtime_logger.info(
            "TELEGRAM SEND SKIPPED: alert_type=%s reason=duplicate_suppressed dedupe_key=%s",
            alert_type,
            dedupe_key,
        )
        return TelegramSendResult(
            ok=False,
            alert_type=alert_type,
            failure_reason="duplicate_suppressed",
            error="duplicate_suppressed",
            sent_at=sent_at,
            message_length=len(message or ""),
        )

    valid, errors, warnings = _record_config_state()
    if not valid:
        joined = "; ".join(errors)
        runtime_logger.warning("TELEGRAM CONFIG INVALID: %s", joined)
        return TelegramSendResult(
            ok=False,
            alert_type=alert_type,
            failure_reason="config_invalid",
            error=joined,
            sent_at=sent_at,
            message_length=len(message or ""),
        )
    if warnings:
        for warning in warnings:
            runtime_logger.warning("TELEGRAM CONFIG WARNING: %s", warning)

    body, truncated = _truncate(_safe_telegram_text(message or ""))
    if truncated:
        runtime_logger.warning(
            "TELEGRAM PAYLOAD TRUNCATED: alert_type=%s original_length=%d", alert_type, len(message or "")
        )

    api_url = f"https://api.telegram.org/bot{_resolve_telegram_token()}/sendMessage"
    chat_id = _resolve_telegram_chat_id()
    payload: dict = {
        "chat_id": chat_id,
        "text": body,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    last_error: Optional[str] = None
    last_status: Optional[int] = None
    last_body: Optional[str] = None
    attempts = 0
    for attempt_index in range(len(TELEGRAM_RETRY_BACKOFF) + 1):
        attempts += 1
        try:
            resp = requests.post(api_url, json=payload, timeout=TELEGRAM_REQUEST_TIMEOUT)
            last_status = resp.status_code
            last_body = resp.text
            if resp.status_code == 200:
                record_success(alert_type, response_text=resp.text[:200] if resp.text else None)
                runtime_logger.info(
                    "TELEGRAM SEND OK: alert_type=%s chat_id=%s sent_at=%s attempts=%d",
                    alert_type,
                    chat_id,
                    sent_at,
                    attempts,
                )
                return TelegramSendResult(
                    ok=True,
                    alert_type=alert_type,
                    status_code=200,
                    response_text=resp.text[:200] if resp.text else None,
                    sent_at=sent_at,
                    failure_reason="success",
                    attempts=attempts,
                    truncated=truncated,
                    message_length=len(body),
                    chat_id=str(chat_id),
                )

            failure_reason, retriable, error_msg = _classify_response(resp.status_code, resp.text)
            last_error = error_msg

            # Plain-text fallback for Markdown parse errors (one-shot).
            if (
                resp.status_code == 400
                and parse_mode
                and "can't parse entities" in (resp.text or "").lower()
            ):
                runtime_logger.warning("TELEGRAM PARSE ERROR — retrying once in plain text: alert_type=%s", alert_type)
                fallback_payload = dict(payload)
                fallback_payload["text"] = _strip_markdown(body)
                fallback_payload.pop("parse_mode", None)
                try:
                    fb = requests.post(api_url, json=fallback_payload, timeout=TELEGRAM_REQUEST_TIMEOUT)
                    if fb.status_code == 200:
                        record_success(alert_type, response_text=fb.text[:200] if fb.text else None)
                        runtime_logger.info(
                            "TELEGRAM SEND OK: alert_type=%s chat_id=%s sent_at=%s attempts=%d (plain-text fallback)",
                            alert_type,
                            chat_id,
                            sent_at,
                            attempts,
                        )
                        return TelegramSendResult(
                            ok=True,
                            alert_type=alert_type,
                            status_code=200,
                            response_text=fb.text[:200] if fb.text else None,
                            sent_at=sent_at,
                            failure_reason="success",
                            attempts=attempts + 1,
                            truncated=truncated,
                            message_length=len(body),
                            chat_id=str(chat_id),
                        )
                    last_status = fb.status_code
                    last_body = fb.text
                    last_error = f"plain_text_fallback_failed: api_{fb.status_code}: {fb.text[:200]}"
                    failure_reason = "api_error"
                    retriable = False
                except requests.RequestException as exc:
                    last_error = f"plain_text_fallback_exception: {exc}"
                    failure_reason = "send_failed"
                    retriable = False

            if not retriable or attempt_index >= len(TELEGRAM_RETRY_BACKOFF):
                runtime_logger.error(
                    "TELEGRAM SEND FAILED: alert_type=%s status_code=%s failure_reason=%s response_text=%s",
                    alert_type,
                    last_status,
                    failure_reason,
                    (last_body or "")[:200],
                )
                record_failure(
                    alert_type,
                    status_code=last_status,
                    error=last_error,
                    failure_reason=failure_reason,
                    response_text=(last_body or "")[:200] if last_body else None,
                )
                return TelegramSendResult(
                    ok=False,
                    alert_type=alert_type,
                    status_code=last_status,
                    error=last_error,
                    response_text=(last_body or "")[:200] if last_body else None,
                    sent_at=sent_at,
                    failure_reason=failure_reason,
                    attempts=attempts,
                    truncated=truncated,
                    message_length=len(body),
                    chat_id=str(chat_id),
                )

            backoff = TELEGRAM_RETRY_BACKOFF[attempt_index]
            runtime_logger.warning(
                "TELEGRAM SEND RETRY: alert_type=%s attempt=%d reason=%s backoff=%ds",
                alert_type,
                attempts + 1,
                failure_reason,
                backoff,
            )
            time.sleep(backoff)

        except requests.Timeout:
            last_error = "request_timeout"
            if attempt_index >= len(TELEGRAM_RETRY_BACKOFF):
                break
            backoff = TELEGRAM_RETRY_BACKOFF[attempt_index]
            runtime_logger.warning(
                "TELEGRAM SEND RETRY: alert_type=%s attempt=%d reason=timeout backoff=%ds",
                alert_type,
                attempts + 1,
                backoff,
            )
            time.sleep(backoff)
        except requests.ConnectionError as exc:
            last_error = f"connection_error: {exc}"
            if attempt_index >= len(TELEGRAM_RETRY_BACKOFF):
                break
            backoff = TELEGRAM_RETRY_BACKOFF[attempt_index]
            runtime_logger.warning(
                "TELEGRAM SEND RETRY: alert_type=%s attempt=%d reason=connection_error backoff=%ds",
                alert_type,
                attempts + 1,
                backoff,
            )
            time.sleep(backoff)
        except Exception as exc:
            last_error = f"unexpected_exception: {exc}"
            break

    runtime_logger.error(
        "TELEGRAM SEND FAILED: alert_type=%s status_code=%s failure_reason=%s response_text=%s",
        alert_type,
        last_status,
        "send_failed",
        (last_body or last_error or "")[:200],
    )
    failure_reason = (
        "timeout"
        if last_error and "timeout" in last_error
        else "connection_error"
        if last_error and "connection_error" in last_error
        else "send_failed"
    )
    record_failure(
        alert_type,
        status_code=last_status,
        error=last_error,
        failure_reason=failure_reason,
        response_text=(last_body or "")[:200] if last_body else None,
    )
    chat_id_now = _resolve_telegram_chat_id()
    return TelegramSendResult(
        ok=False,
        alert_type=alert_type,
        status_code=last_status,
        error=last_error,
        response_text=(last_body or "")[:200] if last_body else None,
        sent_at=sent_at,
        failure_reason=failure_reason,
        attempts=attempts,
        truncated=truncated,
        message_length=len(body),
        chat_id=str(chat_id_now) if chat_id_now else None,
    )


def check_telegram_connection() -> dict:
    """Validates token + chat id and pings Telegram getMe.

    Used by the health endpoint and by ``TelegramBot.__init__`` so we surface
    a precise status before the first alert is sent. Does NOT send any
    test messages to the chat — only inspects bot identity.
    """
    valid, errors, warnings = _record_config_state()
    chat_id_now = _resolve_telegram_chat_id()
    result = {
        "configured": valid,
        "config_valid": valid,
        "config_errors": errors,
        "config_warnings": warnings,
        "chat_id_configured": bool(chat_id_now),
        "chat_id": (chat_id_now or None),
        "bot_username": None,
        "connected": False,
        "checked_at": _now_iso(),
    }
    if not valid:
        update_health(
            connected=False,
            last_error="; ".join(errors) if errors else "config_invalid",
            last_send_status="config_invalid",
        )
        return result
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{_resolve_telegram_token()}/getMe",
            timeout=TELEGRAM_REQUEST_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json() or {}
            bot_info = (data.get("result") or {}) if data.get("ok") else {}
            username = bot_info.get("username")
            result["connected"] = True
            result["bot_username"] = username
            update_health(
                connected=True,
                bot_username=username,
                last_error=None,
            )
            return result
        result["error"] = f"getMe api_{resp.status_code}: {(resp.text or '')[:200]}"
        update_health(connected=False, last_error=result["error"])
        return result
    except Exception as exc:
        result["error"] = f"getMe exception: {exc}"
        update_health(connected=False, last_error=result["error"])
        return result


# ─────────────────────────────────────────────────────────────────────────────
# Runtime alert gate — reads flag file + control file directly so the guard
# works even when telegram_bot.py runs inside the main.py subprocess (no
# shared in-memory state with the API server).
# ─────────────────────────────────────────────────────────────────────────────

_ROOT_DIR = Path(__file__).resolve().parents[1]
_RUNTIME_ALERTS_DISABLED_FLAG = _ROOT_DIR / "runtime_alerts_disabled.flag"
_RUNTIME_CONTROL_FILE = _ROOT_DIR / "bot_runtime_control.json"

_RUNTIME_ACTIVE_STATUSES = {"starting", "running", "watching", "analyzing"}


def can_send_runtime_alert() -> bool:
    """
    Last-line-of-defense guard inside telegram_bot.py.
    Checked before every runtime/system alert method so no caller can bypass it.
    Lifecycle alerts (startup, stop, crash) call can_send_lifecycle_alert() instead.
    """
    if _RUNTIME_ALERTS_DISABLED_FLAG.exists():
        logger.warning("TELEGRAM RUNTIME ALERT BLOCKED: emergency flag present")
        return False
    try:
        ctrl = json.loads(_RUNTIME_CONTROL_FILE.read_text(encoding="utf-8")) if _RUNTIME_CONTROL_FILE.exists() else {}
    except Exception:
        ctrl = {}
    if ctrl.get("shutdown_requested"):
        logger.warning(
            "TELEGRAM RUNTIME ALERT BLOCKED: shutdown_event_set status=%s", ctrl.get("status")
        )
        return False
    if not ctrl.get("runtime_alerts_enabled", False):
        logger.warning(
            "TELEGRAM RUNTIME ALERT BLOCKED: runtime_alerts_enabled=false status=%s", ctrl.get("status")
        )
        return False
    if ctrl.get("status") not in _RUNTIME_ACTIVE_STATUSES:
        logger.warning(
            "TELEGRAM RUNTIME ALERT BLOCKED: bot_not_running status=%s", ctrl.get("status")
        )
        return False
    return True


def can_send_lifecycle_alert() -> bool:
    """Lifecycle alerts (startup, stop, restart, fatal crash) are always allowed."""
    return True


class TelegramBot:
    """
    Synchronous Telegram message sender.

    Public send_ methods map 1-to-1 with the 5 official alert types plus
    three operational messages (startup, shutdown, system_alert).
    All other methods have been removed to prevent Telegram spam.
    """

    def __init__(self):
        self._token   = TELEGRAM_BOT_TOKEN
        self._chat_id = TELEGRAM_CHAT_ID
        self._enabled = bool(self._token and self._chat_id)
        self._last_error = ""
        self._last_result: Optional[TelegramSendResult] = None

        valid, errors, warnings = _record_config_state()
        if not valid:
            logger.warning("TELEGRAM CONFIG INVALID: %s", "; ".join(errors))
            for err in errors:
                logger.warning("  - %s", err)
            self._enabled = False
        elif warnings:
            for warning in warnings:
                logger.warning("TELEGRAM CONFIG WARNING: %s", warning)
            logger.info("Telegram configured — alerts → chat %s", self._chat_id)
        else:
            logger.info("Telegram ready — alerts → chat %s", self._chat_id)

    # ─────────────────────────────────────────────────────
    # CORE SEND
    # ─────────────────────────────────────────────────────

    def send(self, message: str, parse_mode: Optional[str] = "Markdown", *, alert_type: str = "GENERIC") -> bool:
        """Send a message. Returns True on success.

        Internally delegates to :func:`send_telegram_message` which handles
        config validation, retries, plain-text fallback, payload truncation,
        and shared health-state recording. Kept return type ``bool`` for
        backwards compatibility with existing callers.
        """
        self._last_error = ""
        result = send_telegram_message(message, alert_type, parse_mode=parse_mode)
        self._last_result = result
        if not result.ok:
            self._last_error = result.error or result.failure_reason or "send_failed"
        return bool(result.ok)

    def send_message(self, message: str, alert_type: str, *, parse_mode: Optional[str] = "Markdown", dedupe_key: Optional[str] = None) -> TelegramSendResult:
        """Direct access to the structured send result for new callers."""
        result = send_telegram_message(
            message, alert_type, parse_mode=parse_mode, dedupe_key=dedupe_key
        )
        self._last_result = result
        if not result.ok:
            self._last_error = result.error or result.failure_reason or "send_failed"
        return result

    def _send_logged(self, event_label: str, message: str, parse_mode: Optional[str] = "Markdown") -> bool:
        result = send_telegram_message(message, event_label, parse_mode=parse_mode)
        self._last_result = result
        if not result.ok:
            self._last_error = result.error or result.failure_reason or "send_failed"
        return bool(result.ok)

    @staticmethod
    def _format_api_error(status_code: int, raw_body: str) -> str:
        try:
            parsed = json.loads(raw_body)
            description = parsed.get("description") or raw_body
        except Exception:
            description = raw_body
        return f"api_{status_code}: {description}"

    @staticmethod
    def _strip_markdown(message: str) -> str:
        cleaned = message
        for token in ("*", "_", "`"):
            cleaned = cleaned.replace(token, "")
        return cleaned

    # ─────────────────────────────────────────────────────
    # ALERT TYPE 1 — STARTUP  (two messages)
    # handled in send_startup() below
    # ─────────────────────────────────────────────────────

    # ─────────────────────────────────────────────────────
    # ALERT TYPE 2 — SETUP ALERT
    # ─────────────────────────────────────────────────────

    def send_setup_alert(
        self,
        trade: Trade,
        strategy_name: str = "",
        strategy_score: float = 0.0,
    ) -> bool:
        """
        Fired when a high-quality setup is identified and fully validated.
        Gives the trader full context before the pending-order trigger.
        """
        dir_emoji  = trade_direction_emoji(trade.direction)
        sl_pips    = price_to_pips(abs(trade.entry_price - trade.sl_price))
        model_tag  = self._model_tag(trade)
        score_str  = f"`{strategy_score:.2f}`" if strategy_score > 0 else "_learning..._"

        # TP projection line — show all non-None TPs
        tp_parts = [
            f"TP{i + 1}: `{tp:.2f}`"
            for i, tp in enumerate(trade.tp_levels[:5])
            if tp is not None
        ]
        tp_line = "  ".join(tp_parts) if tp_parts else "_TPs pending_"

        msg = (
            f"🔍 *{trade.direction} SETUP IDENTIFIED — XAUUSD* {dir_emoji}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Strategy:   _{model_tag}_\n"
            f"Entry zone: `{trade.entry_price:.2f}`\n"
            f"Stop Loss:  `{trade.sl_price:.2f}` (-{sl_pips:.0f} pips)\n"
            f"{tp_line}\n"
            f"Confidence: `{trade.confidence * 100:.0f}%` | Score: {score_str}\n\n"
            f"_Price reacting at high-probability zone. Waiting for confirmation._"
        )
        return self._send_logged("SETUP ALERT", msg)

    # ─────────────────────────────────────────────────────
    # ALERT TYPE 3 — WATCH LEVEL (price approaching)
    # ─────────────────────────────────────────────────────

    def _send_watchlist_setup_legacy(
        self,
        level_price: float,
        level_type: str,
        direction: str,
        distance_pips: float,
        timeframe_pair: str,
        current_price: float,
        quality_score: float = 0.0,
        scope: str = "",
        reasons=None,
        is_qm: bool = False,
        is_psychological: bool = False,
        psych_strength: str = "",
    ) -> bool:
        """
        Fired for shortlisted accepted levels before confirmation exists.
        This is the early setup/watchlist stage, not a manual entry trigger.
        """
        direction = direction.upper()
        dir_emoji = trade_direction_emoji(direction)

        if level_type == "A":
            level_tag = "resistance"
        elif level_type == "V":
            level_tag = "support"
        elif level_type == "Gap":
            level_tag = "bearish imbalance" if direction == "SELL" else "bullish imbalance"
        else:
            level_tag = level_type.lower()

        tags = []
        if is_qm:
            tags.append("QM")
        if is_psychological:
            tags.append(f"psych {psych_strength}".strip())
        tag_line = f" | {' + '.join(tags)}" if tags else ""

        reason_items = [str(r) for r in (reasons or []) if r]
        reason_line = "; ".join(reason_items[:4]) or "accepted by elite level filters"
        if len(reason_line) > 220:
            reason_line = reason_line[:217] + "..."

        msg = (
            f"ðŸ”Ž *{direction} SETUP WATCHLIST â€” XAUUSD* {dir_emoji}\n"
            f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
            f"Level:      `{level_price:.2f}` _({level_tag})_{tag_line}\n"
            f"Timeframes: `{timeframe_pair}`\n"
            f"Current:    `{current_price:.2f}` | Distance: `{distance_pips:.1f} pips`\n"
            f"Quality:    `{quality_score:.0f}` | Scope: `{scope or 'selected'}`\n"
            f"Why: {reason_line}\n\n"
            f"_No entry yet. Waiting for price to reach the zone and complete confirmation._"
        )
        msg = (
            f"[WATCHLIST] *{direction} SETUP WATCHLIST - XAUUSD* {dir_emoji}\n"
            f"------------------------------\n"
            f"Level:      `{level_price:.2f}` _({level_tag})_{tag_line}\n"
            f"Timeframes: `{timeframe_pair}`\n"
            f"Current:    `{current_price:.2f}` | Distance: `{distance_pips:.1f} pips`\n"
            f"Quality:    `{quality_score:.0f}` | Scope: `{scope or 'selected'}`\n"
            f"Why: {reason_line}\n\n"
            f"_No entry yet. Waiting for price to reach the zone and complete confirmation._"
        )
        return self._send_logged("WATCHLIST", msg)

    def send_watchlist_setup(
        self,
        level_price: float,
        level_type: str,
        direction: str,
        distance_pips: float,
        timeframe_pair: str,
        current_price: float,
        quality_score: float = 0.0,
        base_quality_score: float = 0.0,
        scope: str = "",
        reasons=None,
        symbol: str = "XAUUSD",
        bias: str = "neutral",
        horizon: str = "intraday",
        confluences=None,
        status: str = "",
        is_qm: bool = False,
        is_psychological: bool = False,
        psych_strength: str = "",
    ) -> bool:
        """
        Fired for shortlisted accepted levels before confirmation exists.
        This is the early setup/watchlist stage, not a manual entry trigger.
        """
        direction = direction.upper()
        dir_emoji = trade_direction_emoji(direction)
        horizon_labels = {
            "swing": "Swing Watch",
            "fast_intraday": "Fast Intraday Watch",
            "intraday": "Intraday Watch",
        }
        horizon_label = horizon_labels.get(horizon, "Intraday Watch")

        if level_type == "A":
            level_tag = "resistance"
        elif level_type == "V":
            level_tag = "support"
        elif level_type == "Gap":
            level_tag = "bearish imbalance" if direction == "SELL" else "bullish imbalance"
        else:
            level_tag = level_type.lower()

        tags = []
        if is_qm:
            tags.append("QM")
        if is_psychological:
            tags.append(f"psych {psych_strength}".strip())
        if scope:
            tags.append(scope)
        tag_line = f" ({', '.join(tags)})" if tags else ""

        if confluences is None:
            confluences = reasons or []
        confluence_items = [str(item) for item in confluences if item][:3]
        confluence_line = " | ".join(confluence_items) or "elite selector pass"
        status = status or "waiting for price to approach"
        score_line = f"`{quality_score:.0f}`"
        if base_quality_score and abs(base_quality_score - quality_score) >= 1:
            score_line = f"`{quality_score:.0f}` adj / `{base_quality_score:.0f}` base"

        msg = (
            f"SPENCER SETUP — GAP SWEEP WATCHLIST\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Level: {level_price:.2f}\n"
            f"Zone: {level_type} {level_tag}{tag_line}\n"
            f"Timeframe: {timeframe_pair}\n"
            f"Session: {horizon_label}\n"
            f"Bias: {bias}\n"
            f"Current Price: {current_price:.2f}\n"
            f"Distance: {distance_pips:.1f}p\n"
            f"Quality: {score_line}\n"
            f"Confluence: {confluence_line}\n"
            f"Status: Watching for liquidity sweep reclaim\n"
            f"Watch Context: {status}"
        )
        return self.send(msg)

    def send_watch_level(
        self,
        level_price: float,
        level_type: str,
        distance_pips: float,
        timeframe_pair: str,
        current_price: float,
        scope: str = "",
        is_qm: bool = False,
    ) -> bool:
        """
        Alert when price is actively approaching a key level.
        Deduplication (one alert per price level) is enforced in main.py.
        """
        side = "SELL" if level_price > current_price else "BUY"
        msg = (
            f"👁 PRICE APPROACHING SPENCER LEVEL - XAUUSD\n\n"
            f"Level: {level_price:.2f}\n"
            f"Plan: {side}\n"
            f"Current Price: {current_price:.2f}\n"
            f"Distance: {distance_pips:.1f} pips\n\n"
            f"I’m watching for confirmation now."
        )
        return self._send_logged("WATCH LEVEL", msg, parse_mode=None)
        side      = "SELL" if level_price > current_price else "BUY"
        level_tag = {"A": "resistance", "V": "support", "Gap": "imbalance"}.get(
            level_type, level_type.lower()
        )
        qm_tag = " ⚡QM" if is_qm else ""

        msg = (
            f"👁 *Price approaching key {side} zone — XAUUSD*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Level:    `{level_price:.2f}`{qm_tag} _({level_tag})_\n"
            f"Distance: `{distance_pips:.1f} pips` away\n"
            f"Current:  `{current_price:.2f}`\n\n"
            f"_Confirmation monitoring active on {timeframe_pair}._"
        )
        return self.send(msg)

    # ─────────────────────────────────────────────────────
    # ALERT TYPE 4 — CONFIRMATION  (PENDING ORDER READY)
    # ─────────────────────────────────────────────────────

    def send_confirmation(self, trade: Trade, strategy_score: float = 0.0) -> bool:
        """
        All conditions confirmed: manual pending-order trigger.

        strategy_score is accepted for compatibility with the existing pipeline.
        """
        confirmation = "clean confirmation at Spencer level"
        if canonical_strategy_type(getattr(trade, "strategy_type", "")) == "gap_liquidity_sweep_reclaim":
            confirmation = "liquidity sweep/reclaim confirmation"
        elif canonical_strategy_type(getattr(trade, "strategy_type", "")) == "standard_break_retest":
            confirmation = "break and retest confirmation"
        elif canonical_strategy_type(getattr(trade, "strategy_type", "")) == "engulfing_rejection":
            confirmation = "engulfing rejection confirmation"
        msg = (
            f"✅ SPENCER ENTRY CONFIRMED - {trade.pair}\n\n"
            f"Direction: {trade.direction}\n"
            f"Entry: {trade.entry_price:.2f}\n"
            f"SL: {trade.sl_price:.2f}\n"
            f"TP1: {trade.tp1:.2f}\n"
            f"TP2: {trade.tp2:.2f}\n"
            f"TP3: {trade.tp3:.2f}\n\n"
            f"Confirmation:\n{confirmation}\n\n"
            f"AI Read:\nAdvisory layer active if available.\n\n"
            f"Action:\nManual execution only."
        )
        return self._send_logged("PENDING ORDER ALERT", msg, parse_mode=None)
        action = trade.direction
        model_tag = self._model_tag(trade)
        tf_pair = f"{trade.higher_tf} → {trade.lower_tf}"
        strategy_key = canonical_strategy_type(getattr(trade, "strategy_type", ""))
        strategy_title = model_tag.upper()
        if action == "BUY":
            confirmation = f"First bearish rejection closed above support ({trade.lower_tf})"
        else:
            confirmation = f"First bullish rejection closed below resistance ({trade.lower_tf})"
        bias = self._bias_storyline_label(trade.h4_bias)
        extra = ""
        if strategy_key == "gap_liquidity_sweep_reclaim":
            extra = (
                f"\nSession: {trade.session_name or 'checking'}"
                f"\nBias: {getattr(trade, 'dominant_bias', trade.h4_bias) or trade.h4_bias}"
                f"/{getattr(trade, 'bias_strength', 'moderate')}"
                f"\nConfirmation Type: liquidity_sweep_reclaim"
                f"\nScore: {float(getattr(trade, 'confirmation_score', 0.0) or 0.0):.0f}"
                f"\nTracking: ✓ Pending retest fill"
            )
        elif strategy_key == "engulfing_rejection":
            extra = (
                f"\nEngulf Zone: {getattr(trade, 'level_price', trade.entry_price):.2f}"
                f"\nBias: {getattr(trade, 'dominant_bias', trade.h4_bias)}/{getattr(trade, 'bias_strength', 'weak')}"
                f"\nSession: {trade.session_name or 'off_session'}"
                f"\nQuality Rejections: {getattr(trade, 'quality_rejection_count', 0)}"
                f"\nStructure Breaks: {getattr(trade, 'structure_break_count', 0)}"
                f"\nConfirmation Path: {getattr(trade, 'confirmation_path', 'combined') or 'combined'}"
                f"\nConfirmation Score: {float(getattr(trade, 'confirmation_score', 0.0) or 0.0):.0f}"
            )
        elif strategy_key == "standard_break_retest":
            extra = (
                f"\nBreak Level: {float(getattr(trade, 'level_price', trade.entry_price) or trade.entry_price):.2f}"
                f"\nRetest Level: {float(getattr(trade, 'level_price', trade.entry_price) or trade.entry_price):.2f}"
                f"\nSession: {trade.session_name or 'off_session'}"
                f"\nBias: {getattr(trade, 'dominant_bias', trade.h4_bias)}/{getattr(trade, 'bias_strength', 'weak')}"
                f"\nConfirmation: close_confirmation"
            )
        learning_context = getattr(trade, "learning_context", "")
        if learning_context:
            extra += f"\nLearning: {learning_context}"
        if getattr(trade, "confluence_with", None):
            extra += f"\nConfluence: {model_tag} + {', '.join(trade.confluence_with).replace('_', ' ').title()}"

        if strategy_key == "gap_liquidity_sweep_reclaim":
            msg = (
                f"SPENCER ENTRY — GAP LIQUIDITY SWEEP RECLAIM\n"
                f"Symbol: {trade.pair}\n"
                f"Direction: {action}\n"
                f"Entry: {trade.entry_price:.2f}\n"
                f"SL: {trade.sl_price:.2f}\n"
                f"TP1: {trade.tp1:.2f}\n"
                f"TP2: {trade.tp2:.2f}\n"
                f"TP3: {trade.tp3:.2f}\n"
                f"Confirmation: liquidity_sweep_reclaim\n"
                f"Session: {trade.session_name or 'off_session'}\n"
                f"Bias: {getattr(trade, 'dominant_bias', trade.h4_bias)}/{getattr(trade, 'bias_strength', 'moderate')}\n"
                f"Learning: {learning_context or 'No strong negative profile'}"
            )
        elif strategy_key == "engulfing_rejection":
            msg = (
                f"SPENCER SETUP — ENGULFING REJECTION\n"
                f"Symbol: {trade.pair}\n"
                f"Direction: {action}\n"
                f"Entry: {trade.entry_price:.2f}\n"
                f"SL: {trade.sl_price:.2f}\n"
                f"TP1: {trade.tp1:.2f}\n"
                f"TP2: {trade.tp2:.2f}\n"
                f"TP3: {trade.tp3:.2f}\n"
                f"Timeframe: {trade.lower_tf}\n"
                f"Session: {trade.session_name or 'off_session'}\n"
                f"Bias: {getattr(trade, 'dominant_bias', trade.h4_bias)}/{getattr(trade, 'bias_strength', 'weak')}\n"
                f"{extra}"
            )
        elif strategy_key == "standard_break_retest":
            msg = (
                f"SPENCER SETUP — BREAK + RETEST\n"
                f"Symbol: {trade.pair}\n"
                f"Direction: {action}\n"
                f"Entry: {trade.entry_price:.2f}\n"
                f"SL: {trade.sl_price:.2f}\n"
                f"TP1: {trade.tp1:.2f}\n"
                f"TP2: {trade.tp2:.2f}\n"
                f"TP3: {trade.tp3:.2f}\n"
                f"Timeframe: {trade.lower_tf}\n"
                f"{extra}"
            )
        else:
            msg = (
                f"🚀 SPENCER LIVE SETUP — {strategy_title}\n\n"
                f"Symbol: {trade.pair}\n"
                f"Direction: {action}\n"
                f"Set Pending Order: {trade.entry_price:.2f}\n"
                f"Stop Loss: {trade.sl_price:.2f}\n\n"
                f"TP1: {trade.tp1:.2f}\n"
                f"TP2: {trade.tp2:.2f}\n"
                f"TP3: {trade.tp3:.2f}\n\n"
                f"Timeframes: {tf_pair}\n"
                f"Setup Type: {model_tag}\n"
                f"Confirmation: {confirmation}\n"
                f"Bias Storyline: {bias}\n"
                f"{extra}\n\n"
                f"Status: Waiting for retest entry"
            )
        return self._send_logged("PENDING ORDER ALERT", msg, parse_mode=None)

    def send_market_plan_alert(self, market_plan) -> bool:
        plan = market_plan.to_dict() if hasattr(market_plan, "to_dict") else dict(market_plan or {})
        if plan.get("targets_source") and plan.get("targets_source") != "structure_tp_engine":
            logger.warning(
                "OLD TARGET PATH BLOCKED: source=%s reason=market_plan_requires_structure_targets",
                plan.get("targets_source"),
            )
            return False
        primary = plan.get("primary_scenario", {}) or {}
        secondary = plan.get("secondary_scenario", {}) or {}
        def _filter_targets(scenario: dict) -> tuple[list[float], list[float]]:
            current = float(plan.get("current_price") or 0.0)
            main_targets: list[float] = []
            micro_levels: list[float] = []
            for raw in (scenario.get("market_plan_targets", scenario.get("targets", [])) or []):
                try:
                    level = float(raw)
                except Exception:
                    continue
                distance_pips = price_to_pips(abs(level - current))
                if current and distance_pips < 10:
                    micro_levels.append(level)
                    logger.info(
                        "MARKET PLAN TARGET FILTERED: level=%.2f current_price=%.2f distance_pips=%.1f reason=too_close_to_current_price",
                        level, current, distance_pips,
                    )
                else:
                    main_targets.append(level)
            return main_targets[:5], micro_levels[:3]

        primary_targets, primary_micro = _filter_targets(primary)
        secondary_targets, secondary_micro = _filter_targets(secondary)
        primary_micro_line = f"Reaction/Micro Level: {' / '.join(f'{v:.2f}' for v in primary_micro)}\n" if primary_micro else ""
        secondary_micro_line = f"Reaction/Micro Level: {' / '.join(f'{v:.2f}' for v in secondary_micro)}\n" if secondary_micro else ""
        if not TELEGRAM_VERBOSE_MARKET_PLAN:
            def _first_level(scenario: dict) -> float:
                for key in ("level", "watch_low", "watch_high", "invalidation_level"):
                    try:
                        value = float(scenario.get(key) or 0.0)
                        if value:
                            return value
                    except Exception:
                        pass
                zone = str(scenario.get("watch_zone") or "")
                for part in zone.replace("-", " ").split():
                    try:
                        return float(part)
                    except Exception:
                        continue
                return float(plan.get("current_price") or 0.0)

            def _sl_for(scenario: dict, direction: str) -> float:
                try:
                    value = float(scenario.get("invalidation_level") or 0.0)
                    if value:
                        return value
                except Exception:
                    pass
                low = float(scenario.get("watch_low") or _first_level(scenario))
                high = float(scenario.get("watch_high") or low)
                return low if direction == "BUY" else high

            def _targets(targets: list[float], direction: str, level: float) -> list[float]:
                clean = [float(v) for v in targets if (float(v) > level if direction == "BUY" else float(v) < level)]
                fallback = [level + 20, level + 40, level + 60] if direction == "BUY" else [level - 20, level - 40, level - 60]
                return (clean + fallback)[:3]

            def _expectation(direction: str, level: float, scenario: dict) -> str:
                text = " ".join(str(v) for v in scenario.get("trigger_conditions", [])).lower()
                if "sweep" in text and direction == "SELL":
                    return f"I expect price to sweep buy-side liquidity into {level:.2f} and reject for a SELL."
                if "sweep" in text and direction == "BUY":
                    return f"I expect price to sweep sell-side liquidity into {level:.2f} and reclaim for a BUY."
                if direction == "BUY":
                    return f"I expect price to retest/reclaim around {level:.2f} and BUY from there."
                return f"I expect price to retest/reject around {level:.2f} and SELL from there."

            symbol = plan.get("symbol", "XAUUSD")
            p_dir = str(primary.get("direction") or "WAIT").upper()
            p_level = _first_level(primary)
            p_sl = _sl_for(primary, p_dir)
            p_tps = _targets(primary_targets, p_dir, p_level)
            confidence = primary.get("confidence") or plan.get("bias_strength") or ""
            confidence_line = f"\nConfidence: {str(confidence).upper()}" if confidence else ""

            s_dir = str(secondary.get("direction") or ("SELL" if p_dir == "BUY" else "BUY")).upper()
            s_level = _first_level(secondary or primary)
            s_sl = _sl_for(secondary or primary, s_dir)
            s_targets = _targets(secondary_targets, s_dir, s_level)
            hold_word = "holds above it" if s_dir == "BUY" else "breaks below it"
            retest_side = "as support" if s_dir == "BUY" else "as resistance"

            msg = (
                f"🟡 SPENCER PRIMARY SETUP - {symbol}\n\n"
                f"My best level: {p_level:.2f}\n\n"
                f"Main expectation:\n{_expectation(p_dir, p_level, primary)}\n\n"
                f"Entry plan:\n{p_dir} only after confirmation at {p_level:.2f}\n\n"
                f"SL: {p_sl:.2f}\n"
                f"TP1: {p_tps[0]:.2f}\n"
                f"TP2: {p_tps[1]:.2f}\n"
                f"TP3: {p_tps[2]:.2f}\n\n"
                f"Status:\nWaiting for price to reach the level and confirm."
                f"{confidence_line}\n\n"
                f"🔵 SPENCER ALTERNATIVE PLAN - {symbol}\n\n"
                f"If {p_level:.2f} fails and price {hold_word}:\n\n"
                f"Wait for retest of {s_level:.2f} {retest_side}.\n\n"
                f"Continuation plan:\n{s_dir} after clean retest confirmation.\n\n"
                f"SL: {s_sl:.2f}\n"
                f"TP1: {s_targets[0]:.2f}\n"
                f"TP2: {s_targets[1]:.2f}\n"
                f"TP3: {s_targets[2]:.2f}\n\n"
                f"Status:\nOnly valid if price confirms above/below {p_level:.2f}."
            )
            return self._send_logged("PRIMARY SETUP", msg, parse_mode=None)
        level_intel = dict(plan.get("level_intelligence") or {})
        strongest_resistance = dict(level_intel.get("strongest_resistance") or {})
        strongest_support = dict(level_intel.get("strongest_support") or {})
        consumed_levels = list(level_intel.get("consumed_levels") or [])
        downside_targets = list(level_intel.get("downside_valid_targets") or [])
        upside_targets = list(level_intel.get("upside_reclaim_targets") or [])
        reaction_levels = list(level_intel.get("reaction_micro_levels") or [])
        # Back-compat: if the engine hasn't been refreshed yet, fall back to
        # the legacy mixed list and split it here using the current price.
        if not downside_targets and not upside_targets:
            from analysis.scenario_classifier import directional_targets

            current_price_value = float(plan.get("current_price") or 0.0)
            split = directional_targets(
                level_intel.get("next_valid_targets") or [],
                current_price=current_price_value,
                primary_direction=str(primary.get("direction") or ""),
            )
            downside_targets = split.get("downside_valid_targets", [])
            upside_targets = split.get("upside_reclaim_targets", [])
            reaction_levels = split.get("reaction_micro_levels", [])

        def _fmt_level(row: dict) -> str:
            if not row:
                return "n/a"
            return (
                f"{float(row.get('level') or 0.0):.2f} - "
                f"{row.get('quality_label', 'n/a')} - {row.get('state', 'n/a')} - "
                f"{row.get('evidence_summary', 'n/a')}"
            )

        def _fmt_levels(rows: list[dict]) -> str:
            return " / ".join(f"{float(v.get('level') or 0.0):.2f}" for v in rows[:5]) or "n/a"

        consumed_text = ", ".join(f"{float(v.get('level') or 0.0):.2f}" for v in consumed_levels[:4]) or "none"
        primary_direction_str = str(primary.get("direction") or "").upper()
        if primary_direction_str == "BUY":
            upside_label = "Upside Valid Targets"
            downside_label = "Downside Risk/Invalidation Levels"
        else:
            upside_label = "Upside Reclaim Targets"
            downside_label = "Downside Valid Targets"

        level_section = (
            "\n\nLevel Intelligence:\n"
            f"Strongest Resistance: {_fmt_level(strongest_resistance)}\n"
            f"Strongest Support: {_fmt_level(strongest_support)}\n"
            f"Consumed Levels: {consumed_text}\n"
            f"{downside_label}: {_fmt_levels(downside_targets)}\n"
            f"{upside_label}: {_fmt_levels(upside_targets)}"
        )
        if reaction_levels:
            level_section += f"\nReaction/Micro Levels: {_fmt_levels(reaction_levels)}"

        # Session Liquidity Intelligence (additive advisory layer)
        try:
            from analysis.session_liquidity import format_session_liquidity_section

            session_liquidity_block = format_session_liquidity_section(plan.get("session_liquidity") or {})
        except Exception:
            session_liquidity_block = ""
        if session_liquidity_block:
            level_section += "\n\n" + session_liquidity_block
        deep = dict(plan.get("deep_context_levels") or {})
        deep_support_text = " / ".join(f"{float(v.get('level') or 0.0):.2f}" for v in (deep.get("deep_context_supports") or [])[:4]) or "none"
        deep_resistance_text = " / ".join(f"{float(v.get('level') or 0.0):.2f}" for v in (deep.get("deep_context_resistances") or [])[:4]) or "none"
        active_title = "Active Intraday Scenario" if primary.get("scenario_status") != "no_active_intraday" else "Active Intraday Scenario: No active intraday scenario yet"
        active_line = (
            f"{primary.get('direction', '?')} continuation retest of {primary.get('watch_zone', 'n/a')}"
            if primary.get("scenario_status") != "no_active_intraday"
            else "No active intraday scenario yet. Deep levels remain context only."
        )
        active_confluence = " + ".join(str(v) for v in (primary.get("confluence") or [])[:6] if v)
        active_confluence_line = f"Confluence: {active_confluence}\n" if active_confluence else ""
        msg = (
            f"SPENCER MARKET PLAN — {plan.get('symbol', 'XAUUSD')}\n\n"
            f"Current Price: {float(plan.get('current_price') or 0.0):.2f}\n\n"
            f"H4 Context:\n{plan.get('h4_context', 'Unavailable')}\n\n"
            f"H1 Context:\n{plan.get('h1_context', 'Unavailable')}\n\n"
            f"M15 Context:\n{plan.get('m15_context', 'Unavailable')}\n\n"
            f"Dominant Bias: {plan.get('dominant_bias', 'neutral')} ({plan.get('bias_strength', 'weak')})\n\n"
            f"{active_title}:\n"
            f"{active_line}\n"
            f"{active_confluence_line}"
            f"Type: {primary.get('scenario_type', 'active_continuation_retest')}\n"
            f"Reason: {primary.get('reason', 'n/a')}\n"
            f"Trigger: {'; '.join(primary.get('trigger_conditions', [])[:4])}\n"
            f"{primary_micro_line}"
            f"Targets: {' / '.join(f'{v:.2f}' for v in primary_targets)}\n"
            f"Invalidation: {primary.get('invalidation', 'n/a')}\n\n"
            f"Deep Context Levels:\n"
            f"Deep Context Support: {deep_support_text}\n"
            f"Deep Context Resistance: {deep_resistance_text}\n\n"
            f"Secondary Scenario:\n"
            f"{secondary.get('direction', '?')} {secondary.get('watch_zone', 'n/a')}\n"
            f"Trigger: {'; '.join(secondary.get('trigger_conditions', [])[:4])}\n"
            f"{secondary_micro_line}"
            f"Targets: {' / '.join(f'{v:.2f}' for v in secondary_targets)}\n"
            f"Invalidation: {secondary.get('invalidation', 'n/a')}\n\n"
            f"Watching: {', '.join(plan.get('confirmation_waiting_for', [])[:6])}\n"
            f"Psychological Levels: {', '.join(f'{float(v):.2f}' for v in (plan.get('actionable_psych_levels') or plan.get('psychological_levels') or [])[:12])}\n"
            f"Key Supports: {', '.join(f'{float(v):.2f}' for v in plan.get('key_supports', [])[:4])}\n"
            f"Key Resistances: {', '.join(f'{float(v):.2f}' for v in plan.get('key_resistances', [])[:4])}"
            f"{level_section}"
        )
        return self._send_logged("MARKET PLAN", msg, parse_mode=None)

    def _format_ai_advisory_section(self, setup: dict) -> str:
        ai = dict(setup.get("ai_prediction") or {})
        if not ai:
            return (
                "AI Read: AI-ALLOWED SETUP\n"
                "AI Mode: Advisory only\n"
            )

        label = str(ai.get("ai_label") or setup.get("ai_label") or "AI-ALLOWED SETUP")
        tp1 = float(ai.get("tp1_probability") or 0.0) * 100.0
        sl = float(ai.get("sl_probability") or 0.0) * 100.0
        expected = float(ai.get("expected_pips") or 0.0)
        mode = str(ai.get("advisory_or_blocking") or "advisory").lower()
        mode_label = "Blocking" if mode == "blocking" else "Advisory only"
        would_block = bool(ai.get("would_block") or setup.get("ai_would_block"))
        lines = [
            f"AI Read: {label}",
            f"AI TP1 Probability: {tp1:.0f}%",
            f"AI SL Risk: {sl:.0f}%",
            f"AI Expected Pips: {expected:+.1f}",
            f"AI Mode: {mode_label}",
        ]
        if would_block:
            lines.append("AI Would Block: yes (advisory only)")
        if label in {"AI-CAUTION SETUP", "AI-WOULD-BLOCK SETUP"} or would_block:
            lines.append("Note: Rule-based setup is valid, but AI advises caution. Use manual discretion.")
        return "\n".join(lines) + "\n"

    def send_analyst_entry_alert(self, setup: dict) -> bool:
        ai = dict(setup.get("ai_prediction") or {})
        ai_label = str(ai.get("ai_label") or setup.get("ai_label") or "AI-ALLOWED SETUP")
        tp1_prob = ai.get("tp1_probability")
        ai_line = ai_label
        if tp1_prob is not None:
            try:
                ai_line = f"{ai_label} ({float(tp1_prob) * 100:.0f}% TP1)"
            except Exception:
                pass
        direction = str(setup.get("direction") or "?").upper()
        high_risk = ai_label in {"AI-CAUTION SETUP", "AI-WOULD-BLOCK SETUP"} or bool(ai.get("would_block"))
        title = "⚠️ SPENCER HIGH-RISK SETUP - MANUAL REVIEW" if high_risk else "✅ SPENCER ENTRY CONFIRMED - XAUUSD"
        suggested_tps = setup.get("suggested_tps", {}) or {}
        msg = (
            f"{title}\n\n"
            f"Direction: {direction}\n"
            f"Entry: {float(setup.get('suggested_entry') or setup.get('entry') or 0.0):.2f}\n"
            f"SL: {float(setup.get('suggested_sl') or setup.get('sl') or 0.0):.2f}\n"
            f"TP1: {float(suggested_tps.get('tp1') or setup.get('tp1') or 0.0):.2f}\n"
            f"TP2: {float(suggested_tps.get('tp2') or setup.get('tp2') or 0.0):.2f}\n"
            f"TP3: {float(suggested_tps.get('tp3') or setup.get('tp3') or 0.0):.2f}\n\n"
            f"Confirmation:\n{setup.get('confirmation_type', setup.get('reason', 'clean confirmation at Spencer level'))}\n\n"
            f"AI Read:\n{ai_line}\n\n"
            f"Action:\n{'Manual discretion required.' if high_risk else 'Manual execution only.'}"
        )
        return self._send_logged("ANALYST ENTRY", msg, parse_mode=None)
        target_roles = dict(setup.get("target_roles", {}) or {})
        reaction_level = float(setup.get("reaction_level") or 0.0)
        reaction_line = f"Reaction Level: {reaction_level:.2f}\n" if reaction_level else ""
        ai_section = self._format_ai_advisory_section(setup)
        level_intel = dict(setup.get("level_intelligence") or {})
        level_scores = list(level_intel.get("level_scores") or [])
        entry_price = float(setup.get("entry") or setup.get("suggested_entry") or 0.0)
        tp1_price = float(setup.get("tp1") or 0.0)

        def _nearest_score(level: float) -> dict:
            if not level_scores:
                return {}
            return dict(min(level_scores, key=lambda row: abs(float(row.get("level") or 0.0) - level)))

        entry_score = _nearest_score(entry_price)
        tp1_score = _nearest_score(tp1_price)
        level_section = ""
        if level_scores:
            level_section = (
                "Level Intelligence:\n"
                f"Entry Level Score: {float(entry_score.get('score') or 0.0):.0f} / {entry_score.get('quality_label', 'n/a')}\n"
                f"TP1 Level Score: {float(tp1_score.get('score') or 0.0):.0f} / {tp1_score.get('quality_label', 'n/a')}\n"
                f"Level State: {entry_score.get('state', 'n/a')}\n"
                f"Evidence: {entry_score.get('evidence_summary', 'n/a')}\n\n"
            )
        compliance = dict(setup.get("scenario_compliance") or {})
        compliance_line = (
            f"Scenario Compliance: {compliance.get('corrected_status', 'actionable')} - {compliance.get('reason', 'pass')}\n"
            if compliance else ""
        )
        # Session Liquidity entry section (advisory)
        liquidity_setup = setup.get("session_liquidity_setup") or {}
        try:
            from analysis.session_liquidity import format_session_liquidity_entry_section

            liquidity_block = format_session_liquidity_entry_section(liquidity_setup)
        except Exception:
            liquidity_block = ""
        liquidity_section = (liquidity_block + "\n\n") if liquidity_block else ""
        direction = str(setup.get("direction", "?")).upper()
        ai_label = str(setup.get("ai_label") or (setup.get("ai_prediction") or {}).get("ai_label") or "AI-ALLOWED SETUP")
        ai_would_block = bool(setup.get("ai_would_block") or (setup.get("ai_prediction") or {}).get("would_block"))
        if ai_label in {"AI-CAUTION SETUP", "AI-WOULD-BLOCK SETUP"} or ai_would_block:
            title = "SPENCER HIGH-RISK SETUP - AI CAUTION"
            logger.info("AI CAUTION ALERT DOWNGRADED: ai_label=%s title=%s", ai_label, title)
        elif ai_label == "AI-ALLOWED SETUP":
            title = "SPENCER ENTRY CONFIRMED - AI ALLOWED"
        else:
            title = f"SPENCER ENTRY CONFIRMED - {direction} GOLD"
        msg = (
            f"SPENCER ENTRY CONFIRMED â€” {setup.get('direction', '?')} GOLD\n\n"
            f"Quality: {setup.get('setup_quality_label', 'QUALITY SETUP')}\n"
            f"{ai_section}"
            f"{liquidity_section}"
            f"Score: {float(setup.get('candidate_rank_score') or setup.get('priority') or 0.0):.0f}\n"
            f"Scenario: {setup.get('scenario', 'primary')}\n"
            f"{compliance_line}"
            f"Confirmation: {setup.get('confirmation_type', 'unknown')}\n"
            f"Grade: {setup.get('confirmation_grade', setup.get('grade', '?'))}\n\n"
            f"Entry: {float(setup.get('entry') or setup.get('suggested_entry') or 0.0):.2f}\n"
            f"SL: {float(setup.get('sl') or setup.get('suggested_sl') or 0.0):.2f}\n"
            f"Invalidation: {setup.get('invalidation', 'n/a')}\n\n"
            f"{reaction_line}"
            f"TP1: {float(setup.get('tp1') or 0.0):.2f} — main target / move SL to BE\n"
            f"TP2: {float(setup.get('tp2') or 0.0):.2f} — runner target\n"
            f"TP3: {float(setup.get('tp3') or 0.0):.2f} — extended runner / major target\n\n"
            f"Risk: {float(setup.get('risk_pips') or 0.0):.0f} pips\n"
            f"TP1 RR: {float(setup.get('tp1_rr') or 0.0):.2f}R\n"
            f"TP2 RR: {float(setup.get('tp2_rr') or 0.0):.2f}R\n"
            f"TP3 RR: {float(setup.get('tp3_rr') or 0.0):.2f}R\n\n"
            f"{level_section}"
            f"Why:\n{setup.get('trade_path_rationale', setup.get('entry_reason', 'Quality confirmation at active watch zone.'))}\n\n"
            f"SL Rationale: {setup.get('sl_rationale', 'structure-based')}\n"
            f"TP Rationale: {setup.get('tp_rationale', 'structure-based')}\n"
            f"Target Roles: {target_roles}"
        )
        msg_lines = msg.splitlines()
        msg = "\n".join([title, ""] + msg_lines[2:])
        return self._send_logged("ANALYST ENTRY", msg, parse_mode=None)
        suggested_tps = setup.get("suggested_tps", {}) or {}
        msg = (
            f"SPENCER ENTRY CONFIRMED — {setup.get('direction', '?')} GOLD\n\n"
            f"Reason:\n{setup.get('reason', 'Quality confirmation at active watch zone.')}\n\n"
            f"Confirmation:\n{setup.get('confirmation_type', 'unknown')}\n"
            f"Grade: {setup.get('grade', '?')}\n\n"
            f"Entry: {float(setup.get('suggested_entry') or 0.0):.2f}\n"
            f"SL: {float(setup.get('suggested_sl') or 0.0):.2f}\n"
            f"TP1: {float(suggested_tps.get('tp1') or 0.0):.2f}\n"
            f"TP2: {float(suggested_tps.get('tp2') or 0.0):.2f}\n"
            f"TP3: {float(suggested_tps.get('tp3') or 0.0):.2f}\n\n"
            f"Scenario: {setup.get('scenario', 'primary')}\n"
            f"Invalidation: {setup.get('invalidation', 'n/a')}\n"
            f"TP Rationale: {setup.get('tp_rationale', 'structure-based')}\n"
            f"SL Rationale: {setup.get('sl_rationale', 'structure-based')}"
        )
        return self._send_logged("ANALYST ENTRY", msg, parse_mode=None)

    def send_scenario_manual_review_alert(self, payload: dict) -> bool:
        details = dict(payload.get("details") or {})
        scenario_zone = details.get("reclaim_zone") or details.get("breakdown_zone") or "scenario zone"
        msg = (
            "SPENCER MANUAL REVIEW ONLY - SCENARIO NOT CONFIRMED\n\n"
            f"Symbol: {payload.get('symbol', 'XAUUSD')}\n"
            f"Direction: {str(payload.get('direction', '?')).upper()}\n"
            f"Entry Considered: {float(payload.get('entry') or 0.0):.2f}\n\n"
            f"Reason:\n{payload.get('reason', 'scenario_not_confirmed')}\n"
            f"Scenario Zone: {scenario_zone}\n"
            f"Primary: {payload.get('primary', 'n/a')}\n"
            f"Secondary: {payload.get('secondary', 'n/a')}\n\n"
            "Waiting For:\n"
            f"{payload.get('waiting_for', 'Fresh close/retest confirmation at scenario zone.')}\n\n"
            "Status: No actionable entry alert. No active trade tracking registered."
        )
        return self._send_logged("SCENARIO MANUAL REVIEW", msg, parse_mode=None)

    def send_scenario_update_alert(self, update: dict) -> bool:
        title = str(update.get("title") or f"SPENCER SCENARIO UPDATE - {update.get('symbol', 'XAUUSD')}")
        change_type = str(update.get("change_type") or "scenario_update")
        body = str(update.get("message") or "Scenario updated.")
        msg = (
            f"{title}\n\n"
            f"{body}\n\n"
            f"Primary: {update.get('primary', 'n/a')}\n"
            f"Secondary: {update.get('secondary', 'n/a')}\n"
            f"Waiting For: {update.get('waiting_for', 'n/a')}"
        )
        alert_label = f"SCENARIO UPDATE ({change_type})" if change_type else "SCENARIO UPDATE"
        return self._send_logged(alert_label, msg, parse_mode=None)

    def send_tp1_be_alert(self, trade_state: dict, current_price: float) -> bool:
        msg = (
            f"🎯 TP1 HIT - {trade_state.get('symbol', 'XAUUSD')}\n\n"
            f"Direction: {trade_state.get('direction')}\n"
            f"Entry: {float(trade_state.get('entry') or 0.0):.2f}\n"
            f"TP1: {float(trade_state.get('tp1') or 0.0):.2f}\n"
            f"Current: {current_price:.2f}\n\n"
            f"Action:\nMove SL to BE."
        )
        return self._send_logged("TP1_BE", msg, parse_mode=None)

    def send_tp2_alert(self, trade_state: dict, current_price: float) -> bool:
        msg = (
            f"🎯 TP2 HIT - {trade_state.get('symbol', 'XAUUSD')}\n\n"
            f"Direction: {trade_state.get('direction')}\n"
            f"TP2: {float(trade_state.get('tp2') or 0.0):.2f}\n"
            f"Current: {current_price:.2f}\n\n"
            f"Action:\nSecure partials / trail runner."
        )
        return self._send_logged("TP2", msg, parse_mode=None)

    def send_tp3_alert(self, trade_state: dict, current_price: float) -> bool:
        msg = (
            f"🏁 TP3 HIT - {trade_state.get('symbol', 'XAUUSD')}\n\n"
            f"Direction: {trade_state.get('direction')}\n"
            f"TP3: {float(trade_state.get('tp3') or 0.0):.2f}\n"
            f"Result: Strong win."
        )
        return self._send_logged("TP3", msg, parse_mode=None)

    def send_breakeven_exit_alert(self, trade_state: dict) -> bool:
        msg = (
            f"🛡️ BREAKEVEN EXIT - {trade_state.get('symbol', 'XAUUSD')}\n\n"
            f"TP1 was reached.\n"
            f"Trade closed protected."
        )
        return self._send_logged("BE", msg, parse_mode=None)

    def send_sl_before_tp1_alert(self, trade_state: dict) -> bool:
        msg = (
            f"❌ SL HIT - {trade_state.get('symbol', 'XAUUSD')}\n\n"
            f"Direction: {trade_state.get('direction')}\n"
            f"SL: {float(trade_state.get('sl') or 0.0):.2f}\n"
            f"Result: Loss."
        )
        return self._send_logged("SL", msg, parse_mode=None)

    # ─────────────────────────────────────────────────────
    # INTERNAL — trade registered for simulated tracking
    # (NOT sent to Telegram — confirmation already covers this)
    # ─────────────────────────────────────────────────────

    def send_trade_executed(self, trade: Trade) -> bool:
        """
        Silenced — no Telegram message.
        The confirmation alert already told the trader to place the pending order.
        Method kept so trade_tracker.py compile path remains valid.
        """
        logger.debug(
            "Trade activated for tracking: %s %s @ %.2f (no Telegram — pending alert already sent)",
            trade.direction, trade.pair, trade.entry_price,
        )
        runtime_logger.info(
            "TELEGRAM TRADE ACTIVATED ALERT SEND SUCCESS: tracking only | %s %s @ %.2f",
            trade.direction,
            trade.pair,
            trade.entry_price,
        )
        return True

    # ─────────────────────────────────────────────────────
    # ALERT TYPE 5 — TRADE UPDATE  (simulated price tracking)
    # ─────────────────────────────────────────────────────

    def send_trade_update(
        self,
        trade: Trade,
        event: str,
        tp_index: Optional[int] = None,
        current_price: Optional[float] = None,
    ) -> bool:
        """
        Send ONLY on meaningful trade state changes:
          event = "TP_HIT"   (tp_index required — 0-based)
          event = "SL_HIT"
          event = "COMPLETED" (TP5 reached — all targets hit)

        All other intermediate states are logged internally but NOT sent.
        """
        if event == "TP_HIT":
            return self._send_tp_hit(trade, tp_index, current_price=current_price)
        if event == "SL_HIT":
            return self._send_sl_hit(trade)
        if event == "COMPLETED":
            return self._send_completed(trade)
        logger.warning("send_trade_update: unknown event '%s' — not sent", event)
        return False

    def _send_tp_hit(
        self,
        trade: Trade,
        tp_index: int,
        current_price: Optional[float] = None,
    ) -> bool:
        tp_num      = tp_index + 1
        tp_price    = trade.tp_levels[tp_index]
        pips_gained = price_to_pips(abs(tp_price - trade.entry_price))
        dir_emoji   = trade_direction_emoji(trade.direction)

        if tp_num == 1:
            price_line = current_price if current_price is not None else tp_price
            msg = (
                f"🎯 TP1 HIT — {trade.pair}\n"
                f"Move SL to BE\n"
                f"Entry: {trade.entry_price:.2f}\n"
                f"Current Price: {price_line:.2f}"
            )
            return self._send_logged("TP ALERT", msg, parse_mode=None)
        else:
            result_line = "STRONG WIN ✅✅"
            be_note     = ""

        remaining = 5 - trade.hit_count
        rem_note  = f"_{remaining} target(s) remaining_" if remaining > 0 else ""

        msg = (
            f"🎯 *TP{tp_num} HIT → {result_line}* {dir_emoji}\n"
            f"+{pips_gained:.0f} pips @ `{tp_price:.2f}`"
            f"{be_note}\n"
            f"{rem_note}\n"
            f"🆔 `{trade.trade_uuid[:8]}`"
        )
        return self._send_logged("TP ALERT", msg)

    def _send_sl_hit(self, trade: Trade) -> bool:
        pips_lost  = price_to_pips(abs(trade.sl_price - trade.entry_price))
        tps_banked = sum(trade.tp_hit)
        if getattr(trade, "protected_after_tp1", False) or tps_banked > 0:
            msg = (
                f"🛡️ *PROTECTED EXIT — XAUUSD {trade.direction}*\n"
                f"Result: `{trade.result}`\n"
                f"Entry: `{trade.entry_price:.2f}` | Exit/SL: `{trade.sl_price:.2f}`\n"
                f"TPs reached: `{tps_banked}/5`\n"
                f"🆔 `{trade.trade_uuid[:8]}`"
            )
            return self._send_logged("SL ALERT", msg)
        be_note    = " _(BE — no monetary loss)_" if trade.be_moved else ""

        msg = (
            f"🛑 *SL HIT → LOSS ❌ — XAUUSD {trade.direction}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"SL: `{trade.sl_price:.2f}` ({pips_lost:.0f} pips){be_note}\n"
            f"TPs banked: `{tps_banked}/5`\n"
            f"🆔 `{trade.trade_uuid[:8]}`"
        )
        return self._send_logged("SL ALERT", msg)

    def _send_completed(self, trade: Trade) -> bool:
        dir_emoji = trade_direction_emoji(trade.direction)
        msg = (
            f"🏆 *COMPLETED → STRONG WIN ✅✅ — XAUUSD {trade.direction}* {dir_emoji}\n"
            f"All 5 targets reached\n"
            f"🆔 `{trade.trade_uuid[:8]}`"
        )
        return self._send_logged("TP ALERT", msg)

    # ─────────────────────────────────────────────────────
    # MANUAL SETUP ALERTS
    # ─────────────────────────────────────────────────────

    def send_manual_setup_saved(self, setup: dict) -> bool:
        """Fired immediately when a manual setup is created from the frontend."""
        direction = setup.get("direction", "?")
        symbol = setup.get("symbol", "XAUUSD")
        entry = setup.get("entry_price", 0.0)
        sl = setup.get("stop_loss", 0.0)
        tp1 = setup.get("tp1", 0.0)
        tp2 = setup.get("tp2")
        tp3 = setup.get("tp3")
        notes = setup.get("notes") or ""
        session = setup.get("session") or "—"
        setup_id = setup.get("id", "?")

        tp_lines = f"TP1: {float(tp1):.2f}"
        if tp2:
            tp_lines += f"\nTP2: {float(tp2):.2f}"
        if tp3:
            tp_lines += f"\nTP3: {float(tp3):.2f}"

        notes_line = f"\nNotes: {notes[:80]}" if notes else ""
        msg = (
            f"SPENCER MANUAL SETUP SAVED\n\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Entry: {float(entry):.2f}\n"
            f"SL: {float(sl):.2f}\n"
            f"{tp_lines}\n"
            f"Session: {session}\n"
            f"Strategy: Manual Setup\n"
            f"Status: Watching{notes_line}\n\n"
            f"Spencer is now tracking this level and will alert when price approaches or confirms.\n"
            f"Setup ID: {setup_id}"
        )
        return self._send_logged("MANUAL SETUP SAVED", msg, parse_mode=None)

    def send_manual_setup_approaching(self, setup: dict, current_price: float, distance_pips: float) -> bool:
        """Fired when current price gets within approach distance of the manual setup entry."""
        direction = setup.get("direction", "?")
        symbol = setup.get("symbol", "XAUUSD")
        entry = setup.get("entry_price", 0.0)
        setup_id = setup.get("id", "?")

        msg = (
            f"SPENCER MANUAL SETUP APPROACHING\n\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Entry: {float(entry):.2f}\n"
            f"Current Price: {current_price:.2f}\n"
            f"Distance: {distance_pips:.1f} pips\n"
            f"Status: Approaching entry\n\n"
            f"Prepare for confirmation.\n"
            f"Setup ID: {setup_id}"
        )
        return self._send_logged("MANUAL SETUP APPROACHING", msg, parse_mode=None)

    def send_manual_setup_confirmed(self, setup: dict, current_price: float, confirmation: str = "manual") -> bool:
        """Fired when price reaches the manual setup zone and confirmation is signalled."""
        direction = setup.get("direction", "?")
        symbol = setup.get("symbol", "XAUUSD")
        entry = setup.get("entry_price", 0.0)
        sl = setup.get("stop_loss", 0.0)
        tp1 = setup.get("tp1", 0.0)
        tp2 = setup.get("tp2")
        tp3 = setup.get("tp3")
        setup_id = setup.get("id", "?")

        tp_lines = f"TP1: {float(tp1):.2f}"
        if tp2:
            tp_lines += f"\nTP2: {float(tp2):.2f}"
        if tp3:
            tp_lines += f"\nTP3: {float(tp3):.2f}"

        msg = (
            f"SPENCER MANUAL SETUP CONFIRMED\n\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Entry: {float(entry):.2f}\n"
            f"Current Price: {current_price:.2f}\n"
            f"Confirmation: {confirmation}\n"
            f"SL: {float(sl):.2f}\n"
            f"{tp_lines}\n\n"
            f"Action: Manual execution opportunity.\n"
            f"Spencer does not auto-execute. Place your order manually.\n"
            f"Setup ID: {setup_id}"
        )
        return self._send_logged("MANUAL SETUP CONFIRMED", msg, parse_mode=None)

    # ─────────────────────────────────────────────────────
    # ALERT TYPE 1 — STARTUP  /  ALERT TYPE 6 — SHUTDOWN
    # OPERATIONAL — system errors
    # ─────────────────────────────────────────────────────

    def send_startup(self) -> bool:
        """Send two startup messages via the shared wrapper.

        Each send goes through ``send_telegram_message`` which already logs
        ``TELEGRAM SEND OK`` or ``TELEGRAM SEND FAILED`` with the precise
        failure reason. We add a final aggregate log for backwards
        compatibility with downstream log parsers.
        """
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        result1 = send_telegram_message(
            f"🚀 *AlphaPulse started successfully* — `{now}`\n"
            f"XAUUSD | Manual execution mode\n"
            f"_Analysis engine online._",
            "STARTUP",
            parse_mode="Markdown",
        )
        result2 = send_telegram_message(
            "🧠 Spencer is reading the market now.\n\n"
            "I’m checking structure, liquidity, session highs/lows, and the best entry zones.\n"
            "Give me about 5 minutes — I’ll post the cleanest setup once the scan is complete.",
            "STARTUP",
            parse_mode=None,
            dedupe_key="STARTUP_ANALYZING",
        )
        self._last_result = result2
        if result1.ok and result2.ok:
            runtime_logger.info("TELEGRAM SEND SUCCESS: STARTUP (both messages delivered)")
        else:
            runtime_logger.info(
                "TELEGRAM SEND FAILED: alert_type=STARTUP failure_reason=%s error=%s",
                result1.failure_reason or result2.failure_reason,
                result1.error or result2.error,
            )
        return bool(result2.ok)
        result2 = send_telegram_message(
            f"🔍 *Analyzing charts...* please wait for setups.\n"
            f"_Running multi-timeframe scan — first alerts in ~5 minutes._",
            "STARTUP",
            parse_mode="Markdown",
            dedupe_key="STARTUP_ANALYZING",
        )
        self._last_result = result2
        if result1.ok and result2.ok:
            runtime_logger.info("TELEGRAM SEND SUCCESS: STARTUP (both messages delivered)")
        else:
            runtime_logger.info(
                "TELEGRAM SEND FAILED: alert_type=STARTUP failure_reason=%s error=%s",
                result1.failure_reason or result2.failure_reason,
                result1.error or result2.error,
            )
        return bool(result2.ok)

    def send_shutdown(self) -> bool:
        now = datetime.utcnow().strftime("%H:%M UTC")
        ok = self.send(
            f"🔴 *AlphaPulse stopped* — `{now}`\n"
            f"_No further alerts until restart._"
        )
        if ok:
            runtime_logger.info("TELEGRAM SEND SUCCESS: SHUTDOWN")
        else:
            runtime_logger.info("TELEGRAM SEND FAILED: SHUTDOWN")
        return ok

    def send_restart_alert(self) -> bool:
        """Lifecycle alert for restart — not guarded by runtime_alerts_enabled."""
        now = datetime.utcnow().strftime("%H:%M UTC")
        ok = self.send(
            f"🔄 *Spencer restarting* — `{now}`\n"
            f"_AlphaPulse engine restarting now._"
        )
        if ok:
            runtime_logger.info("TELEGRAM SEND SUCCESS: RESTART ALERT")
        else:
            runtime_logger.info("TELEGRAM SEND FAILED: RESTART ALERT")
        return ok

    def send_bot_stopped_alert(self) -> bool:
        """API safety-net stop alert — fires even if bot process was hard-killed."""
        now = datetime.utcnow().strftime("%H:%M UTC")
        ok = self.send(
            f"🔴 *Spencer stopped successfully* — `{now}`\n"
            f"_AlphaPulse engine offline._"
        )
        if ok:
            runtime_logger.info("TELEGRAM SEND SUCCESS: BOT STOPPED ALERT")
        else:
            runtime_logger.info("TELEGRAM SEND FAILED: BOT STOPPED ALERT")
        return ok

    def send_bot_error_alert(self, reason: str = "") -> bool:
        """Alert when the bot process exits unexpectedly."""
        now = datetime.utcnow().strftime("%H:%M UTC")
        detail = f"\n_Reason: {reason}_" if reason else ""
        ok = self.send(
            f"⚠️ *Spencer encountered an error* — `{now}`\n"
            f"_AlphaPulse engine requires attention._{detail}"
        )
        if ok:
            runtime_logger.info("TELEGRAM SEND SUCCESS: BOT ERROR ALERT")
        else:
            runtime_logger.info("TELEGRAM SEND FAILED: BOT ERROR ALERT")
        return ok

    def send_system_alert(self, message: str) -> bool:
        if not can_send_runtime_alert():
            return False
        now = datetime.utcnow().strftime("%H:%M UTC")
        ok = self.send(
            f"⚠️ *System Alert* `{now}`\n{message}"
        )
        if ok:
            runtime_logger.info("TELEGRAM SEND SUCCESS: SYSTEM ALERT")
        else:
            runtime_logger.info("TELEGRAM SEND FAILED: SYSTEM ALERT")
        return ok

    # ─────────────────────────────────────────────────────
    # HELPERS
    # ─────────────────────────────────────────────────────

    @staticmethod
    def _model_tag(trade: Trade) -> str:
        """Return a human-readable model label for the trade."""
        strategy_type = canonical_strategy_type(getattr(trade, "strategy_type", ""))
        if strategy_type in ("gap_liquidity_sweep_reclaim", "engulfing_rejection", "standard_break_retest", "failed_engulf_break_retest"):
            return strategy_display_name(strategy_type)
        setup = getattr(trade, "setup_type", "major")
        _LABELS = {
            "lsd_swing":               "LSD Swing",
            "lsd_scalp":               "LSD Scalp",
            "qm_level":                "QM",
            "imbalance_confluence":    "Imbalance Confluence",
            "psychological_confluence":"Psych Confluence",
            "recent_leg":              "Recent Leg",
            "previous_leg":            "Previous Leg",
            "major":                   "Major Structure",
        }
        return _LABELS.get(setup, setup.replace("_", " ").title())

    @staticmethod
    def _bias_storyline_label(bias: str) -> str:
        labels = {
            "bullish": "Bullish Storyline",
            "bearish": "Bearish Storyline",
            "mixed": "Mixed Storyline",
            "neutral": "Neutral Storyline",
        }
        return labels.get((bias or "neutral").lower(), "Neutral Storyline")

    @staticmethod
    def _confidence_bar(score: float, width: int = 10) -> str:
        filled = round(max(0.0, min(1.0, score)) * width)
        return f"`[{'█' * filled}{'░' * (width - filled)}]`"
