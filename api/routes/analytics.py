from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

import api.state as state

router = APIRouter()


WIN_RESULTS = {"WIN", "STRONG_WIN", "BREAKEVEN_WIN", "PARTIAL_WIN"}
LOSS_RESULTS = {"LOSS", "STOP_LOSS_HIT"}


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _result_label(row: dict) -> str:
    result = str(row.get("final_result") or row.get("result") or "").upper()
    status = str(row.get("status") or "").upper()
    if result in {"WIN", "STRONG_WIN", "TP3_HIT"} or status in {"TP3_HIT", "COMPLETED"}:
        return "win"
    if result in {"BREAKEVEN", "BREAKEVEN_WIN"} or row.get("breakeven_exit"):
        return "breakeven"
    if result in LOSS_RESULTS or status == "STOP_LOSS_HIT":
        return "loss"
    if result == "PARTIAL_WIN" or status == "TP2_HIT":
        return "partial"
    return "open"


def _period_label(ts: str, mode: str = "week") -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return "Unknown"
    return f"W{dt.isocalendar().week}" if mode == "week" else dt.strftime("%b %Y")


def _empty() -> dict:
    return {
        "db_ready": False,
        "metrics": {
            "total_trades": 0,
            "win_rate": 0.0,
            "tp1_hit_rate": 0.0,
            "net_pips": 0.0,
            "avg_pips_per_trade": 0.0,
        },
        "charts": {
            "cumulative_pips": [],
            "session_performance": [],
            "micro_confirmation_performance": [],
            "win_loss_distribution": [],
            "performance_by_bias": [],
            "performance_by_period": [],
        },
        "breakdowns": {
            "session": [],
            "setup_type": [],
            "micro_confirmation": [],
            "bias_gate": [],
            "outcome_mix": [],
        },
    }


