from datetime import datetime, timezone
from typing import Literal, Optional
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import api.state as state

logger = logging.getLogger("alphapulse.api.setups")

router = APIRouter()


Direction = Literal["BUY", "SELL"]
StatusValue = Literal[
    "draft",
    "watching",
    "approaching_entry",
    "entry_touched",
    "confirmation_waiting",
    "confirmed",
    "active",
    "pending-order-ready",
    "activated",
    "TP1 hit",
    "BE protected",
    "TP2 hit",
    "TP3 hit",
    "stopped out",
    "closed manually",
    "expired",
    "completed",
    "failed",
    "cancelled",
    "invalidated",
    "archived",
]


class SetupCreate(BaseModel):
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=20)
    direction: Direction
    timeframe_pair: str
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    bias: Optional[str] = None
    confirmation_type: Optional[str] = None
    session: Optional[str] = None
    notes: Optional[str] = ""
    activation_mode: Optional[str] = None
    move_sl_to_be_after_tp1: bool = True
    enable_telegram_alerts: bool = True
    high_priority: bool = False
    status: StatusValue = "draft"


class SetupUpdate(BaseModel):
    symbol: Optional[str] = Field(default=None, min_length=1, max_length=20)
    direction: Optional[Direction] = None
    timeframe_pair: Optional[str] = None
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    tp3: Optional[float] = None
    bias: Optional[str] = None
    confirmation_type: Optional[str] = None
    session: Optional[str] = None
    notes: Optional[str] = None
    activation_mode: Optional[str] = None
    move_sl_to_be_after_tp1: Optional[bool] = None
    enable_telegram_alerts: Optional[bool] = None
    high_priority: Optional[bool] = None
    status: Optional[StatusValue] = None


class SetupStatusUpdate(BaseModel):
    status: Literal["completed", "cancelled", "failed", "expired", "invalidated", "archived"]
    note: Optional[str] = None


def _shape_setup(row: dict, *, telegram_alert_sent: Optional[bool] = None, tracking_enabled: bool = True) -> dict:
    created_at = row.get("created_at") or datetime.now(timezone.utc).isoformat()
    updated_at = row.get("updated_at") or created_at
    tg_sent = telegram_alert_sent if telegram_alert_sent is not None else bool(row.get("telegram_alert_sent", False))
    return {
        "id": row.get("id"),
        "symbol": row.get("symbol", "XAUUSD"),
        "direction": row.get("direction"),
        "timeframe_pair": row.get("timeframe_pair"),
        "entry_price": float(row["entry_price"]) if row.get("entry_price") is not None else None,
        "stop_loss": float(row["stop_loss"]) if row.get("stop_loss") is not None else None,
        "tp1": float(row["tp1"]) if row.get("tp1") is not None else None,
        "tp2": float(row["tp2"]) if row.get("tp2") is not None else None,
        "tp3": float(row["tp3"]) if row.get("tp3") is not None else None,
        "bias": row.get("bias"),
        "confirmation_type": row.get("confirmation_type"),
        "session": row.get("session"),
        "notes": row.get("notes") or "",
        "activation_mode": row.get("activation_mode"),
        "move_sl_to_be_after_tp1": bool(row.get("move_sl_to_be_after_tp1", True)),
        "enable_telegram_alerts": bool(row.get("enable_telegram_alerts", True)),
        "high_priority": bool(row.get("high_priority", False)),
        "status": row.get("status", "draft"),
        "source": row.get("source", "manual"),
        "strategy_type": row.get("strategy_type", "manual_setup"),
        "setup_type": row.get("setup_type", "manual_setup"),
        "tracking_enabled": tracking_enabled,
        "tracking_status": row.get("tracking_status", "watching"),
        "confirmation_required": bool(row.get("confirmation_required", False)),
        "telegram_alert_sent": tg_sent,
        "telegram_alert_sent_at": row.get("telegram_alert_sent_at"),
        "telegram_error": row.get("telegram_error"),
        "last_alert_type": row.get("last_alert_type"),
        "last_alert_time": row.get("last_alert_time"),
        "current_price": row.get("current_price"),
        "distance_to_entry_pips": row.get("distance_to_entry_pips"),
        "closed_at": row.get("closed_at"),
        "completed_at": row.get("completed_at"),
        "cancelled_at": row.get("cancelled_at"),
        "failed_at": row.get("failed_at"),
        "archived_at": row.get("archived_at"),
        "completion_note": row.get("completion_note"),
        "created_at": str(created_at),
        "updated_at": str(updated_at),
    }


