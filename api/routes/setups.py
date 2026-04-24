from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import api.state as state

router = APIRouter()


Direction = Literal["BUY", "SELL"]
StatusValue = Literal[
    "draft",
    "watching",
    "pending-order-ready",
    "activated",
    "TP1 hit",
    "BE protected",
    "TP2 hit",
    "TP3 hit",
    "stopped out",
    "closed manually",
    "expired",
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


def _shape_setup(row: dict) -> dict:
    created_at = row.get("created_at") or datetime.now(timezone.utc).isoformat()
    updated_at = row.get("updated_at") or created_at
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
        "created_at": str(created_at),
        "updated_at": str(updated_at),
    }


@router.get("/setups")
def list_setups():
    if not state.db_ready:
        return {"setups": [], "total": 0, "db_ready": False}

    try:
        rows = state.db.get_manual_setups(limit=500)
        setups = [_shape_setup(row) for row in rows]
        return {"setups": setups, "total": len(setups), "db_ready": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/setups", status_code=201)
def create_setup(body: SetupCreate):
    if not state.db_ready:
        raise HTTPException(status_code=503, detail="Database not available")

    payload = body.model_dump()
    payload["symbol"] = payload["symbol"].strip().upper()

    try:
        row = state.db.insert_manual_setup(payload)
        if not row:
            raise HTTPException(status_code=500, detail="Failed to create manual setup")
        return _shape_setup(row)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


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
        raise HTTPException(status_code=500, detail=str(exc))


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
        raise HTTPException(status_code=500, detail=str(exc))
