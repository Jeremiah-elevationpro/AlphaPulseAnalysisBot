from __future__ import annotations

import argparse
import os
from collections import Counter, defaultdict
from typing import Dict, Iterable, List

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from db.database import Database
from historical_replay.break_retest_research import STRATEGY_STANDARD, STRATEGY_FAILED_ENGULF

_BREAK_RETEST_STRATEGIES = {STRATEGY_STANDARD, STRATEGY_FAILED_ENGULF}


def main():
    parser = argparse.ArgumentParser(description="Evaluate stored strategy research replay results.")
    parser.add_argument("--strategy", type=str, default="engulfing_rejection")
    parser.add_argument("--run-id", type=int, default=0)
    parser.add_argument("--show-trades", type=int, default=20)
    args = parser.parse_args()

    db = Database()
    try:
        db.init()
        result = db.get_strategy_research_results(args.run_id) if args.run_id else db.get_latest_strategy_research_results(args.strategy)
        if not result:
            raise SystemExit("No strategy research runs found in Supabase.")
        if args.strategy in _BREAK_RETEST_STRATEGIES:
            from historical_replay.evaluate_break_retest import build_report as br_build_report
            print(br_build_report(result["run"], result["stats"], result["trades"], show_trades=args.show_trades))
        else:
            print(build_report(result["run"], result["stats"], result["trades"], show_trades=args.show_trades))
    finally:
        db.close()