def _send_telegram_saved(row: dict) -> tuple[bool, Optional[str]]:
    """Send Telegram saved alert from the API process. Returns (sent, error_str)."""
    try:
        from notifications.telegram_bot import TelegramBot
        tg = TelegramBot()
        ok = tg.send_manual_setup_saved(row)
        return ok, None
    except Exception as exc:
        logger.warning("Manual setup Telegram alert failed: %s", exc)
        return False, str(exc)


@router.get("/setups")
def list_setups():
    if not state.db_ready:
        return {"setups": [], "total": 0, "db_ready": False}

    try:
        rows = state.db.get_manual_setups(limit=500)
        setups = [_shape_setup(row) for row in rows]
        return {"setups": setups, "total": len(setups), "db_ready": True}
    except Exception:
        state.mark_db_failure()
        return {"setups": [], "total": 0, "db_ready": False}


@router.get("/manual-setups")
def list_manual_setups():
    return list_setups()


@router.get("/manual-setups/active")
def list_active_manual_setups():
    if not state.db_ready:
        return {"setups": [], "total": 0, "db_ready": False}
    rows = state.db.get_manual_setups(limit=500)
    active = [
        _shape_setup(row)
        for row in rows
        if row.get("tracking_enabled", True) and row.get("status") not in {"completed", "failed", "cancelled", "expired", "invalidated", "archived"}
    ]
    return {"setups": active, "total": len(active), "db_ready": True}


@router.get("/manual-setups/history")
def list_manual_setup_history():
    if not state.db_ready:
        return {"setups": [], "total": 0, "db_ready": False}
    rows = state.db.get_manual_setups(limit=500)
    history = [
        _shape_setup(row, tracking_enabled=bool(row.get("tracking_enabled", False)))
        for row in rows
        if row.get("status") in {"completed", "failed", "cancelled", "expired", "invalidated", "archived"}
    ]
    return {"setups": history, "total": len(history), "db_ready": True}


@router.post("/setups", status_code=201)
def create_setup(body: SetupCreate):
    if not state.db_ready:
        raise HTTPException(status_code=503, detail="Database not available")

    payload = body.model_dump()
    payload["symbol"] = payload["symbol"].strip().upper()
    payload.setdefault("source", "manual")
    payload.setdefault("strategy_type", "manual_setup")
    payload.setdefault("setup_type", "manual_setup")
    payload.setdefault("tracking_enabled", True)
    payload.setdefault("tracking_status", "watching")

    # Force status to "watching" so tracking activates immediately
    if payload.get("status") == "draft":
        payload["status"] = "watching"

    try:
        row = state.db.insert_manual_setup(payload)
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create manual setup")
    except HTTPException:
        raise
    except Exception as exc:
        state.mark_db_failure()
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")

    # Send Telegram saved alert (non-blocking — failure does not break the response)
    tg_sent = False
    tg_error = None
    if body.enable_telegram_alerts:
        tg_sent, tg_error = _send_telegram_saved(row)
        # Persist telegram outcome back to DB
        try:
            patch: dict = {"telegram_alert_sent": tg_sent}
            if tg_sent:
                patch["telegram_alert_sent_at"] = datetime.now(timezone.utc).isoformat()
                patch["last_alert_type"] = "manual_setup"
                patch["last_alert_time"] = datetime.now(timezone.utc).isoformat()
            if tg_error:
                patch["telegram_error"] = tg_error[:500]
            state.db.update_manual_setup(row["id"], patch)
            row["telegram_alert_sent"] = tg_sent
            row["telegram_error"] = tg_error
        except Exception:
            pass

    shaped = _shape_setup(row, telegram_alert_sent=tg_sent, tracking_enabled=True)
    shaped["setup_id"] = shaped["id"]
    shaped["telegram_alert_sent"] = tg_sent
    shaped["telegram_error"] = tg_error if not tg_sent else None
    # Session Liquidity advisory (non-blocking) — surfaces sweep guidance to
    # the user when their manual entry is parked on a known liquidity pool.
    try:
        from analysis.session_liquidity import SessionLiquidityEngine
        plan = (state.bot_state.get("market_plan") or {})
        summary = plan.get("session_liquidity") or {}
        if summary:
            advisory = SessionLiquidityEngine().evaluate_manual_setup(
                level=float(payload.get("entry_price") or 0.0),
                direction=str(payload.get("direction") or ""),
                current_price=float(state.bot_state.get("current_price") or 0.0),
                plan_summary=summary,
            )
            shaped["session_liquidity_advisory"] = advisory
    except Exception:
        shaped["session_liquidity_advisory"] = None
    return shaped


