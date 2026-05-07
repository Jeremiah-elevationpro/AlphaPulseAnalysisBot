from collections import defaultdict

from fastapi import APIRouter

import api.state as state

router = APIRouter()

_STRATEGIES = [
    "gap_liquidity_sweep_reclaim",
    "engulfing_rejection",
    "standard_break_retest",
    "failed_engulf_break_retest",
]


@router.get("/learning/profiles")
def learning_profiles():
    if not state.db_ready:
        return {"profiles": [], "db_ready": False}

    rows = state.db.get_strategy_learning_profiles(limit=500)
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("strategy_type") or "unknown"].append(row)

    profiles = []
    for strategy in _STRATEGIES:
        items = grouped.get(strategy, [])
        sample_size = sum(int(item.get("sample_size") or 0) for item in items)
        wins = sum(int(item.get("wins") or 0) for item in items)
        losses = sum(int(item.get("losses") or 0) for item in items)
        net_pips = round(sum(float(item.get("net_pips") or 0.0) for item in items), 2)
        best_session = max(items, key=lambda item: float(item.get("win_rate") or 0.0), default={}).get("session_name")
        best_timeframe = max(items, key=lambda item: float(item.get("win_rate") or 0.0), default={}).get("timeframe")
        profiles.append({
            "strategy_type": strategy,
            "sample_size": sample_size,
            "wins": wins,
            "losses": losses,
            "win_rate": round((wins / (wins + losses)) * 100, 2) if (wins + losses) else 0.0,
            "net_pips": net_pips,
            "best_session": best_session,
            "best_timeframe": best_timeframe,
            "status": "ready" if items else "no data",
        })
    return {"profiles": profiles, "db_ready": True}