def build_report(run: Dict, stats_rows: List[Dict], trades: List[Dict], *, show_trades: int = 20) -> str:
    activated = [t for t in trades if (t.get("final_result") or "") in {"LOSS", "BREAKEVEN_WIN", "WIN", "STRONG_WIN"}]
    wins = sum(1 for t in activated if t.get("final_result") != "LOSS")
    losses = sum(1 for t in activated if t.get("final_result") == "LOSS")
    closed = max(1, wins + losses)
    failed_count = sum(1 for t in trades if t.get("final_result") == "potential_failed_engulf_break_retest")
    summary_payload = _summary_payload(stats_rows)
    funnel_summary = _funnel_summary(run, stats_rows, summary_payload)
    reject_summary = _reject_summary(run, stats_rows, summary_payload)
    shortlisted = int(summary_payload.get("shortlisted_candidates", funnel_summary.get("shortlisted_candidates", 0)) or 0)
    confirmation_warning = bool(summary_payload.get("confirmation_score_warning"))
    lines = [
        "AlphaPulse Strategy Research Report",
        "=" * 40,
        f"Run ID: {run.get('id')} | Strategy: {run.get('strategy_group') or run.get('strategy_type')}",
        f"Symbol: {run.get('symbol')} | Status: {run.get('status')}",
        f"Period: {run.get('replay_start')} -> {run.get('replay_end')}",
        "",
        "Summary",
        f"- Activated trades: {len(activated)}",
        f"- Wins/Losses: {wins}/{losses} | Win rate: {(wins / closed) * 100:.1f}%",
        f"- Failed engulf break-retest candidates: {failed_count}",
        f"- Net pips: {sum(_to_float(t.get('final_pips')) for t in activated):.1f}",
        f"- {'WARNING: sample size too small for decision' if len(activated) < 20 else 'Sample size acceptable for preliminary review'}",
    ]
    if len(activated) > shortlisted and shortlisted > 0:
        lines.append("- WARNING: activation bypassed shortlist")
    if shortlisted == 0 and len(activated) > 0:
        lines.append("- WARNING: shortlist counter disconnected")
    if len(activated) > 0 and int(funnel_summary.get("activated_trades", 0) or 0) == 0:
        lines.append("- WARNING: funnel data missing for this run")
    if confirmation_warning:
        lines.append("- WARNING: confirmation score not predictive yet")
    lines.extend([
        "",
        "Filters",
        f"- Rejected due to mixed/neutral bias: {summary_payload.get('rejected_due_to_weak_bias', 0)}",
        f"- Rejected due to low quality rejections: {summary_payload.get('rejected_due_to_low_quality_rejections', 0)}",
        f"- Rejected due to low quality score: {summary_payload.get('rejected_due_to_low_quality_score', 0)}",
        f"- M15 disabled count: {funnel_summary.get('m15_disabled_count', 0)}",
        f"- Weak bias rejected count: {funnel_summary.get('weak_bias_rejected_count', 0)}",
        f"- Counter-bias rejected count: {funnel_summary.get('counter_bias_rejected_count', 0)}",
        f"- Shortlisted candidates: {shortlisted}",
        "",
        "Engulfing Funnel",
        f"- Raw engulf candles: {funnel_summary.get('raw_engulf_candles_detected', 0)}",
        f"- Zones created: {funnel_summary.get('engulf_zones_created', 0)}",
        f"- Quality rejection passed: {funnel_summary.get('quality_rejection_passed', 0)}",
        f"- Bias passed: {funnel_summary.get('bias_passed', 0)}",
        f"- Quality score passed: {funnel_summary.get('quality_score_passed', 0)}",
        f"- Shortlisted: {funnel_summary.get('shortlisted_candidates', 0)}",
        f"- Revisited: {funnel_summary.get('zone_revisited', 0)}",
        f"- Rejection confirmed: {funnel_summary.get('rejection_confirmation_passed', 0)}",
        f"- Activated: {funnel_summary.get('activated_trades', 0)}",
        f"- Failed break-retest: {funnel_summary.get('failed_engulf_break_retest_candidates', 0)}",
        f"- Expired: {funnel_summary.get('expired_candidates', 0)}",
        f"- Confirmation wick passed: {funnel_summary.get('confirmation_wick_passed', 0)}",
        f"- Confirmation close passed: {funnel_summary.get('confirmation_close_passed', 0)}",
        f"- Confirmation sweep passed: {funnel_summary.get('confirmation_sweep_passed', 0)}",
        f"- Confirmation momentum passed: {funnel_summary.get('confirmation_momentum_passed', 0)}",
        f"- Confirmation hard invalidated: {funnel_summary.get('confirmation_hard_invalidated', 0)}",
        f"- Confirmation score failed: {funnel_summary.get('confirmation_score_failed', 0)}",
        "",
        "Reject Reasons",
        _format_counts(Counter({k: int(v) for k, v in reject_summary.items() if int(v or 0) > 0})),
        "",
        _format_breakdown("Confirmation Path", _group(activated, "confirmation_path")),
        f"Average confirmation score winners: {summary_payload.get('avg_confirmation_score_winners', _average_confirmation_score(activated, winner=True)):.2f}",
        f"Average confirmation score losses: {summary_payload.get('avg_confirmation_score_losses', _average_confirmation_score(activated, winner=False)):.2f}",
        "",
        _format_breakdown("Engulf Direction", _group(activated, "direction")),
        _format_breakdown("Dominant Bias", _group(activated, "dominant_bias")),
        _format_breakdown("Bias Strength", _group(activated, "bias_strength")),
        _format_breakdown("Bias Alignment", _group_bias_alignment(activated)),
        _format_breakdown("Timeframe", _group(activated, "timeframe")),
        _format_breakdown("Session", _group(activated, "session_name")),
        _format_breakdown("Structure Break Count", _group(activated, "structure_break_count")),
        _format_breakdown("Quality Rejection Bucket", _group_quality_rejection_buckets(activated)),
        "",
        "Outcome Mix",
        _format_counts(Counter((t.get("final_result") or "OPEN") for t in activated)),
    ])
    if activated and show_trades:
        lines.extend(["", f"Sample Trades ({min(show_trades, len(activated))})"])
        for trade in activated[:show_trades]:
            lines.append(
                "- {direction} {timeframe} bias={dominant_bias}/{bias_strength} session={session_name} "
                "q={quality_score} qr={quality_rejection_count} sb={structure_break_count} cp={confirmation_path} cs={confirmation_score} "
                "entry={entry} result={final_result} pips={final_pips}".format(
                    direction=trade.get("direction"),
                    timeframe=trade.get("timeframe"),
                    dominant_bias=trade.get("dominant_bias"),
                    bias_strength=trade.get("bias_strength"),
                    session_name=trade.get("session_name"),
                    quality_score=trade.get("quality_score"),
                    quality_rejection_count=trade.get("quality_rejection_count"),
                    structure_break_count=trade.get("structure_break_count"),
                    confirmation_path=trade.get("confirmation_path"),
                    confirmation_score=trade.get("confirmation_score"),
                    entry=trade.get("entry"),
                    final_result=trade.get("final_result"),
                    final_pips=trade.get("final_pips"),
                )
            )
    return "\n".join(lines)


def _summary_payload(stats_rows: List[Dict]) -> Dict:
    for row in stats_rows:
        if row.get("stats_key") == "summary":
            payload = row.get("payload")
            if isinstance(payload, dict):
                return payload
    return {}


def _stats_payload(stats_rows: List[Dict], stats_key: str) -> Dict:
    for row in stats_rows:
        if row.get("stats_key") == stats_key:
            payload = row.get("payload")
            if isinstance(payload, dict):
                return payload
            stats_value = row.get("stats_value")
            if isinstance(stats_value, dict):
                return stats_value
    return {}


