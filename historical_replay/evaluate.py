from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import ACTIVE_TIMEFRAME_PAIR_LABELS, DISABLED_TIMEFRAME_PAIRS, PIP_SIZE
from db.database import Database


def main():
    parser = argparse.ArgumentParser(description="Evaluate stored AlphaPulse historical replay results.")
    parser.add_argument("--run-id", type=int, default=0, help="Replay run id. Defaults to latest run.")
    parser.add_argument("--show-trades", type=int, default=10, help="Number of sample trades to print.")
    args = parser.parse_args()

    db = Database()
    try:
        db.init()
        run = db.get_replay_run(args.run_id) if args.run_id else db.get_latest_replay_run()
        if not run:
            raise SystemExit("No replay runs found in Supabase.")

        run_id = int(run["id"])
        stats = db.get_replay_stats(run_id) or {}
        trades = _active_trades_only(db.get_replay_trades(run_id))
        if DISABLED_TIMEFRAME_PAIRS:
            disabled = ", ".join(f"{high}->{low}" for high, low in DISABLED_TIMEFRAME_PAIRS)
            print(f"Replay evaluator excluding disabled timeframe pair(s): {disabled}")
        print(build_report(run, stats, trades, show_trades=args.show_trades))
    finally:
        db.close()


def build_report(run: Dict, stats: Dict, trades: List[Dict], *, show_trades: int = 10) -> str:
    active_only = _has_disabled_stats(stats)
    activated = len(trades) if active_only else int(stats.get("total_activated_trades") or run.get("total_activated_trades") or len(trades))
    wins = _count_wins(trades) if active_only else int(stats.get("total_wins") or run.get("total_wins") or _count_wins(trades))
    losses = _count_losses(trades) if active_only else int(stats.get("total_losses") or run.get("total_losses") or _count_losses(trades))
    closed = wins + losses
    win_rate = _rate(wins, closed)
    tp1_rate = _tp_rate(trades, 1) if active_only or stats.get("tp1_hit_rate") is None else stats.get("tp1_hit_rate")
    tp2_rate = _tp_rate(trades, 2) if active_only or stats.get("tp2_hit_rate") is None else stats.get("tp2_hit_rate")
    tp3_rate = _tp_rate(trades, 3) if active_only or stats.get("tp3_hit_rate") is None else stats.get("tp3_hit_rate")
    pip_summary = _pip_summary(trades)
    by_timeframe = _group(trades, "timeframe_pair")
    by_bias = _group(trades, "dominant_bias")
    by_session = _group(trades, "session")
    by_setup_type = _group(trades, "setup_type")
    by_micro = _group(trades, "micro_confirmation_type")
    by_level_type = _group(trades, "level_type")
    by_sweep = _group(trades, "h1_sweep_direction")
    by_pd = _group(trades, "pd_location")
    by_bias_gate = _group(trades, "bias_gate_result")

    lines = [
        "AlphaPulse Historical Replay Report",
        "=" * 40,
        f"Run ID: {run.get('id')} | Status: {run.get('status')}",
        f"Symbol: {run.get('symbol')} | Period: {run.get('replay_start')} -> {run.get('replay_end')}",
        "",
        "Funnel",
        f"- Watchlists: {run.get('total_watchlists', stats.get('total_watchlists', 0)) if not active_only else 'active-pair trades only'}",
        f"- Pending-order-ready: {run.get('total_pending_order_ready', stats.get('total_pending_order_ready', 0)) if not active_only else 'active-pair trades only'}",
        f"- Activated trades: {activated}",
        f"- Closed wins/losses: {wins}/{losses} | Win rate: {win_rate:.1f}%",
        "",
        "TP Hit Rates",
        f"- TP1: {_pct(tp1_rate)}",
        f"- TP2: {_pct(tp2_rate)}",
        f"- TP3: {_pct(tp3_rate)}",
        "",
        "Pip Performance",
        f"- Total pips gained: {pip_summary['total_pips_gained']:.1f}",
        f"- Total pips lost: {pip_summary['total_pips_lost']:.1f}",
        f"- Net pips: {pip_summary['net_pips']:.1f}",
        f"- Average pips/trade: {pip_summary['average_pips_per_trade']:.1f}",
        "",
        "Breakdowns",
        _format_breakdown("Timeframe Pair", by_timeframe),
        _format_breakdown("Bias", by_bias),
        _format_breakdown("Session", by_session),
        _format_breakdown("Setup Type", by_setup_type),
        _format_breakdown("Micro Confirmation", by_micro),
        _format_breakdown("Level Type (Gap/V/A)", by_level_type),
        _format_breakdown("H1 Sweep Direction", by_sweep),
        _format_breakdown("Premium/Discount", by_pd),
        _format_breakdown("Bias Gate", by_bias_gate),
        "",
        "Outcome Mix",
        _format_counts(Counter(t.get("final_result") or "OPEN" for t in trades)),
    ]

    if trades and show_trades:
        lines.extend(["", f"Sample Trades ({min(show_trades, len(trades))})"])
        for trade in trades[:show_trades]:
            lines.append(
                "- {timestamp} | {direction} {timeframe_pair} {setup_type} | "
                "micro={micro_confirmation_type}/{micro_layer_decision} sweep={h1_sweep_direction} pd={pd_location} bias_gate={bias_gate_result} | "
                "entry={entry} result={final_result} TP={tp_progress} pips={final_pips} reward={reward_score}".format(
                    timestamp=trade.get("timestamp"),
                    direction=trade.get("direction"),
                    timeframe_pair=trade.get("timeframe_pair"),
                    setup_type=trade.get("setup_type"),
                    micro_confirmation_type=trade.get("micro_confirmation_type") or "none",
                    micro_layer_decision=trade.get("micro_layer_decision") or "neutral",
                    h1_sweep_direction=trade.get("h1_sweep_direction") or "none",
                    pd_location=trade.get("pd_location") or "unknown",
                    bias_gate_result=trade.get("bias_gate_result") or "unknown",
                    entry=trade.get("entry"),
                    final_result=trade.get("final_result"),
                    tp_progress=trade.get("tp_progress"),
                    final_pips=_fmt_pips(_trade_final_pips(trade)),
                    reward_score=trade.get("reward_score"),
                )
            )

    return "\n".join(lines)


