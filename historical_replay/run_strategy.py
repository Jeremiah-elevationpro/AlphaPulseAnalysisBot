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
from historical_replay.export_strategy_learning import export_strategy_learning, GAP_SWEEP
from utils.strategy_registry import canonical_strategy_type

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
        default=GAP_SWEEP,
        choices=[
            "alphapulse",
            "gap_sweep",
            GAP_SWEEP,
            "engulfing",
            "engulfing_rejection",
            "break_retest",
            STRATEGY_STANDARD,
            "failed_engulf",
            STRATEGY_FAILED_ENGULF,
        ],
        help="Strategy replay to run. Research strategies store into strategy_research_* tables.",
    )
    parser.add_argument("--months", type=int, default=REPLAY_DEFAULT_MONTHS)
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--symbol", type=str, default=SYMBOL)
    parser.add_argument("--show-trades", type=int, default=20, dest="show_trades")
    parser.add_argument("--export-learning", action="store_true", dest="export_learning")
    parser.add_argument("--source-strategy", type=str, default="engulfing_rejection")
    args = parser.parse_args()

    strategy_name = GAP_SWEEP if args.strategy == "alphapulse" else canonical_strategy_type(args.strategy)
    engine = _build_engine(strategy_name, source_strategy=canonical_strategy_type(args.source_strategy))
    if strategy_name in _RESEARCH_STRATEGIES:
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

    if args.export_learning:
        db = getattr(engine, "db", None)
        source_run_id = result.get("run_id") or result.get("replay_run_id") if isinstance(result, dict) else None
        can_export = db is not None and (source_run_id is not None or strategy_name == GAP_SWEEP)
        export_result = export_strategy_learning(
            db,
            strategy_type=strategy_name,
            run_id=source_run_id,
        ) if can_export else {
            "strategy_type": strategy_name,
            "source": "research" if strategy_name in _RESEARCH_STRATEGIES else "unknown",
            "source_run_id": source_run_id,
            "activated_trades": result.get("total_trades", 0) if isinstance(result, dict) else 0,
            "rows_exported": 0,
            "duplicates_skipped": 0,
            "invalid_skipped": 0,
            "learning_valid": 0,
            "skipped": 0,
            "profiles_upserted": 0,
            "errors": ["learning export skipped: missing replay/research run id"],
        }
        result["learning_export"] = export_result
        if (
            db is not None
            and strategy_name in _RESEARCH_STRATEGIES
            and (result.get("run_id") or result.get("replay_run_id"))
        ):
            try:
                db.update_strategy_research_run(
                    result.get("run_id") or result.get("replay_run_id"),
                    {
                        "export_learning": True,
                        "rows_exported": export_result.get("rows_exported", 0),
                        "learning_valid_count": export_result.get("learning_valid", 0),
                        "learning_skipped_count": export_result.get("skipped", 0),
                        "summary": {
                            **result,
                            "learning_export": export_result,
                        },
                    },
                )
            except Exception as exc:
                export_result["run_update_error"] = str(exc)

    print(result)


def _build_engine(strategy: str, *, source_strategy: str = "engulfing_rejection"):
    if strategy == "engulfing_rejection":
        return EngulfingResearchEngine()
    if strategy in (STRATEGY_STANDARD, STRATEGY_FAILED_ENGULF):
        return BreakRetestResearchEngine(strategy_type=strategy, source_strategy=source_strategy)
    return HistoricalReplayEngine()


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    main()