def _funnel_summary(run: Dict, stats_rows: List[Dict], summary_payload: Dict) -> Dict:
    return (
        run.get("funnel_summary")
        or summary_payload.get("funnel_summary")
        or _stats_payload(stats_rows, "funnel_summary")
        or (summary_payload.get("stats_value") if isinstance(summary_payload.get("stats_value"), dict) else {})
        or {}
    )


def _reject_summary(run: Dict, stats_rows: List[Dict], summary_payload: Dict) -> Dict:
    return (
        run.get("reject_summary")
        or summary_payload.get("reject_summary")
        or _stats_payload(stats_rows, "reject_summary")
        or (summary_payload.get("stats_value") if isinstance(summary_payload.get("stats_value"), dict) else {})
        or {}
    )


def _group(trades: Iterable[Dict], key: str) -> Dict[str, Dict]:
    grouped = defaultdict(lambda: {"activated": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
    for trade in trades:
        name = str(trade.get(key) or "unknown")
        bucket = grouped[name]
        bucket["activated"] += 1
        bucket["wins"] += 1 if trade.get("final_result") != "LOSS" else 0
        bucket["losses"] += 1 if trade.get("final_result") == "LOSS" else 0
        bucket["net_pips"] += _to_float(trade.get("final_pips"))
    for bucket in grouped.values():
        bucket["win_rate"] = round((bucket["wins"] / bucket["activated"]) * 100, 1) if bucket["activated"] else 0.0
        bucket["net_pips"] = round(bucket["net_pips"], 2)
        bucket["avg_pips"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
    return dict(grouped)


def _group_quality_rejection_buckets(trades: Iterable[Dict]) -> Dict[str, Dict]:
    grouped = defaultdict(lambda: {"activated": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
    for trade in trades:
        count = int(trade.get("quality_rejection_count") or 0)
        if count <= 4:
            name = "3-4"
        elif count <= 7:
            name = "5-7"
        elif count <= 12:
            name = "8-12"
        else:
            name = "13+"
        bucket = grouped[name]
        bucket["activated"] += 1
        bucket["wins"] += 1 if trade.get("final_result") != "LOSS" else 0
        bucket["losses"] += 1 if trade.get("final_result") == "LOSS" else 0
        bucket["net_pips"] += _to_float(trade.get("final_pips"))
    for bucket in grouped.values():
        bucket["win_rate"] = round((bucket["wins"] / bucket["activated"]) * 100, 1) if bucket["activated"] else 0.0
        bucket["net_pips"] = round(bucket["net_pips"], 2)
        bucket["avg_pips"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
    return dict(grouped)


def _group_bias_alignment(trades: Iterable[Dict]) -> Dict[str, Dict]:
    grouped = defaultdict(lambda: {"activated": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
    for trade in trades:
        aligned = (
            (trade.get("direction") == "BUY" and trade.get("dominant_bias") == "bullish")
            or (trade.get("direction") == "SELL" and trade.get("dominant_bias") == "bearish")
        )
        name = "aligned" if aligned else "counter_bias"
        bucket = grouped[name]
        bucket["activated"] += 1
        bucket["wins"] += 1 if trade.get("final_result") != "LOSS" else 0
        bucket["losses"] += 1 if trade.get("final_result") == "LOSS" else 0
        bucket["net_pips"] += _to_float(trade.get("final_pips"))
    for bucket in grouped.values():
        bucket["win_rate"] = round((bucket["wins"] / bucket["activated"]) * 100, 1) if bucket["activated"] else 0.0
        bucket["net_pips"] = round(bucket["net_pips"], 2)
        bucket["avg_pips"] = round(bucket["net_pips"] / bucket["activated"], 2) if bucket["activated"] else 0.0
    return dict(grouped)


def _format_breakdown(title: str, data: Dict[str, Dict]) -> str:
    if not data:
        return f"{title}: no data"
    lines = [f"{title}:"]
    for name, item in sorted(data.items(), key=lambda pair: pair[1].get("activated", 0), reverse=True):
        lines.append(
            f"- {name}: activated={item['activated']} wins={item['wins']} losses={item['losses']} "
            f"win_rate={item['win_rate']:.1f}% net_pips={item['net_pips']:.1f} avg_pips={item.get('avg_pips', 0.0):.1f}"
        )
    return "\n".join(lines)


def _format_counts(counts: Counter) -> str:
    return "\n".join(f"- {name}: {count}" for name, count in counts.most_common()) if counts else "- no data"


def _to_float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _average_confirmation_score(trades: Iterable[Dict], *, winner: bool) -> float:
    selected = [
        _to_float(trade.get("confirmation_score"))
        for trade in trades
        if ((trade.get("final_result") != "LOSS") if winner else (trade.get("final_result") == "LOSS"))
    ]
    return round(sum(selected) / len(selected), 2) if selected else 0.0


if __name__ == "__main__":
    main()
