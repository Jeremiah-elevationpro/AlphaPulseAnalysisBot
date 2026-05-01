from datetime import datetime, timezone
from fastapi import APIRouter
import api.state as state

router = APIRouter()

_started_at = datetime.now(timezone.utc)


@router.get("/health")
def health_check():
    uptime_s = int((datetime.now(timezone.utc) - _started_at).total_seconds())
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)

    # Try to count active trades as a simple DB probe
    active_count = 0
    db_ok = state.db_ready
    if db_ok:
        try:
            active_count = len(state.db.get_active_trades())
        except Exception:
            db_ok = False

    return {
        "status": "ok" if db_ok else "degraded",
        "db_connected": db_ok,
        "active_trades": active_count,
        "pair": "XAUUSD",
        "timeframe": "M30→M15",
        "uptime": f"{h:02d}:{m:02d}:{s:02d}",
        "uptime_seconds": uptime_s,
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/system/data-health")
def data_health():
    hb = state.read_heartbeat() or {}
    now = datetime.now(timezone.utc)
    last_scan_at = hb.get("last_scan_at")
    last_alert = hb.get("last_telegram_alert_time") or (hb.get("telegram") or {}).get("last_alert_time")
    try:
        last_scan_dt = datetime.fromisoformat(str(last_scan_at).replace("Z", "+00:00")) if last_scan_at else None
        last_scan_age = int((now - last_scan_dt).total_seconds()) if last_scan_dt else None
    except Exception:
        last_scan_age = None

    manual_rows = []
    analytics_unknown_session_count = 0
    analytics_unknown_micro_count = 0
    warnings: list[str] = []

    if state.db_ready:
        try:
            manual_rows = state.db.get_watching_manual_setups()
        except Exception:
            warnings.append("manual_setup_fetch_failed")
        try:
            replay_run = state.db.get_latest_replay_run() if hasattr(state.db, "get_latest_replay_run") else None
            rows = state.db.get_replay_trades(replay_run["id"], limit=2000) if replay_run else []
            analytics_unknown_session_count = sum(
                1 for row in rows
                if not (row.get("session_name") or row.get("session") or row.get("market_session"))
            )
            analytics_unknown_micro_count = sum(
                1 for row in rows
                if not (row.get("micro_confirmation_type") or row.get("confirmation_type") or row.get("confirmation_pattern"))
            )
        except Exception:
            warnings.append("analytics_probe_failed")

    if hb and not hb.get("last_telegram_status") and not (hb.get("telegram") or {}).get("last_status"):
        warnings.append("telegram_status_missing")

    return {
        "heartbeat_ok": bool(hb),
        "telegram_ok": (hb.get("last_telegram_status") or (hb.get("telegram") or {}).get("last_status")) not in {"failed", None, ""},
        "active_instance_id": hb.get("instance_id"),
        "last_scan_age_seconds": last_scan_age,
        "last_alert_sent_at": last_alert,
        "manual_tracking_count": len(manual_rows),
        "analytics_unknown_session_count": analytics_unknown_session_count,
        "analytics_unknown_micro_count": analytics_unknown_micro_count,
        "warnings": warnings,
    }
