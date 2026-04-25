from __future__ import annotations

import argparse
import os
from datetime import datetime, timezone

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import REPLAY_DEFAULT_MONTHS, SYMBOL
from historical_replay.engine import HistoricalReplayEngine
from historical_replay.engulfing_research import EngulfingResearchEngine
from historical_replay.break_retest_research import (
    BreakRetestResearchEngine,
    STRATEGY_STANDARD,
    STRATEGY_FAILED_ENGULF,
)

_RESEARCH_STRATEGIES = {
    "engulfing_rejection",
    STRATEGY_STANDARD,
    STRATEGY_FAILED_ENGULF,
}


def main():
    parser = argparse.ArgumentParser(
        description="Run AlphaPulse strategy replay or research replay.",
    )
    parser.add_argument(
        "--strategy",
        type=str,
        default="alphapulse",
        choices=[
            "alphapulse",
            "engulfing_rejection",
            STRATEGY_STANDARD,
            STRATEGY_FAILED_ENGULF,
        ],
        help="Strategy replay to run. Research strategies store into strategy_research_* tables.",
    )
    parser.add_argument("--months", type=int, default=REPLAY_DEFAULT_MONTHS)
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--symbol", type=str, default=SYMBOL)
    parser.add_argument("--show-trades", type=int, default=20, dest="show_trades")
    args = parser.parse_args()

    engine = _build_engine(args.strategy)
    if args.strategy in _RESEARCH_STRATEGIES:
        if args.start and args.end:
            start = _parse_utc(args.start)
            end = _parse_utc(args.end)
            result = engine.run(
                start=start,
                end=end,
                symbol=args.symbol,
                show_trades=args.show_trades,
            )
        else:
            result = engine.run_last_months(
                months=args.months,
                symbol=args.symbol,
                show_trades=args.show_trades,
            )
    else:
        if args.start and args.end:
            start = _parse_utc(args.start)
            end = _parse_utc(args.end)
            result = engine.run(start=start, end=end, symbol=args.symbol)
        else:
            result = engine.run_last_months(months=args.months)

    print(result)


def _build_engine(strategy: str):
    if strategy == "engulfing_rejection":
        return EngulfingResearchEngine()
    if strategy in (STRATEGY_STANDARD, STRATEGY_FAILED_ENGULF):
        return BreakRetestResearchEngine(strategy_type=strategy)
    return HistoricalReplayEngine()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    main()
