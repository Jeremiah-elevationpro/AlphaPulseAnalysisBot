"""
AlphaPulse — Multi-Strategy Replay Evaluator
=============================================
Reads results from multi_strategy_replay_runs / multi_strategy_replay_trades
and prints a structured performance report.

Usage:
    python -m historical_replay.evaluate_multi_strategy --run-id latest --show-trades 20
    python -m historical_replay.evaluate_multi_strategy --run-id 7 --show-trades 30
"""

from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from db.database import Database

_CLOSED_RESULTS = {"LOSS", "BREAKEVEN_WIN", "PARTIAL_WIN", "WIN", "STRONG_WIN"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate a multi-strategy replay run.",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default="latest",
        dest="run_id",
        help="Multi-strategy run ID or 'latest' (default: latest)",
    )
    parser.add_argument(
        "--show-trades",
        type=int,
        default=20,
        dest="show_trades",
        help="Number of sample trades to print per strategy (default: 20)",
    )
    args = parser.parse_args()

    db = Database()
    try:
        db.init()
        if args.run_id == "latest":
            run = db.get_latest_multi_strategy_replay_run()
        else:
            run = db.get_multi_strategy_replay_run(int(args.run_id))

        if not run:
            raise SystemExit("No multi-strategy replay run found.")

        run_id = run.get("id")
        trades = db.get_multi_strategy_replay_trades(run_id)
        print(build_report(run, trades, show_trades=args.show_trades))
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
# Report builder
# ─────────────────────────────────────────────────────────────────────────────

def build_report(run: Dict, trades: List[Dict], *, show_trades: int = 20) -> str:
    all_closed = [t for t in trades if (t.get("final_result") or "") in _CLOSED_RESULTS]
    strategies  = _unique_strategies(trades)

    lines = [
        "",
        "=" * 56,
        "  AlphaPulse Multi-Strategy Replay Report",
        "=" * 56,
        f"  Run ID     : {run.get('id')}",
        f"  Strategies : {', '.join(strategies)}",
        f"  Symbol     : {run.get('symbol', 'XAUUSD')}",
        f"  Period     : {run.get('replay_start')} → {run.get('replay_end')}",
        f"  Status     : {run.get('status')}",
        "",
        _section("COMBINED RESULTS"),
        _combined_stats(all_closed),
        "",
        _section("BY STRATEGY"),
        _by_strategy_report(all_closed, strategies),
        "",
        _section("SESSION PERFORMANCE"),
        _breakdown_per_strategy("Session", all_closed, "session_name", strategies),
        "",
        _section("TIMEFRAME PERFORMANCE"),
        _breakdown_per_strategy("Timeframe", all_closed, "timeframe", strategies),
        "",
        _section("BIAS PERFORMANCE"),
        _breakdown_per_strategy("Bias", all_closed, "dominant_bias", strategies),
        "",
        _section("CONFIRMATION TYPE"),
        _breakdown_per_strategy("Confirmation", all_closed, "confirmation_type", strategies),
        "",
        _section("CONFLUENCE"),
        _confluence_report(run),
        "",
        _section("LEARNING SUMMARY"),
        _learning_report(run),
        "",
        _section("OUTCOME MIX"),
        _format_counts(Counter(t.get("final_result") or "OPEN" for t in all_closed)),
    ]

    if all_closed and show_trades:
        for strategy in strategies:
            strategy_trades = [t for t in all_closed if t.get("strategy_type") == strategy][:show_trades]
            if not strategy_trades:
                continue
            lines.extend([
                "",
                _section(f"SAMPLE TRADES — {strategy.upper()} (first {len(strategy_trades)})"),
            ])
            for t in strategy_trades:
                lines.append(
                    "  {dir} {tf} bias={bias}/{strength} session={session} "
                    "conf={conf}({cscore}) entry={entry} result={result} pips={pips}".format(
                        dir=t.get("direction", "?"),
                        tf=t.get("timeframe") or t.get("timeframe_pair") or "?",
                        bias=t.get("dominant_bias", "?"),
                        strength=t.get("bias_strength", "?"),
                        session=t.get("session_name", "?"),
                        conf=t.get("confirmation_type", "?"),
                        cscore=_ff(t.get("confirmation_score")),
                        entry=_ff(t.get("entry")),
                        result=t.get("final_result", "?"),
                        pips=_ff(t.get("final_pips")),
                    )
                )

    lines.append("=" * 56)
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Section builders
# ─────────────────────────────────────────────────────────────────────────────

