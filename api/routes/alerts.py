from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query

import api.state as state

router = APIRouter()


def _fmt_ts(value: Any) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    return str(value)


def _alert_priority(kind: str) -> str:
    if kind in {"SL hit", "system alerts"}:
        return "critical"
    if kind in {"activated", "pending-order-ready", "bot status alerts"}:
        return "high"
    if kind in {"TP1 hit", "move SL to BE", "TP2 hit", "TP3 hit"}:
        return "medium"
    return "low"


def _date_bucket(ts: str) -> str:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return "this week"
    now = datetime.now(timezone.utc)
    delta = now.date() - dt.date()
    if delta.days <= 0:
        return "today"
    if delta.days == 1:
        return "yesterday"
    return "this week"


def _status_to_alert_type(status: str, be_moved: bool) -> Optional[str]:
    if status == "PENDING":
        return "pending-order-ready"
    if status == "ACTIVE":
        return "activated"
    if status == "TP1_HIT":
        return "move SL to BE" if be_moved else "TP1 hit"
    if status == "TP2_HIT":
        return "TP2 hit"
    if status in {"TP3_HIT", "COMPLETED"}:
        return "TP3 hit"
    if status == "STOP_LOSS_HIT":
        return "SL hit"
    return None


@router.get("/alerts")
def list_alerts(limit: int = Query(50, ge=1, le=200)):
    alerts: list[dict[str, Any]] = []

    if not state.db_ready:
        return {"alerts": alerts, "total": 0, "db_ready": False}

    try:
        trades = state.db.get_active_trades() + state.db.get_closed_trades(limit=25)
        setups = state.db.get_manual_setups(limit=25)

        for row in trades:
            status = row.get("status", "")
            alert_type = _status_to_alert_type(status, bool(row.get("be_moved")))
            if not alert_type:
                continue
            ts = _fmt_ts(row.get("closed_at") or row.get("activated_at") or row.get("created_at"))
            alerts.append(
                {
                    "id": f"trade-{row.get('id')}-{status}",
                    "type": alert_type,
                    "title": f"{row.get('pair', 'XAUUSD')} {alert_type}",
                    "message": f"{row.get('direction', 'TRADE')} trade {row.get('trade_uuid', row.get('id'))} is now {status}.",
                    "priority": _alert_priority(alert_type),
                    "read": False,
                    "date_bucket": _date_bucket(ts),
                    "timestamp": ts,
                    "related_label": f"Trade {row.get('trade_uuid', row.get('id'))}",
                    "related_type": "trade",
                    "symbol": row.get("pair", "XAUUSD"),
                    "source": "bot",
                }
            )

        for row in setups:
            status = row.get("status", "draft")
            if status not in {"watching", "pending-order-ready", "activated"}:
                continue
            alert_type = "watch alert" if status == "watching" else status
            ts = _fmt_ts(row.get("updated_at") or row.get("created_at"))
            alerts.append(
                {
                    "id": f"setup-{row.get('id')}-{status}",
                    "type": alert_type,
                    "title": f"Manual setup {status}",
                    "message": f"{row.get('symbol', 'XAUUSD')} {row.get('direction', '')} setup is {status} and ready for tracking.",
                    "priority": _alert_priority(alert_type),
                    "read": False,
                    "date_bucket": _date_bucket(ts),
                    "timestamp": ts,
                    "related_label": f"Setup {row.get('id')}",
                    "related_type": "setup",
                    "symbol": row.get("symbol", "XAUUSD"),
                    "source": "manual",
                }
            )

        alerts.append(
            {
                "id": "system-db-status",
                "type": "bot status alerts",
                "title": "AlphaPulse API online",
                "message": "Frontend bridge is connected and serving Supabase-backed bot data.",
                "priority": "medium",
                "read": False,
                "date_bucket": "today",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "related_label": "API runtime",
                "related_type": "system",
                "symbol": "XAUUSD",
                "source": "system",
            }
        )

        alerts.sort(key=lambda item: item["timestamp"], reverse=True)
        alerts = alerts[:limit]
        return {"alerts": alerts, "total": len(alerts), "db_ready": True}
    except Exception as exc:
        return {"alerts": [], "total": 0, "db_ready": False, "error": str(exc)}
