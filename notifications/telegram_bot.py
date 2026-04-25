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

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from db.models import Trade
from utils.helpers import price_to_pips, trade_direction_emoji
from utils.logger import get_logger, get_runtime_logger

logger = get_logger(__name__)
runtime_logger = get_runtime_logger()

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
            f"*{symbol} {direction} {horizon_label}* {dir_emoji}\n"
            f"Bias: `{bias}` | TF: `{timeframe_pair}`\n"
            f"Level: `{level_price:.2f}` _{level_tag}_{tag_line}\n"
            f"Now: `{current_price:.2f}` | Distance: `{distance_pips:.1f}p`\n"
            f"Quality: {score_line}\n"
            f"Confluence: {confluence_line}\n"
            f"Status: _{status}_"
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
        strategy_title = model_tag.upper()
        if action == "BUY":
            confirmation = f"First bearish rejection closed above support ({trade.lower_tf})"
        else:
            confirmation = f"First bullish rejection closed below resistance ({trade.lower_tf})"
        bias = self._bias_storyline_label(trade.h4_bias)
        extra = ""
        if getattr(trade, "strategy_type", "") == "engulfing_rejection":
            extra = (
                f"\nStrategy: Engulfing Rejection"
                f"\nBias: {getattr(trade, 'dominant_bias', trade.h4_bias)}/{getattr(trade, 'bias_strength', 'weak')}"
                f"\nSession: {trade.session_name or 'off_session'}"
                f"\nQuality Rejections: {getattr(trade, 'quality_rejection_count', 0)}"
                f"\nStructure Breaks: {getattr(trade, 'structure_break_count', 0)}"
                f"\nConfirmation Path: {getattr(trade, 'confirmation_path', 'combined') or 'combined'}"
                f"\nConfirmation Score: {float(getattr(trade, 'confirmation_score', 0.0) or 0.0):.0f}"
            )
        if getattr(trade, "confluence_with", None):
            extra += f"\nConfluence: {model_tag} + {', '.join(trade.confluence_with).replace('_', ' ').title()}"

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
        strategy_type = getattr(trade, "strategy_type", "")
        if strategy_type == "gap_sweep":
            return "Gap Sweep"
        if strategy_type == "engulfing_rejection":
            return "Engulfing Rejection"
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