def _combined_stats(trades: List[Dict]) -> str:
    n = len(trades)
    if not n:
        return "  No closed trades."
    wins    = sum(1 for t in trades if t.get("final_result") != "LOSS")
    losses  = n - wins
    wr      = (wins / n) * 100
    net     = sum(_to_float(t.get("final_pips")) for t in trades)
    tp1_hit = sum(1 for t in trades if int(t.get("tp_progress") or 0) >= 1)
    tp2_hit = sum(1 for t in trades if int(t.get("tp_progress") or 0) >= 2)
    tp3_hit = sum(1 for t in trades if int(t.get("tp_progress") or 0) >= 3)
    return "\n".join([
        f"  Activated  : {n}",
        f"  Wins/Losses: {wins}/{losses}  ({wr:.1f}% WR)",
        f"  Net Pips   : {net:.1f}",
        f"  Avg Pips   : {(net/n):.1f}",
        f"  TP1 Rate   : {(tp1_hit/n*100):.1f}%",
        f"  TP2 Rate   : {(tp2_hit/n*100):.1f}%",
        f"  TP3 Rate   : {(tp3_hit/n*100):.1f}%",
    ])


def _by_strategy_report(trades: List[Dict], strategies: List[str]) -> str:
    lines = []
    for strategy in strategies:
        grp = [t for t in trades if t.get("strategy_type") == strategy]
        n = len(grp)
        if not n:
            lines.append(f"  [{strategy}]  no closed trades")
            continue
        w  = sum(1 for t in grp if t.get("final_result") != "LOSS")
        np = sum(_to_float(t.get("final_pips")) for t in grp)
        lines.append(
            f"  [{strategy}]  "
            f"trades={n}  wins={w}  losses={n-w}  "
            f"WR={w/n*100:.1f}%  net={np:.1f}p  avg={np/n:.1f}p"
        )
    return "\n".join(lines)


def _breakdown_per_strategy(
    label: str,
    trades: List[Dict],
    key: str,
    strategies: List[str],
) -> str:
    lines = []
    for strategy in strategies:
        grp = [t for t in trades if t.get("strategy_type") == strategy]
        breakdown = _group(grp, key)
        if not breakdown:
            continue
        lines.append(f"  [{strategy}]")
        for name, item in sorted(breakdown.items(), key=lambda p: p[1]["trades"], reverse=True):
            lines.append(
                f"    {name:22s}  trades={item['trades']:4d}  "
                f"WR={item['win_rate']:5.1f}%  "
                f"net={item['net_pips']:8.1f}p  avg={item['avg_pips']:6.1f}p"
            )
    return "\n".join(lines) if lines else "  no data"


def _confluence_report(run: Dict) -> str:
    import json
    raw = run.get("confluence_summary")
    if not raw:
        return "  no confluence data stored"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return "  (parse error)"
    return "\n".join([
        f"  Pairs detected : {data.get('confluence_pairs_detected', 0)}",
        f"  Window         : {data.get('window_hours', 4)}h",
        f"  Level distance : {data.get('level_distance_pips', 15)} pips",
    ])


def _learning_report(run: Dict) -> str:
    import json
    raw = run.get("learning_summary")
    if not raw:
        return "  no learning data stored"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return "  (parse error)"
    return (
        f"  Profiles upserted : {data.get('profiles_upserted', 0)}\n"
        f"  Multi Run ID       : {data.get('multi_run_id', '?')}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Grouping / formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _group(trades: Iterable[Dict], key: str) -> Dict[str, Dict]:
    grouped: Dict[str, Dict] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "net_pips": 0.0}
    )
    for t in trades:
        name = str(t.get(key) or "unknown")
        bucket = grouped[name]
        bucket["trades"]   += 1
        bucket["wins"]     += 1 if t.get("final_result") != "LOSS" else 0
        bucket["losses"]   += 1 if t.get("final_result") == "LOSS" else 0
        bucket["net_pips"] += _to_float(t.get("final_pips"))
    for bucket in grouped.values():
        n = bucket["trades"]
        bucket["win_rate"] = round((bucket["wins"] / n) * 100, 1) if n else 0.0
        bucket["net_pips"] = round(bucket["net_pips"], 2)
        bucket["avg_pips"] = round(bucket["net_pips"] / n, 2) if n else 0.0
    return dict(grouped)


def _unique_strategies(trades: List[Dict]) -> List[str]:
    seen = []
    for t in trades:
        s = t.get("strategy_type") or ""
        if s and s not in seen:
            seen.append(s)
    return seen


def _section(title: str) -> str:
    return f"  ── {title} {'─' * max(0, 46 - len(title))}"


def _format_counts(counts: Counter) -> str:
    return (
        "\n".join(f"  {name}: {count}" for name, count in counts.most_common())
        if counts else "  no data"
    )


def _to_float(v) -> float:
    try:
        return float(v or 0.0)
    except Exception:
        return 0.0


def _ff(v) -> str:
    try:
        return f"{float(v):.2f}"
    except Exception:
        return str(v or "?")


if __name__ == "__main__":
    main()
