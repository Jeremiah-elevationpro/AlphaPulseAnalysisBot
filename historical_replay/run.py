from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import REPLAY_DEFAULT_MONTHS
from historical_replay.engine import HistoricalReplayEngine


def main():
    parser = argparse.ArgumentParser(description="Run AlphaPulse historical strategy replay.")
    parser.add_argument("--months", type=int, default=REPLAY_DEFAULT_MONTHS)
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    args = parser.parse_args()

    engine = HistoricalReplayEngine()
    if args.start and args.end:
        start = _parse_utc(args.start)
        end = _parse_utc(args.end)
        result = engine.run(start=start, end=end)
    else:
        result = engine.run_last_months(months=args.months)

    print(result)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    main()