@router.post("/manual-setups", status_code=201)
def create_manual_setup(body: SetupCreate):
    return create_setup(body)


@router.patch("/setups/{setup_id}")
def update_setup(setup_id: int, body: SetupUpdate):
    if not state.db_ready:
        raise HTTPException(status_code=503, detail="Database not available")

    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if not payload:
        raise HTTPException(status_code=422, detail="No update fields provided")
    if "symbol" in payload:
        payload["symbol"] = payload["symbol"].strip().upper()

    try:
        row = state.db.update_manual_setup(setup_id, payload)
        if not row:
            raise HTTPException(status_code=404, detail="Setup not found")
        return _shape_setup(row)
    except HTTPException:
        raise
    except Exception as exc:
        state.mark_db_failure()
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")


@router.patch("/manual-setups/{setup_id}/status")
def update_manual_setup_status(setup_id: int, body: SetupStatusUpdate):
    if not state.db_ready:
        raise HTTPException(status_code=503, detail="Database not available")
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "status": body.status,
        "tracking_enabled": False,
        "completion_note": body.note or "",
        "closed_at": now,
        "tracking_status": body.status,
        f"{body.status}_at": now if body.status in {"completed", "failed", "cancelled", "archived"} else None,
    }
    if body.status == "expired":
        payload["completed_at"] = None
    try:
        row = state.db.update_manual_setup(setup_id, {k: v for k, v in payload.items() if v is not None})
        if not row:
            raise HTTPException(status_code=404, detail="Setup not found")
        logger.info("MANUAL SETUP STATUS UPDATED: id=%s status=%s tracking=false", setup_id, body.status)
        return {
            "success": True,
            "setup_id": setup_id,
            "status": body.status,
            "tracking_enabled": False,
            "closed_at": now,
            "setup": _shape_setup(row, tracking_enabled=False),
        }
    except HTTPException:
        raise
    except Exception as exc:
        state.mark_db_failure()
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")


@router.post("/manual-setups/{setup_id}/resend-alert")
def resend_manual_setup_alert(setup_id: int):
    if not state.db_ready:
        raise HTTPException(status_code=503, detail="Database not available")
    rows = state.db.get_manual_setups(limit=500)
    row = next((item for item in rows if item.get("id") == setup_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Setup not found")
    tg_sent, tg_error = _send_telegram_saved(row)
    patch = {
        "telegram_alert_sent": tg_sent,
        "telegram_alert_sent_at": datetime.now(timezone.utc).isoformat() if tg_sent else row.get("telegram_alert_sent_at"),
        "telegram_error": tg_error[:500] if tg_error else None,
        "last_alert_type": "manual_setup",
        "last_alert_time": datetime.now(timezone.utc).isoformat(),
    }
    updated = state.db.update_manual_setup(setup_id, {k: v for k, v in patch.items() if v is not None}) or row
    return {"success": tg_sent, "setup": _shape_setup(updated, telegram_alert_sent=tg_sent)}


@router.patch("/manual-setups/{setup_id}")
def patch_manual_setup(setup_id: int, body: SetupUpdate):
    return update_setup(setup_id, body)


@router.delete("/setups/{setup_id}")
def delete_setup(setup_id: int):
    if not state.db_ready:
        raise HTTPException(status_code=503, detail="Database not available")

    try:
        ok = state.db.delete_manual_setup(setup_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Setup not found")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        state.mark_db_failure()
        raise HTTPException(status_code=503, detail=f"Database unavailable: {exc}")
