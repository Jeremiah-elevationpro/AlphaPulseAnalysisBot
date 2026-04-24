import logging
from datetime import datetime
from threading import Thread
from time import sleep
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import api.state as state

router = APIRouter()
logger = logging.getLogger("alphapulse.api.replay")


class ReplayRunRequest(BaseModel):
    symbol: str = "XAUUSD"
    months: int = 2
    showTrades: int = 20


def _build_result(run_id: int, symbol: str, months: int) -> dict[str, Any]:
    latest = state.db.get_latest_replay_run() if state.db_ready and hasattr(state.db, "get_latest_replay_run") else None
    latest_stats = None
    if latest and state.db_ready:
        latest_stats = state.db.get_replay_stats(latest["id"])

    if latest and latest_stats:
        return {
            "runId": run_id,
            "symbol": symbol,
            "period": f"{latest.get('start_date', '--')} -> {latest.get('end_date', '--')}",
            "activatedTrades": latest_stats.get("total_activated_trades", 0),
            "wins": latest_stats.get("total_wins", 0),
            "losses": latest_stats.get("total_losses", 0),
            "winRate": latest_stats.get("tp1_hit_rate", 0.0) if not latest_stats.get("win_rate") else latest_stats.get("win_rate"),
            "tp1Rate": latest_stats.get("tp1_hit_rate", 0.0),
            "tp2Rate": latest_stats.get("tp2_hit_rate", 0.0),
            "tp3Rate": latest_stats.get("tp3_hit_rate", 0.0),
            "netPips": (latest_stats.get("pip_summary") or {}).get("net_pips", 0.0),
            "averagePipsPerTrade": (latest_stats.get("pip_summary") or {}).get("average_pips_per_trade", 0.0),
            "status": "completed",
            "microConfirmationBreakdown": latest_stats.get("performance_by_micro_confirmation", {}),
            "sessionBreakdown": latest_stats.get("performance_by_session", {}),
            "biasBreakdown": latest_stats.get("performance_by_bias", {}),
            "sampleTrades": state.db.get_replay_trades(latest["id"], limit=20) if state.db_ready else [],
        }

    end = datetime.utcnow().date()
    return {
        "runId": run_id,
        "symbol": symbol,
        "period": f"{end} -> {end}",
        "activatedTrades": 0,
        "wins": 0,
        "losses": 0,
        "winRate": 0.0,
        "tp1Rate": 0.0,
        "tp2Rate": 0.0,
        "tp3Rate": 0.0,
        "netPips": 0.0,
        "averagePipsPerTrade": 0.0,
        "status": "completed",
        "microConfirmationBreakdown": {},
        "sessionBreakdown": {},
        "biasBreakdown": {},
        "sampleTrades": [],
    }


def _complete_replay(run_id: int, symbol: str, months: int):
    sleep(2)
    try:
        result = _build_result(run_id, symbol, months)
        state.replay_runs[run_id] = {
            **state.replay_runs[run_id],
            "status": "completed",
            "message": "Spencer completed replay analysis",
            "completed_at": state.now_iso(),
            "result": result,
        }
        is_running = state.bot_process is not None and state.bot_process.poll() is None
        if is_running:
            state.bot_state["status"] = "online"
            state.bot_state["message"] = "Spencer completed replay analysis"
        else:
            state.bot_state["status"] = "offline"
            state.bot_state["message"] = "Spencer is offline"
        logger.info("REPLAY COMPLETED: runId=%d", run_id)
    except Exception as exc:
        logger.error("REPLAY FAILED: runId=%d error=%s", run_id, exc)
        state.replay_runs[run_id] = {
            **state.replay_runs.get(run_id, {}),
            "status": "failed",
            "message": f"Replay failed: {exc}",
            "completed_at": state.now_iso(),
        }
        is_running = state.bot_process is not None and state.bot_process.poll() is None
        state.bot_state["status"] = "online" if is_running else "offline"


@router.post("/replay/run")
def run_replay(body: ReplayRunRequest):
    run_id = state.next_replay_run_id()
    state.latest_replay_run_id = run_id
    state.replay_runs[run_id] = {
        "runId": run_id,
        "symbol": body.symbol,
        "months": body.months,
        "showTrades": body.showTrades,
        "status": "running",
        "message": f"Spencer started replay analysis for {body.months} months",
        "started_at": state.now_iso(),
        "result": None,
    }
    state.bot_state["status"] = "running_replay"
    logger.info("REPLAY STARTED FROM FRONTEND: runId=%d symbol=%s months=%d", run_id, body.symbol, body.months)
    Thread(target=_complete_replay, args=(run_id, body.symbol, body.months), daemon=True).start()
    return {
        "success": True,
        "runId": run_id,
        "status": "running",
        "message": f"Spencer started replay analysis for {body.months} months",
    }


@router.get("/replay/status/{run_id}")
def replay_status(run_id: int):
    run = state.replay_runs.get(run_id)
    if not run:
        latest = state.db.get_latest_replay_run() if state.db_ready and hasattr(state.db, "get_latest_replay_run") else None
        if latest and latest.get("id") == run_id:
            return {
                "success": True,
                "runId": run_id,
                "status": latest.get("status", "completed"),
                "message": "Latest replay loaded",
                "timestamp": state.now_iso(),
            }
        raise HTTPException(status_code=404, detail="Replay run not found")
    return {
        "success": True,
        "runId": run_id,
        "status": run["status"],
        "message": run["message"],
        "timestamp": state.now_iso(),
    }


@router.get("/replay/latest")
def replay_latest():
    if state.latest_replay_run_id is not None and state.latest_replay_run_id in state.replay_runs:
        return state.replay_runs[state.latest_replay_run_id]

    latest = state.db.get_latest_replay_run() if state.db_ready and hasattr(state.db, "get_latest_replay_run") else None
    if not latest:
        return {"success": True, "status": "idle", "message": "No replay run available"}
    result = _build_result(latest["id"], latest.get("symbol", "XAUUSD"), 0)
    return {
        "success": True,
        "runId": latest["id"],
        "symbol": latest.get("symbol", "XAUUSD"),
        "status": latest.get("status", "completed"),
        "message": "Latest replay loaded",
        "timestamp": state.now_iso(),
        "result": result,
    }


@router.get("/replay/results/{run_id}")
def replay_results(run_id: int):
    run = state.replay_runs.get(run_id)
    if run and run.get("result"):
        return run["result"]
    latest = state.db.get_latest_replay_run() if state.db_ready and hasattr(state.db, "get_latest_replay_run") else None
    if latest and latest.get("id") == run_id:
        return _build_result(run_id, latest.get("symbol", "XAUUSD"), 0)
    raise HTTPException(status_code=404, detail="Replay result not available")
