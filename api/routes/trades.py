from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query
import api.state as state

router = APIRouter()


def _safe_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _shape_trade(row: dict) -> dict:
    """Normalize a DB row into a consistent API shape."""
    return {
        "id":           row.get("id"),
        "uuid":         row.get("trade_uuid"),
        "pair":         row.get("pair", "XAUUSD"),
        "direction":    row.get("direction"),
        "entry_price":  _safe_float(row.get("entry_price")),
        "sl_price":     _safe_float(row.get("sl_price")),
        "tp1":          _safe_float(row.get("tp1")),
        "tp2":          _safe_float(row.get("tp2")),
        "tp3":          _safe_float(row.get("tp3")),
        "level_type":   row.get("level_type"),
        "level_price":  _safe_float(row.get("level_price")),
        "higher_tf":    row.get("higher_tf"),
        "lower_tf":     row.get("lower_tf"),
        "status":       row.get("status"),
        "result":       row.get("result"),
        "confidence":   _safe_float(row.get("confidence")),
        "strategy_type": row.get("strategy_type"),
        "source": row.get("source"),
        "setup_type":   row.get("setup_type"),
        "is_qm":        row.get("is_qm", False),
        "is_psychological": row.get("is_psychological", False),
        "h4_bias":      row.get("h4_bias"),
        "dominant_bias": row.get("dominant_bias"),
        "bias_strength": row.get("bias_strength"),
        "session_name": row.get("session_name"),
        "confirmation_type": row.get("confirmation_type"),
        "confirmation_score": _safe_float(row.get("confirmation_score")),
        "confirmation_path": row.get("confirmation_path"),
        "quality_rejection_count": row.get("quality_rejection_count"),
        "structure_break_count": row.get("structure_break_count"),
        "level_timeframe": row.get("level_timeframe"),
        "confluence_with": row.get("confluence_with"),
        "tp_progress_reached": row.get("tp_progress_reached", 0),
        "realized_pips": _safe_float(row.get("realized_pips")),
        "created_at":   str(row.get("created_at", "")),
        "activated_at": str(row.get("activated_at", "")) if row.get("activated_at") else None,
        "closed_at":    str(row.get("closed_at", "")) if row.get("closed_at") else None,
    }


@router.get("/trades")
def list_trades(
    status: str = Query("all", description="Filter: all | active | closed"),
    limit: int = Query(50, ge=1, le=500),
):
    if not state.db_ready:
        return {"trades": [], "total": 0, "db_ready": False}

    try:
        if status == "active":
            rows = state.db.get_active_trades()
        elif status == "closed":
            rows = state.db.get_closed_trades(limit=limit)
        else:
            # Today + recent active
            rows = state.db.get_today_trades()
            if len(rows) < limit:
                active = state.db.get_active_trades()
                seen = {r.get("id") for r in rows}
                rows += [r for r in active if r.get("id") not in seen]
            rows = rows[:limit]

        trades = [_shape_trade(r) for r in rows]
        return {"trades": trades, "total": len(trades), "db_ready": True}

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/trades/active")
def active_trades():
    if not state.db_ready:
        return {"trades": [], "total": 0}
    try:
        rows = state.db.get_active_trades()
        return {"trades": [_shape_trade(r) for r in rows], "total": len(rows)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/trades/{trade_uuid}")
def get_trade(trade_uuid: str):
    if not state.db_ready:
        raise HTTPException(status_code=503, detail="Database not available")
    row = state.db.get_trade(trade_uuid)
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")
    return _shape_trade(row)