@router.get("/analytics")
def get_analytics(
    session: str = Query("all"),
    confirmation_type: str = Query("all"),
    symbol: str = Query("all"),
):
    if not state.db_ready:
        return _empty()

    try:
        replay_run = state.db.get_latest_replay_run() if hasattr(state.db, "get_latest_replay_run") else None
        rows = []
        if replay_run:
            rows = state.db.get_replay_trades(replay_run["id"], limit=10000)
        if not rows:
            rows = state.db.get_all_closed_trades()

        filtered = []
        for row in rows:
            row_symbol = row.get("pair") or row.get("symbol") or "XAUUSD"
            row_session = row.get("session_name") or row.get("session") or "unknown"
            row_micro = row.get("micro_confirmation_type") or row.get("confirmation_type") or "unknown"
            if symbol != "all" and row_symbol != symbol:
                continue
            if session != "all" and row_session != session:
                continue
            if confirmation_type != "all" and row_micro != confirmation_type:
                continue
            filtered.append(row)

        total = len(filtered)
        wins = 0
        tp1_hits = 0
        total_pips = 0.0
        cumulative = []
        running_pips = 0.0

        session_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "tp1": 0, "net_pips": 0.0})
        setup_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pips": 0.0})
        micro_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "tp1": 0, "net_pips": 0.0})
        bias_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pips": 0.0})
        outcome_stats: dict[str, int] = defaultdict(int)
        period_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pips": 0.0})

        for row in filtered:
            outcome = _result_label(row)
            row_pips = _safe_float(row.get("final_pips"), _safe_float(row.get("realized_pips")))
            row_session = row.get("session_name") or row.get("session") or "unknown"
            row_setup = row.get("setup_type") or row.get("level_type") or "Gap"
            row_micro = row.get("micro_confirmation_type") or row.get("confirmation_type") or "unknown"
            row_bias = row.get("bias_gate_result") or row.get("h4_bias") or row.get("bias") or "unknown"
            row_time = str(row.get("timestamp") or row.get("closed_at") or row.get("created_at") or "")

            is_win = outcome in {"win", "partial"}
            if is_win:
                wins += 1
            if (row.get("tp_progress_reached") or 0) >= 1:
                tp1_hits += 1
            total_pips += row_pips
            running_pips += row_pips
            cumulative.append({"label": row_time[:10] if row_time else "Unknown", "pips": round(running_pips, 1)})

            session_stats[row_session]["trades"] += 1
            session_stats[row_session]["wins"] += 1 if is_win else 0
            session_stats[row_session]["tp1"] += 1 if (row.get("tp_progress_reached") or 0) >= 1 else 0
            session_stats[row_session]["net_pips"] += row_pips

            setup_stats[row_setup]["trades"] += 1
            setup_stats[row_setup]["wins"] += 1 if is_win else 0
            setup_stats[row_setup]["net_pips"] += row_pips

            micro_stats[row_micro]["trades"] += 1
            micro_stats[row_micro]["wins"] += 1 if is_win else 0
            micro_stats[row_micro]["tp1"] += 1 if (row.get("tp_progress_reached") or 0) >= 1 else 0
            micro_stats[row_micro]["net_pips"] += row_pips

            bias_stats[row_bias]["trades"] += 1
            bias_stats[row_bias]["wins"] += 1 if is_win else 0
            bias_stats[row_bias]["net_pips"] += row_pips

            outcome_stats[outcome] += 1

            period = _period_label(row_time, "week")
            period_stats[period]["trades"] += 1
            period_stats[period]["wins"] += 1 if is_win else 0
            period_stats[period]["net_pips"] += row_pips

        def _rate(w: int, t: int) -> float:
            return round((w / t) * 100, 1) if t else 0.0

        metrics = {
            "total_trades": total,
            "win_rate": _rate(wins, total),
            "tp1_hit_rate": _rate(tp1_hits, total),
            "net_pips": round(total_pips, 1),
            "avg_pips_per_trade": round(total_pips / total, 1) if total else 0.0,
        }

        return {
            "db_ready": True,
            "metrics": metrics,
            "charts": {
                "cumulative_pips": cumulative,
                "session_performance": [
                    {
                        "name": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                        "tp1_rate": _rate(value["tp1"], value["trades"]),
                    }
                    for key, value in session_stats.items()
                ],
                "micro_confirmation_performance": [
                    {
                        "name": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in micro_stats.items()
                ],
                "win_loss_distribution": [
                    {"name": "Wins", "value": outcome_stats.get("win", 0), "color": "#10B981"},
                    {"name": "Breakeven", "value": outcome_stats.get("breakeven", 0), "color": "#D4AF37"},
                    {"name": "Losses", "value": outcome_stats.get("loss", 0), "color": "#EF4444"},
                ],
                "performance_by_bias": [
                    {
                        "name": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in bias_stats.items()
                ],
                "performance_by_period": [
                    {
                        "label": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in sorted(period_stats.items())
                ],
            },
            "breakdowns": {
                "session": [
                    {
                        "session": key,
                        "trades": value["trades"],
                        "wins": value["wins"],
                        "tp1": value["tp1"],
                        "net_pips": round(value["net_pips"], 1),
                        "avg_pips": round(value["net_pips"] / value["trades"], 1) if value["trades"] else 0.0,
                    }
                    for key, value in session_stats.items()
                ],
                "setup_type": [
                    {
                        "setup_type": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in setup_stats.items()
                ],
                "micro_confirmation": [
                    {
                        "micro": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "tp1_rate": _rate(value["tp1"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in micro_stats.items()
                ],
                "bias_gate": [
                    {
                        "bias_gate": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in bias_stats.items()
                ],
                "outcome_mix": [
                    {"outcome": "Full Win", "trades": outcome_stats.get("win", 0), "color": "text-buy"},
                    {"outcome": "Partial Win", "trades": outcome_stats.get("partial", 0), "color": "text-gold-400"},
                    {"outcome": "Breakeven", "trades": outcome_stats.get("breakeven", 0), "color": "text-muted-foreground"},
                    {"outcome": "Loss", "trades": outcome_stats.get("loss", 0), "color": "text-sell"},
                ],
            },
        }
    except Exception as exc:
        return {**_empty(), "error": str(exc)}
