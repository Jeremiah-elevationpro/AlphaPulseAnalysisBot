"""
AlphaPulse — Multi-Strategy Replay CLI
=======================================
Usage:
    python -m historical_replay.run_multi_strategy \\
        --strategies gap_sweep,engulfing_rejection \\
        --months 4

Options:
    --strategies   Comma-separated list of strategies (gap_sweep, engulfing_rejection)
    --months       Number of months to replay (default: 4)
    --symbol       Trading symbol (default: XAUUSD)
    --show-trades  Number of sample trades to show in output (default: 20)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import REPLAY_DEFAULT_MONTHS, SYMBOL


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run AlphaPulse multi-strategy replay.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--strategies",
        type=str,
        required=True,
        help="Comma-separated strategy names. Supported: gap_sweep, engulfing_rejection",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=REPLAY_DEFAULT_MONTHS,
        help="Months of history to replay (default: %(default)s)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=SYMBOL,
        help="Trading symbol (default: %(default)s)",
    )
    parser.add_argument(
        "--show-trades",
        type=int,
        default=20,
        dest="show_trades",
        help="Number of sample trades to print (default: %(default)s)",
    )
    parser.add_argument(
        "--start",
        type=str,
        default="",
        help="Start date ISO8601 (overrides --months)",
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="End date ISO8601 (overrides --months)",
    )
    args = parser.parse_args()

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    if not strategies:
        parser.error("--strategies must not be empty")

    from historical_replay.multi_strategy_engine import MultiStrategyReplayEngine

    engine = MultiStrategyReplayEngine(strategies=strategies)

    if args.start and args.end:
        start = _parse_utc(args.start)
        end   = _parse_utc(args.end)
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

    _print_summary(result)


def _print_summary(result: dict) -> None:
    run_id    = result.get("run_id", "?")
    total     = result.get("total_trades", 0)
    wins      = result.get("wins", 0)
    losses    = result.get("losses", 0)
    win_rate  = result.get("win_rate", 0.0)
    tp1_rate  = result.get("tp1_rate", 0.0)
    net_pips  = result.get("net_pips", 0.0)
    avg_pips  = result.get("avg_pips", 0.0)
    strategies = result.get("strategies", [])
    by_strat  = result.get("by_strategy", {})
    confluence = result.get("confluence_summary", {})
    learning   = result.get("learning_summary", {})

    print()
    print("=" * 52)
    print("  AlphaPulse Multi-Strategy Replay Complete")
    print("=" * 52)
    print(f"  Run ID     : {run_id}")
    print(f"  Strategies : {', '.join(strategies)}")
    print()
    print("  COMBINED RESULTS")
    print(f"  Trades     : {total}  ({wins}W / {losses}L)")
    print(f"  Win Rate   : {win_rate:.1f}%")
    print(f"  TP1 Rate   : {tp1_rate:.1f}%")
    print(f"  Net Pips   : {net_pips:.1f}")
    print(f"  Avg Pips   : {avg_pips:.1f}")
    print()
    print("  BY STRATEGY")
    for name, stats in by_strat.items():
        if "error" in stats:
            print(f"  [{name}]  ERROR: {stats['error']}")
        else:
            print(
                f"  [{name}]  "
                f"trades={stats.get('trades', 0)}  "
                f"WR={stats.get('win_rate', 0.0):.1f}%  "
                f"net={stats.get('net_pips', 0.0):.1f}p  "
                f"avg={stats.get('avg_pips', 0.0):.1f}p"
            )
    print()
    print("  CONFLUENCE")
    print(f"  Pairs detected : {confluence.get('confluence_pairs_detected', 0)}")
    print(f"  Window         : {confluence.get('window_hours', 4)}h / {confluence.get('level_distance_pips', 15)}p")
    print()
    print("  LEARNING PROFILES")
    print(f"  Profiles upserted: {learning.get('profiles_upserted', 0)}")
    print()
    print("  To evaluate results:")
    print(f"    python -m historical_replay.evaluate_multi_strategy --run-id {run_id} --show-trades 20")
    print("=" * 52)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    main()
