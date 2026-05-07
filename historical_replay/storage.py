from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from db.database import Database
from utils.logger import get_logger

logger = get_logger(__name__)


class ReplayStorage:
    """Thin Supabase persistence adapter for replay runs, trades, and stats."""

    def __init__(self, db: Database):
        self._db = db

    def start_run(
        self,
        *,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        strategy_version: str,
        notes: str = "",
    ) -> int:
        payload = {
            "symbol": symbol,
            "started_at": datetime.utcnow(),
            "replay_start": start_time,
            "replay_end": end_time,
            "strategy_version": strategy_version,
            "status": "running",
            "notes": notes,
        }
        replay_run_id = self._db.create_replay_run(payload)
        if replay_run_id is None:
            raise RuntimeError("Supabase did not return a replay run id")
        logger.info("Historical replay run created: id=%s", replay_run_id)
        return int(replay_run_id)

    def finish_run(self, replay_run_id: int, *, status: str, counters: Dict[str, Any]):
        self._db.update_replay_run(
            replay_run_id,
            {
                "finished_at": datetime.utcnow(),
                "status": status,
                "total_watchlists": counters.get("total_watchlists", 0),
                "total_pending_order_ready": counters.get("total_pending_order_ready", 0),
                "total_activated_trades": counters.get("total_activated_trades", 0),
                "total_wins": counters.get("total_wins", 0),
                "total_losses": counters.get("total_losses", 0),
            },
        )

    def fail_run(self, replay_run_id: int, error: str):
        self._db.update_replay_run(
            replay_run_id,
            {
                "finished_at": datetime.utcnow(),
                "status": "failed",
                "notes": error[:1000],
            },
        )

    def insert_trade(self, replay_run_id: int, payload: Dict[str, Any]) -> Optional[int]:
        payload = dict(payload)
        payload["replay_run_id"] = replay_run_id
        return self._db.insert_replay_trade(payload)

    def insert_stats(self, replay_run_id: int, payload: Dict[str, Any]) -> Optional[int]:
        payload = dict(payload)
        payload["replay_run_id"] = replay_run_id
        return self._db.insert_replay_stats(payload)