def _group(trades: Iterable[Dict], key: str) -> Dict:
    grouped = defaultdict(
        lambda: {
            "activated": 0,
            "wins": 0,
            "losses": 0,
            "tp1_hits": 0,
            "total_pips_gained": 0.0,
            "total_pips_lost": 0.0,
            "net_pips": 0.0,
        }
    )
    for trade in trades:
        name = trade.get(key) or "unknown"
        bucket = grouped[name]
        bucket["activated"] += 1
        bucket["wins"] += 1 if trade.get("final_result") in (
            "PARTIAL_WIN",
            "BREAKEVEN_WIN",
            "WIN",
            "STRONG_WIN",
        ) else 0
        bucket["losses"] += 1 if trade.get("final_result") == "LOSS" else 0
        bucket["tp1_hits"] += 1 if int(trade.get("tp_progress") or 0) >= 1 else 0
        final_pips = _trade_final_pips(trade)
        if final_pips >= 0:
            bucket["total_pips_gained"] += final_pips
        else:
            bucket["total_pips_lost"] += abs(final_pips)
        bucket["net_pips"] += final_pips
    for bucket in grouped.values():
        bucket["win_rate"] = round(bucket["wins"] / max(1, bucket["wins"] + bucket["losses"]), 3)
        bucket["tp1_hit_rate"] = round(bucket["tp1_hits"] / max(1, bucket["activated"]), 3)
        bucket["total_pips_gained"] = round(bucket["total_pips_gained"], 2)
        bucket["total_pips_lost"] = round(bucket["total_pips_lost"], 2)
        bucket["net_pips"] = round(bucket["net_pips"], 2)
        bucket["avg_pips_per_trade"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
    return dict(grouped)


def _format_breakdown(title: str, data: Dict) -> str:
    if not data:
        return f"{title}: no data"
    rows = [f"{title}:"]
    for name, item in sorted(data.items(), key=lambda pair: pair[1].get("activated", 0), reverse=True):
        rows.append(
            f"- {name}: activated={item.get('activated', 0)} "
            f"wins={item.get('wins', 0)} losses={item.get('losses', 0)} "
            f"win_rate={_pct(item.get('win_rate'))} tp1={_pct(item.get('tp1_hit_rate'))} "
            f"net_pips={_fmt_pips(item.get('net_pips'))} avg={_fmt_pips(item.get('avg_pips_per_trade'))}"
        )
    return "\n".join(rows)


def _format_counts(counts: Counter) -> str:
    if not counts:
        return "- no trades stored"
    return "\n".join(f"- {name}: {count}" for name, count in counts.most_common())


def _count_wins(trades: Iterable[Dict]) -> int:
    return sum(
        1 for trade in trades
        if trade.get("final_result") in (
            "PARTIAL_WIN",
            "BREAKEVEN_WIN",
            "WIN",
            "STRONG_WIN",
        )
    )


def _count_losses(trades: Iterable[Dict]) -> int:
    return sum(1 for trade in trades if trade.get("final_result") == "LOSS")


def _tp_rate(trades: List[Dict], target_number: int) -> float:
    if not trades:
        return 0.0
    return sum(1 for trade in trades if int(trade.get("tp_progress") or 0) >= target_number) / len(trades)


def _pip_summary(trades: Iterable[Dict]) -> Dict[str, float]:
    values = [_trade_final_pips(trade) for trade in trades]
    gained = sum(value for value in values if value >= 0)
    lost = sum(abs(value) for value in values if value < 0)
    net = gained - lost
    return {
        "total_pips_gained": round(gained, 2),
        "total_pips_lost": round(lost, 2),
        "net_pips": round(net, 2),
        "average_pips_per_trade": round(net / len(values), 2) if values else 0.0,
    }


def _trade_final_pips(trade: Dict) -> float:
    stored = _as_float(trade.get("final_pips"))
    if stored is not None:
        return round(stored, 2)

    progress = int(trade.get("tp_progress") or trade.get("tp_progress_reached") or 0)
    entry = _as_float(trade.get("entry"))
    if entry is None:
        return 0.0

    if progress > 0:
        tp = _as_float(trade.get(f"tp{min(progress, 5)}"))
        return round(_price_to_pips(abs(tp - entry)), 2) if tp is not None else 0.0

    if trade.get("final_result") == "LOSS":
        sl = _as_float(trade.get("sl"))
        return round(-_price_to_pips(abs(entry - sl)), 2) if sl is not None else 0.0

    return 0.0


def _price_to_pips(price_distance: float) -> float:
    pip_size = PIP_SIZE or 1.0
    return float(price_distance) / pip_size


def _as_float(value) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _active_trades_only(trades: Iterable[Dict]) -> List[Dict]:
    return [
        trade for trade in trades
        if _normalise_tf_pair(trade.get("timeframe_pair") or "") in ACTIVE_TIMEFRAME_PAIR_LABELS
    ]


def _has_disabled_stats(stats: Dict) -> bool:
    by_tf = stats.get("performance_by_timeframe_pair") or {}
    return any(_normalise_tf_pair(tf_pair) not in ACTIVE_TIMEFRAME_PAIR_LABELS for tf_pair in by_tf)


def _normalise_tf_pair(value: str) -> str:
    return str(value).replace("->", "-").replace(" ", "")


def _rate(numerator: int, denominator: int) -> float:
    return round((numerator / denominator) * 100, 1) if denominator else 0.0


def _fmt_pips(value) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def _pct(value) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


if __name__ == "__main__":
    main()
