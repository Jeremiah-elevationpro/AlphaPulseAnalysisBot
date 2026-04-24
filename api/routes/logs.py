from collections import deque
from pathlib import Path

from fastapi import APIRouter, Query

import api.state as state

router = APIRouter()

# Keywords for the legacy strategy-detail log filter
IMPORTANT_PATTERNS = (
    "started successfully",
    "analysis phase complete",
    "setup watchlist alerts sent",
    "watchlist alert sent",
    "gap accepted",
    "level accepted",
    "pending-order-ready",
    "send_confirmation",
    "trade activated",
    "activated for tracking",
    "tp1",
    "tp2",
    "tp3",
    "sl hit",
    "shutdown",
    "running replay",
    "replay completed",
    "scan complete",
    "scan started",
    "no watchlist setups found",
    "watchlist loop started",
)

TELEGRAM_PATTERNS = (
    "telegram ",
    "watchlist alert calling telegram",
    "watchlist alert sent",
    "watchlist alert failed",
)


def _read_tail(log_path: Path, limit: int, predicate=None):
    if not log_path.exists():
        return {"events": [], "source": str(log_path), "exists": False}

    matched: deque = deque(maxlen=limit)
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip()
            if not line:
                continue
            if predicate is None or predicate(line):
                matched.append(line)

    events = [{"id": i + 1, "line": line} for i, line in enumerate(reversed(matched))]
    return {"events": events, "source": str(log_path), "exists": True}


@router.get("/logs/runtime")
def runtime_logs(limit: int = Query(default=100, ge=1, le=500)):
    """
    Read logs/spencer_runtime.log — the live high-level event log written
    by the bot process each scan cycle.  Returns newest events first.
    """
    return _read_tail(state.RUNTIME_LOG_FILE, limit)


@router.get("/logs/activity")
def activity_logs(limit: int = Query(default=30, ge=1, le=200)):
    """
    Read logs/alphapulse.log filtered to operationally important lines.
    This is the strategy-detail log (verbose detector / confirmation output).
    """
    log_path = state.ROOT_DIR / "logs" / "alphapulse.log"
    return _read_tail(
        log_path,
        limit,
        predicate=lambda line: any(pattern in line.lower() for pattern in IMPORTANT_PATTERNS),
    )


@router.get("/logs/replay")
def replay_logs(limit: int = Query(default=100, ge=1, le=500)):
    """
    Read the replay log so Analytics can show current replay execution details.
    """
    log_path = state.ROOT_DIR / "logs" / "historical_replay.log"
    return _read_tail(log_path, limit)


@router.get("/logs/telegram")
def telegram_logs(limit: int = Query(default=100, ge=1, le=500)):
    """
    Show only Telegram-related runtime events so send attempts/success/failure
    are visible without mixing in the full scan lifecycle feed.
    """
    return _read_tail(
        state.RUNTIME_LOG_FILE,
        limit,
        predicate=lambda line: any(pattern in line.lower() for pattern in TELEGRAM_PATTERNS),
    )
