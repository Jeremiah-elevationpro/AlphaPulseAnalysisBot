from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any

from analysis.sl_engine import validate_sl_direction
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TradeManagementState:
    setup_id: str
    symbol: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    current_status: str
    move_sl_to_be: bool
    partial_result: str
    final_result: str
    pips_result: float
    management_alert_required: bool
    review_required: bool
    protected_after_tp1: bool = False
    confirmation_type: str = ""
    confirmations: list[str] | None = None
    ai_label: str = ""
    sent_at: str = ""
    scenario_key: str = ""
    reaction_level: float = 0.0
    invalidation_level: float = 0.0
    status: str = "active"
    tp1_alert_sent: bool = False
    tp2_alert_sent: bool = False
    tp3_alert_sent: bool = False
    be_alert_sent: bool = False
    sl_alert_sent: bool = False
    virtual_sl: float | None = None
    last_trade_management_alert: str = ""
    metadata: dict[str, Any] | None = None
    created_at: str = ""
    updated_at: str = ""
    registered_at: str = ""
    tracking_version: str = "trade_tracking_v2"
    imported_from_memory: bool = False
    lifecycle_alerts_enabled: bool = True
    actionable_trade: bool = True
    first_seen_price: float = 0.0
    first_seen_candle_time: str = ""
    last_checked_price: float = 0.0
    last_checked_candle_time: str = ""
    last_checked_high: float = 0.0
    last_checked_low: float = 0.0
    backfilled_tp1: bool = False
    backfilled_tp2: bool = False
    backfilled_tp3: bool = False
    duplicate_suppressed: bool = False
    merged_into_setup_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradeManagementEngine:
    def __init__(self):
        self._active_setups: dict[str, TradeManagementState] = {}
        self._sent_alert_keys: set[str] = set()
        self.trade_tracking_started_at = datetime.now(timezone.utc).isoformat()
        self.tracking_version = "trade_tracking_v2"
        self.duplicate_trades_merged_count = 0
        self.recovered_trades_muted_count = 0
        self.last_tp_alert_sent = ""

    @staticmethod
    def _parse_time(value: str) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    @staticmethod
    def _close(a: float, b: float, tolerance: float = 3.0) -> bool:
        return abs(float(a) - float(b)) <= tolerance

    def _same_active_trade_idea(self, state: TradeManagementState, setup, metadata: dict[str, Any]) -> bool:
        if state.status not in {"active", "tp1_hit", "tp2_hit", "tp3_hit"}:
            return False
        setup_symbol = str(getattr(setup, "symbol", "XAUUSD") or "XAUUSD")
        if state.symbol != setup_symbol or str(state.direction).upper() != str(setup.direction).upper():
            return False
        if not self._close(state.entry, float(setup.entry)) or not self._close(state.sl, float(setup.sl)):
            return False
        if not (self._close(state.tp1, float(setup.tp1)) and self._close(state.tp2, float(setup.tp2)) and self._close(state.tp3, float(setup.tp3))):
            return False
        scenario_type = str(metadata.get("scenario_type") or "")
        state_scenario = str((state.metadata or {}).get("scenario_type") or "")
        if scenario_type and state_scenario and scenario_type != state_scenario:
            return False
        new_time = self._parse_time(str(metadata.get("candle_time") or metadata.get("sent_at") or ""))
        old_time = self._parse_time(state.registered_at or state.created_at)
        if new_time and old_time and abs((new_time - old_time).total_seconds()) > 7200:
            return False
        return True

    def find_duplicate_active_trade(self, setup, metadata: dict[str, Any]) -> TradeManagementState | None:
        for state in self._active_setups.values():
            if self._same_active_trade_idea(state, setup, metadata):
                return state
        return None

    def _mark_backfilled_tps(self, state: TradeManagementState, *, high: float, low: float, current_price: float) -> None:
        direction = state.direction.upper()
        checks = [
            ("TP1", "tp1_alert_sent", "backfilled_tp1", state.tp1),
            ("TP2", "tp2_alert_sent", "backfilled_tp2", state.tp2),
            ("TP3", "tp3_alert_sent", "backfilled_tp3", state.tp3),
        ]
        for level_name, sent_attr, backfilled_attr, target in checks:
            hit = low <= target if direction == "SELL" else high >= target
            if hit and not getattr(state, sent_attr):
                setattr(state, sent_attr, True)
                setattr(state, backfilled_attr, True)
                self.mark_alert_sent(state.setup_id, level_name)
                if level_name == "TP1":
                    state.current_status = "tp1_hit"
                    state.status = "tp1_hit"
                    state.move_sl_to_be = True
                    state.protected_after_tp1 = True
                    state.be_alert_sent = True
                    state.virtual_sl = state.entry
                    state.partial_result = "TP1 HIT"
                elif level_name == "TP2":
                    state.current_status = "tp2_hit"
                    state.status = "tp2_hit"
                    state.final_result = "WIN"
                    state.pips_result = round(abs(state.entry - state.tp2), 2)
                elif level_name == "TP3":
                    state.current_status = "tp3_hit"
                    state.status = "tp3_hit"
                    state.final_result = "STRONG_WIN"
                    state.pips_result = round(abs(state.entry - state.tp3), 2)
                logger.info("BACKFILLED %s MARKED WITHOUT ALERT: setup_id=%s price=%.2f", level_name, state.setup_id, current_price)

    def _merge_active_trade(self, existing: TradeManagementState, setup_id: str, setup, metadata: dict[str, Any]) -> TradeManagementState:
        confirmations = list(existing.confirmations or [])
        confirmation_type = str(getattr(setup, "confirmation_type", "") or metadata.get("confirmation_type", ""))
        if confirmation_type and confirmation_type not in confirmations:
            confirmations.append(confirmation_type)
        existing.confirmations = confirmations
        existing.confirmation_type = confirmations[-1] if confirmations else existing.confirmation_type
        existing.metadata = {**dict(existing.metadata or {}), "merged_setup_ids": [*list((existing.metadata or {}).get("merged_setup_ids") or []), setup_id]}
        existing.updated_at = datetime.now(timezone.utc).isoformat()
        self.duplicate_trades_merged_count += 1
        logger.info(
            "ACTIVE TRADE MERGED: existing_setup_id=%s new_setup_id=%s confirmations=%s",
            existing.setup_id,
            setup_id,
            confirmations,
        )
        return existing

    def register_setup(
        self,
        setup_id: str,
        setup,
        metadata: dict[str, Any] | None = None,
        *,
        current_price: float | None = None,
        candle_high: float | None = None,
        candle_low: float | None = None,
        candle_time: str = "",
    ) -> TradeManagementState:
        now = datetime.now(timezone.utc).isoformat()
        metadata = dict(metadata or {})
        duplicate = self.find_duplicate_active_trade(setup, metadata)
        if duplicate is not None:
            return self._merge_active_trade(duplicate, setup_id, setup, metadata)
        is_valid_sl, sl_reason = validate_sl_direction(str(setup.direction), float(setup.entry), float(setup.sl))
        first_seen_price = float(current_price if current_price is not None else setup.entry)
        first_high = float(candle_high if candle_high is not None else first_seen_price)
        first_low = float(candle_low if candle_low is not None else first_seen_price)
        actionable_trade = bool(metadata.get("actionable_trade", True))
        state = TradeManagementState(
            setup_id=setup_id,
            symbol="XAUUSD",
            direction=setup.direction,
            entry=float(setup.entry),
            sl=float(setup.sl),
            tp1=float(setup.tp1),
            tp2=float(setup.tp2),
            tp3=float(setup.tp3),
            current_status="active" if is_valid_sl else "invalidated",
            move_sl_to_be=False,
            partial_result="",
            final_result="" if is_valid_sl else "INVALIDATED",
            pips_result=0.0,
            management_alert_required=False,
            review_required=not is_valid_sl,
            confirmation_type=str(getattr(setup, "confirmation_type", "") or (metadata or {}).get("confirmation_type", "")),
            confirmations=list((metadata or {}).get("confirmations") or [str(getattr(setup, "confirmation_type", "") or (metadata or {}).get("confirmation_type", ""))]),
            ai_label=str((metadata or {}).get("ai_label", "")),
            sent_at=str((metadata or {}).get("sent_at", now)),
            scenario_key=str((metadata or {}).get("scenario_key", "")),
            reaction_level=float((metadata or {}).get("reaction_level", 0.0) or 0.0),
            invalidation_level=float((metadata or {}).get("invalidation_level", 0.0) or 0.0),
            status="active" if is_valid_sl else "invalidated",
            virtual_sl=float(setup.sl),
            metadata=dict(metadata or {}),
            created_at=now,
            updated_at=now,
            registered_at=now,
            tracking_version=self.tracking_version,
            imported_from_memory=False,
            lifecycle_alerts_enabled=bool(is_valid_sl and actionable_trade),
            actionable_trade=actionable_trade,
            first_seen_price=first_seen_price,
            first_seen_candle_time=str(candle_time or metadata.get("candle_time") or now),
            last_checked_price=first_seen_price,
            last_checked_candle_time=str(candle_time or metadata.get("candle_time") or now),
            last_checked_high=first_high,
            last_checked_low=first_low,
        )
        self._mark_backfilled_tps(state, high=first_high, low=first_low, current_price=first_seen_price)
        self._active_setups[setup_id] = state
        if is_valid_sl:
            logger.info(
                "LIVE TRADE REGISTERED: setup_id=%s direction=%s entry=%.2f sl=%.2f tp1=%.2f tp2=%.2f tp3=%.2f",
                setup_id, state.direction, state.entry, state.sl, state.tp1, state.tp2, state.tp3,
            )
        else:
            logger.warning("TRADE MANAGEMENT REJECTED: setup_id=%s reason=%s", setup_id, sl_reason)
        return state

    def load_states(self, states: dict[str, dict[str, Any]] | None, sent_alert_keys: list[str] | None = None) -> None:
        self._sent_alert_keys = set(str(key) for key in (sent_alert_keys or []))
        for setup_id, raw in (states or {}).items():
            try:
                clean = dict(raw)
                known = set(TradeManagementState.__dataclass_fields__.keys())
                state = TradeManagementState(**{k: v for k, v in clean.items() if k in known})
                if state.status in {"active", "tp1_hit", "tp2_hit", "tp3_hit"} and not state.review_required:
                    state.imported_from_memory = True
                    state.lifecycle_alerts_enabled = False
                    state.updated_at = datetime.now(timezone.utc).isoformat()
                    self.recovered_trades_muted_count += 1
                    logger.info(
                        "RECOVERED ACTIVE TRADE MUTED: setup_id=%s reason=created_before_tracking_start",
                        setup_id,
                    )
                    self._active_setups[str(setup_id)] = state
            except Exception as exc:
                logger.debug("Trade management state restore skipped: setup_id=%s error=%s", setup_id, exc)
        self.cleanup_duplicate_active_trades()

    def cleanup_duplicate_active_trades(self) -> dict[str, int]:
        before = len(self._active_setups)
        canonical: dict[str, TradeManagementState] = {}
        for setup_id, state in list(self._active_setups.items()):
            key = (
                state.symbol,
                state.direction.upper(),
                round(state.entry / 3.0),
                round(state.sl / 3.0),
                round(state.tp1 / 3.0),
                round(state.tp2 / 3.0),
                round(state.tp3 / 3.0),
            )
            existing = canonical.get(str(key))
            if existing is None:
                canonical[str(key)] = state
                continue
            existing.confirmations = sorted(set(list(existing.confirmations or []) + list(state.confirmations or [])))
            existing.tp1_alert_sent = existing.tp1_alert_sent or state.tp1_alert_sent
            existing.tp2_alert_sent = existing.tp2_alert_sent or state.tp2_alert_sent
            existing.tp3_alert_sent = existing.tp3_alert_sent or state.tp3_alert_sent
            existing.backfilled_tp1 = existing.backfilled_tp1 or state.backfilled_tp1
            existing.backfilled_tp2 = existing.backfilled_tp2 or state.backfilled_tp2
            existing.backfilled_tp3 = existing.backfilled_tp3 or state.backfilled_tp3
            existing.protected_after_tp1 = existing.protected_after_tp1 or state.protected_after_tp1
            state.duplicate_suppressed = True
            state.merged_into_setup_id = existing.setup_id
            self._active_setups.pop(setup_id, None)
            self.duplicate_trades_merged_count += 1
        after = len(self._active_setups)
        merged = before - after
        logger.info("ACTIVE TRADE CLEANUP: before=%d after=%d merged=%d", before, after, merged)
        return {"before": before, "after": after, "merged": merged}

    def merge_confirmation(self, setup_id: str, confirmation_type: str) -> TradeManagementState | None:
        state = self._active_setups.get(setup_id)
        if state is None:
            return None
        confirmations = list(state.confirmations or [])
        if confirmation_type and confirmation_type not in confirmations:
            confirmations.append(confirmation_type)
        state.confirmations = confirmations
        state.confirmation_type = confirmations[-1] if confirmations else state.confirmation_type
        state.updated_at = datetime.now(timezone.utc).isoformat()
        logger.info(
            "ENTRY ALERT MERGED: same_trade_idea dedupe_key=%s confirmations=%s",
            setup_id,
            confirmations,
        )
        return state

    def alert_already_sent(self, setup_id: str, level_name: str) -> bool:
        return f"{setup_id}:{level_name}" in self._sent_alert_keys

    def mark_alert_sent(self, setup_id: str, level_name: str) -> None:
        self._sent_alert_keys.add(f"{setup_id}:{level_name}")

    def sent_alert_keys(self) -> list[str]:
        return sorted(self._sent_alert_keys)

    def tracking_summary(self) -> dict[str, Any]:
        enabled = sum(1 for state in self._active_setups.values() if state.lifecycle_alerts_enabled)
        muted = sum(1 for state in self._active_setups.values() if not state.lifecycle_alerts_enabled)
        return {
            "trade_tracking_started_at": self.trade_tracking_started_at,
            "tracking_version": self.tracking_version,
            "active_trades_count": len(self._active_setups),
            "lifecycle_alerts_enabled_count": enabled,
            "recovered_trades_muted_count": max(self.recovered_trades_muted_count, muted),
            "duplicate_trades_merged_count": self.duplicate_trades_merged_count,
            "last_tp_alert_sent": self.last_tp_alert_sent,
        }

    def update_trade(
        self,
        setup_id: str,
        current_price: float,
        candle_high: float | None = None,
        candle_low: float | None = None,
        candle_time: str = "",
    ) -> TradeManagementState | None:
        state = self._active_setups.get(setup_id)
        if state is None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        state.updated_at = now
        high = float(candle_high if candle_high is not None else current_price)
        low = float(candle_low if candle_low is not None else current_price)
        previous_high = float(state.last_checked_high or state.first_seen_price or current_price)
        previous_low = float(state.last_checked_low or state.first_seen_price or current_price)
        direction = state.direction.upper()
        if direction == "SELL":
            tp1_hit = low <= state.tp1
            tp2_hit = low <= state.tp2
            tp3_hit = low <= state.tp3
            tp1_crossed = tp1_hit and previous_low > state.tp1
            tp2_crossed = tp2_hit and previous_low > state.tp2
            tp3_crossed = tp3_hit and previous_low > state.tp3
            sl_hit = high >= (state.virtual_sl if state.virtual_sl is not None else state.sl)
            be_hit = state.protected_after_tp1 and low <= state.entry <= high
        else:
            tp1_hit = high >= state.tp1
            tp2_hit = high >= state.tp2
            tp3_hit = high >= state.tp3
            tp1_crossed = tp1_hit and previous_high < state.tp1
            tp2_crossed = tp2_hit and previous_high < state.tp2
            tp3_crossed = tp3_hit and previous_high < state.tp3
            sl_hit = low <= (state.virtual_sl if state.virtual_sl is not None else state.sl)
            be_hit = state.protected_after_tp1 and low <= state.entry <= high
        alerts_allowed = bool(state.lifecycle_alerts_enabled and state.actionable_trade)

        logger.info(
            "TRACKED TRADE CHECK: setup_id=%s direction=%s entry=%.2f sl=%.2f tp1=%.2f tp2=%.2f tp3=%.2f tp1_hit=%s tp2_hit=%s tp3_hit=%s sl_hit=%s",
            setup_id, state.direction, state.entry, state.sl, state.tp1, state.tp2, state.tp3,
            tp1_hit, tp2_hit, tp3_hit, sl_hit,
        )

        if not alerts_allowed and (tp1_hit or tp2_hit or tp3_hit):
            self._mark_backfilled_tps(state, high=high, low=low, current_price=current_price)
            state.management_alert_required = False

        if alerts_allowed and tp1_crossed and not state.tp1_alert_sent:
            state.current_status = "tp1_hit"
            state.status = "tp1_hit"
            state.move_sl_to_be = True
            state.management_alert_required = True
            state.protected_after_tp1 = True
            state.be_alert_sent = True
            state.virtual_sl = state.entry
            state.partial_result = "TP1 HIT"
            state.last_trade_management_alert = "TP1_BE"
            logger.info("TP1 HIT DETECTED: setup_id=%s price=%.2f move_sl_to_be=true", setup_id, current_price)

        if alerts_allowed and tp2_crossed and not state.tp2_alert_sent:
            state.current_status = "tp2_hit"
            state.status = "tp2_hit"
            state.final_result = "WIN"
            state.pips_result = round(abs(state.entry - state.tp2), 2)
            state.management_alert_required = True
            state.last_trade_management_alert = "TP2"

        if alerts_allowed and tp3_crossed and not state.tp3_alert_sent:
            state.current_status = "tp3_hit"
            state.status = "tp3_hit"
            state.final_result = "STRONG_WIN"
            state.pips_result = round(abs(state.entry - state.tp3), 2)
            state.management_alert_required = True
            state.review_required = True
            state.last_trade_management_alert = "TP3"

        if alerts_allowed and be_hit and state.protected_after_tp1 and not state.review_required and state.tp1_alert_sent:
            state.current_status = "breakeven_win"
            state.status = "breakeven_win"
            state.final_result = "BREAKEVEN_WIN"
            state.pips_result = max(state.pips_result, round(abs(state.entry - state.tp1), 2))
            state.management_alert_required = True
            state.review_required = True
            state.last_trade_management_alert = "BE"

        if alerts_allowed and sl_hit and not state.review_required and not (tp1_hit and not state.tp1_alert_sent):
            if state.protected_after_tp1:
                state.current_status = "breakeven_win"
                state.status = "breakeven_win"
                state.final_result = "BREAKEVEN_WIN"
                state.last_trade_management_alert = "BE"
            else:
                state.current_status = "stopped"
                state.status = "loss"
                state.final_result = "LOSS"
                state.pips_result = -round(abs(state.entry - state.sl), 2)
                state.last_trade_management_alert = "SL"
            state.management_alert_required = True
            state.review_required = True
        state.last_checked_price = float(current_price)
        state.last_checked_high = high
        state.last_checked_low = low
        state.last_checked_candle_time = str(candle_time or now)
        return state

    def update_trade_legacy(self, setup_id: str, current_price: float) -> TradeManagementState | None:
        """Compatibility shim for older imports."""
        return self.update_trade(setup_id, current_price)
