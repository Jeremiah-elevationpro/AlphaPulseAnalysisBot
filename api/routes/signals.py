"""
Signals endpoint.
Active/Pending trades are surfaced as detected signals.
Closed trades with result data are excluded (they graduate to the Trades view).
"""
from fastapi import APIRouter, Query
import api.state as state

router = APIRouter()

SIGNAL_STATUSES = {"PENDING", "ACTIVE", "TP1_HIT"}


@router.get("/signals")
def list_signals(limit: int = Query(30, ge=1, le=200)):
    if not state.db_ready:
        return {"signals": [], "total": 0, "db_ready": False}

    try:
        rows = state.db.get_active_trades()
        signals = []
        for row in rows[:limit]:
            status = row.get("status", "")
            if status not in SIGNAL_STATUSES:
                continue
            entry = row.get("entry_price") or row.get("level_price")
            level_type = row.get("level_type", "A")
            direction = row.get("direction", "SELL")
            sig = {
                "id":          row.get("id"),
                "uuid":        row.get("trade_uuid"),
                "type":        level_type,
                "price":       float(entry) if entry else None,
                "level_price": float(row["level_price"]) if row.get("level_price") else None,
                "direction":   direction,
                "quality":     round(float(row.get("confidence", 0.5)) * 100),
                "displacement": float(row.get("realized_pips", 0) or 0),
                "touch_count": 1,
                "break_count": 0,
                "basis":       row.get("confirmation_type", "wick-based"),
                "timeframe":   row.get("higher_tf", "M30"),
                "status":      "active" if status == "ACTIVE" else "pending",
                "is_qm":       row.get("is_qm", False),
                "is_psych":    row.get("is_psychological", False),
                "h4_bias":     row.get("h4_bias"),
                "created_at":  str(row.get("created_at", "")),
            }
            signals.append(sig)

        return {"signals": signals, "total": len(signals), "db_ready": True}

    except Exception as exc:
        return {"signals": [], "total": 0, "error": str(exc), "db_ready": False}
