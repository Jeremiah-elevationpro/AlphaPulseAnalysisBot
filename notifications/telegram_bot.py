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

from typing import Optional
from datetime import datetime
import json
from pathlib import Path

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from db.models import Trade
from utils.helpers import price_to_pips, trade_direction_emoji
from utils.logger import get_logger, get_runtime_logger
from utils.strategy_registry import canonical_strategy_type, strategy_display_name

logger = get_logger(__name__)
runtime_logger = get_runtime_logger()

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


TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


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

        if not self._token:
            logger.warning(
                "Telegram BOT TOKEN not set. Add to .env:\n"
                "  TELEGRAM_BOT_TOKEN=<token from @BotFather>"
            )
        elif not self._chat_id:
            logger.warning(
                "Telegram CHAT ID not set.\n"
                "  Add TELEGRAM_CHAT_ID=<id> to your .env"
            )
        else:
            logger.info("Telegram ready — alerts → chat %s", self._chat_id)

    # ─────────────────────────────────────────────────────
    # CORE SEND
    # ─────────────────────────────────────────────────────

    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send a message. Returns True on success."""
        if not self._enabled:
            logger.info("[TELEGRAM MOCK]\n%s", message)
            return True
        try:
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "disable_web_page_preview": True,
            }
            if parse_mode:
                payload["parse_mode"] = parse_mode

            resp = requests.post(
                f"{TELEGRAM_API}/sendMessage",
                json=payload,
                timeout=10,
            )
            if resp.status_code == 200:
                logger.debug("Telegram sent OK.")
                return True

            if (
                resp.status_code == 400
                and "can't parse entities" in resp.text.lower()
                and parse_mode
            ):
                logger.warning("Telegram parse error; retrying without Markdown.")
                fallback = {
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": self._strip_markdown(message),
                    "disable_web_page_preview": True,
                }
                retry = requests.post(
                    f"{TELEGRAM_API}/sendMessage",
                    json=fallback,
                    timeout=10,
                )
                if retry.status_code == 200:
                    logger.debug("Telegram sent OK (plain-text fallback).")
                    return True
                logger.error("Telegram API %s: %s", retry.status_code, retry.text)
                return False

            logger.error("Telegram API %s: %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    def _send_logged(self, event_label: str, message: str, parse_mode: str = "Markdown") -> bool:
        runtime_logger.info("TELEGRAM %s SEND ATTEMPT", event_label)
        try:
            ok = self.send(message, parse_mode=parse_mode)
        except Exception as exc:
            runtime_logger.info("TELEGRAM %s SEND FAILED: error=%s", event_label, exc)
            return False

        if ok:
            runtime_logger.info("TELEGRAM %s SEND SUCCESS", event_label)
        else:
            runtime_logger.info("TELEGRAM %s SEND FAILED: error=send_returned_false", event_label)
        return ok

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
        msg = (
            f"SPENCER MARKET PLAN — {plan.get('symbol', 'XAUUSD')}\n\n"
            f"Current Price: {float(plan.get('current_price') or 0.0):.2f}\n\n"
            f"H4 Context:\n{plan.get('h4_context', 'Unavailable')}\n\n"
            f"H1 Context:\n{plan.get('h1_context', 'Unavailable')}\n\n"
            f"M15 Context:\n{plan.get('m15_context', 'Unavailable')}\n\n"
            f"Dominant Bias: {plan.get('dominant_bias', 'neutral')} ({plan.get('bias_strength', 'weak')})\n\n"
            f"Primary Scenario:\n"
            f"{primary.get('direction', '?')} fresh retest of {primary.get('watch_zone', 'n/a')} only\n"
            f"Trigger: {'; '.join(primary.get('trigger_conditions', [])[:4])}\n"
            f"Targets: {' / '.join(f'{float(v):.2f}' for v in primary.get('market_plan_targets', primary.get('targets', []))[:5])}\n"
            f"Invalidation: {primary.get('invalidation', 'n/a')}\n\n"
            f"Secondary Scenario:\n"
            f"{secondary.get('direction', '?')} {secondary.get('watch_zone', 'n/a')}\n"
            f"Trigger: {'; '.join(secondary.get('trigger_conditions', [])[:4])}\n"
            f"Targets: {' / '.join(f'{float(v):.2f}' for v in secondary.get('market_plan_targets', secondary.get('targets', []))[:5])}\n"
            f"Invalidation: {secondary.get('invalidation', 'n/a')}\n\n"
            f"Watching: {', '.join(plan.get('confirmation_waiting_for', [])[:6])}\n"
            f"Psychological Levels: {', '.join(f'{float(v):.2f}' for v in (plan.get('actionable_psych_levels') or plan.get('psychological_levels') or [])[:12])}\n"
            f"Key Supports: {', '.join(f'{float(v):.2f}' for v in plan.get('key_supports', [])[:4])}\n"
            f"Key Resistances: {', '.join(f'{float(v):.2f}' for v in plan.get('key_resistances', [])[:4])}"
        )
        return self._send_logged("MARKET PLAN", msg, parse_mode=None)

    def send_analyst_entry_alert(self, setup: dict) -> bool:
        target_roles = dict(setup.get("target_roles", {}) or {})
        reaction_level = float(setup.get("reaction_level") or 0.0)
        msg = (
            f"SPENCER ENTRY CONFIRMED â€” {setup.get('direction', '?')} GOLD\n\n"
            f"Quality: {setup.get('setup_quality_label', 'QUALITY SETUP')}\n"
            f"Score: {float(setup.get('candidate_rank_score') or setup.get('priority') or 0.0):.0f}\n"
            f"Scenario: {setup.get('scenario', 'primary')}\n"
            f"Confirmation: {setup.get('confirmation_type', 'unknown')}\n"
            f"Grade: {setup.get('confirmation_grade', setup.get('grade', '?'))}\n\n"
            f"Entry: {float(setup.get('entry') or setup.get('suggested_entry') or 0.0):.2f}\n"
            f"SL: {float(setup.get('sl') or setup.get('suggested_sl') or 0.0):.2f}\n"
            f"Invalidation: {setup.get('invalidation', 'n/a')}\n\n"
            f"{f'Reaction Level: {reaction_level:.2f}\\n' if reaction_level else ''}"
            f"TP1: {float(setup.get('tp1') or 0.0):.2f} — main target / move SL to BE\n"
            f"TP2: {float(setup.get('tp2') or 0.0):.2f} — runner target\n"
            f"TP3: {float(setup.get('tp3') or 0.0):.2f} — extended runner / major target\n\n"
            f"Risk: {float(setup.get('risk_pips') or 0.0):.0f} pips\n"
            f"TP1 RR: {float(setup.get('tp1_rr') or 0.0):.2f}R\n"
            f"TP2 RR: {float(setup.get('tp2_rr') or 0.0):.2f}R\n"
            f"TP3 RR: {float(setup.get('tp3_rr') or 0.0):.2f}R\n\n"
            f"Why:\n{setup.get('trade_path_rationale', setup.get('entry_reason', 'Quality confirmation at active watch zone.'))}\n\n"
            f"SL Rationale: {setup.get('sl_rationale', 'structure-based')}\n"
            f"TP Rationale: {setup.get('tp_rationale', 'structure-based')}\n"
            f"Target Roles: {target_roles}"
        )
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

    def send_scenario_update_alert(self, update: dict) -> bool:
        msg = (
            f"SPENCER SCENARIO UPDATE — {update.get('symbol', 'XAUUSD')}\n\n"
            f"{update.get('message', 'Scenario updated.')}\n\n"
            f"Primary: {update.get('primary', 'n/a')}\n"
            f"Secondary: {update.get('secondary', 'n/a')}\n"
            f"Waiting For: {update.get('waiting_for', 'n/a')}"
        )
        return self._send_logged("SCENARIO UPDATE", msg, parse_mode=None)

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
        """Send two messages: bot online, then analysis phase notice."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        ok1 = self.send(
            f"🚀 *AlphaPulse started successfully* — `{now}`\n"
            f"XAUUSD | Manual execution mode\n"
            f"_Analysis engine online._"
        )
        ok2 = self.send(
            f"🔍 *Analyzing charts...* please wait for setups.\n"
            f"_Running multi-timeframe scan — first alerts in ~5 minutes._"
        )
        if ok1 and ok2:
            runtime_logger.info("TELEGRAM SEND SUCCESS: STARTUP")
        else:
            runtime_logger.info("TELEGRAM SEND FAILED: STARTUP")
        return ok2

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
