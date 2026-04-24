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
        "status": "online" if db_ok else "degraded",
        "db_connected": db_ok,
        "active_trades": active_count,
        "pair": "XAUUSD",
        "timeframe": "M30→M15",
        "uptime": f"{h:02d}:{m:02d}:{s:02d}",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
