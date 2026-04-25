"""
AlphaPulse — Break + Retest Evaluation Report Builder
======================================================
Reads results stored by BreakRetestResearchEngine from strategy_research_*
tables and formats a human-readable performance report.

Usage (via evaluate_strategy.py):
    python -m historical_replay.evaluate_strategy --strategy standard_break_retest --show-trades 20
    python -m historical_replay.evaluate_strategy --strategy failed_engulf_break_retest --show-trades 20
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Dict, Iterable, List


def build_report(run: Dict, stats_rows: List[Dict], trades: List[Dict], *, show_trades: int = 20) -> str:
    activated = [
        t for t in trades
        if (t.get("final_result") or "") in {"LOSS", "BREAKEVEN_WIN", "PARTIAL_WIN", "WIN", "STRONG_WIN"}
    ]
    wins   = sum(1 for t in activated if t.get("final_result") != "LOSS")
    losses = sum(1 for t in activated if t.get("final_result") == "LOSS")
    closed = max(1, wins + losses)

    summary  = _summary_payload(stats_rows)
    funnel   = _funnel_summary(run, stats_rows, summary)
    rejects  = _reject_summary(run, stats_rows, summary)
    net_pips = sum(_to_float(t.get("final_pips")) for t in activated)

    strategy_type = run.get("strategy_type") or run.get("strategy_group") or "break_retest"

    lines = [
        "AlphaPulse Break + Retest Research Report",
        "=" * 44,
        f"Run ID:   {run.get('id')}",
        f"Strategy: {strategy_type}",
        f"Symbol:   {run.get('symbol')} | Status: {run.get('status')}",
        f"Period:   {run.get('replay_start')} → {run.get('replay_end')}",
        "",
        "─" * 44,
        "SUMMARY",
        "─" * 44,
        f"  Activated trades : {len(activated)}",
        f"  Wins / Losses    : {wins} / {losses}  ({(wins / closed) * 100:.1f}% WR)",
        f"  Net pips         : {net_pips:.1f}",
        f"  Avg pips / trade : {(net_pips / len(activated)):.1f}" if activated else "  Avg pips / trade : 0.0",
        f"  TP1 rate         : {summary.get('tp1_rate', 0.0):.1f}%",
        f"  TP2 rate         : {summary.get('tp2_rate', 0.0):.1f}%",
        f"  TP3 rate         : {summary.get('tp3_rate', 0.0):.1f}%",
        f"  {'WARNING: sample size too small for decision' if len(activated) < 20 else 'Sample size acceptable for preliminary review'}",
        "",
        "─" * 44,
        "BREAK + RETEST FUNNEL",
        "─" * 44,
        f"  Raw levels detected     : {funnel.get('raw_levels_detected', 0)}",
        f"  Break candidates        : {funnel.get('break_candidates', 0)}",
        f"  Valid breaks            : {funnel.get('valid_breaks', 0)}",
        f"  Fake breaks rejected    : {funnel.get('fake_breaks_rejected', 0)}",
        f"  Retest candidates       : {funnel.get('retest_candidates', 0)}",
        f"  Valid retests           : {funnel.get('valid_retests', 0)}",
        f"  Confirmation passed     : {funnel.get('confirmation_passed', 0)}",
        f"  Activated trades        : {funnel.get('activated_trades', 0)}",
        f"  Expired candidates      : {funnel.get('expired_candidates', 0)}",
        f"  Failed-engulf candidates: {funnel.get('failed_engulf_candidates', 0)}",
        "",
        "─" * 44,
        "REJECT REASONS",
        "─" * 44,
        _format_counts(Counter({k: int(v) for k, v in rejects.items() if int(v or 0) > 0})),
        "",
        "─" * 44,
        "BREAKDOWNS",
        "─" * 44,
    ]

    lines.extend([
        _format_breakdown("By Timeframe",          _group(activated, "timeframe")),
        "",
        _format_breakdown("By Session",             _group(activated, "session_name")),
        "",
        _format_breakdown("By Direction",           _group(activated, "direction")),
        "",
        _format_breakdown("By Dominant Bias",       _group(activated, "dominant_bias")),
        "",
        _format_breakdown("By Bias Strength",       _group(activated, "bias_strength")),
        "",
        _format_breakdown("By Bias Alignment",      _group_bias_alignment(activated)),
        "",
        _format_breakdown("By Confirmation Type",   _group(activated, "retest_confirmation_type")),
        "",
        _format_breakdown("By Source Level Type",   _group(activated, "source_level_type")),
        "",
        "─" * 44,
        "OUTCOME MIX",
        "─" * 44,
        _format_counts(Counter((t.get("final_result") or "OPEN") for t in activated)),
    ])

    if activated and show_trades:
        lines.extend(["", "─" * 44, f"SAMPLE TRADES (first {min(show_trades, len(activated))})", "─" * 44])
        for t in activated[:show_trades]:
            lines.append(
                "  {direction} {tf} bias={bias}/{strength} session={session} "
                "break_level={break_level} break_dist={break_dist}p "
                "conf={conf_type}({conf_score}) "
                "entry={entry} result={result} pips={pips}".format(
                    direction=t.get("direction", "?"),
                    tf=t.get("timeframe", "?"),
                    bias=t.get("dominant_bias", "?"),
                    strength=t.get("bias_strength", "?"),
                    session=t.get("session_name", "?"),
                    break_level=t.get("break_level", "?"),
                    break_dist=_fmt_float(t.get("break_distance_pips")),
                    conf_type=t.get("retest_confirmation_type") or t.get("confirmation_path") or "?",
                    conf_score=_fmt_float(t.get("retest_confirmation_score") or t.get("confirmation_score")),
                    entry=_fmt_float(t.get("entry")),
                    result=t.get("final_result", "?"),
                    pips=_fmt_float(t.get("final_pips")),
                )
            )

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Stats extraction helpers
# ─────────────────────────────────────────────────────────────────────────────

def _summary_payload(stats_rows: List[Dict]) -> Dict:
    for row in stats_rows:
        if row.get("stats_key") == "summary":
            p = row.get("payload")
            if isinstance(p, dict):
                return p
    return {}


def _stats_payload(stats_rows: List[Dict], stats_key: str) -> Dict:
    for row in stats_rows:
        if row.get("stats_key") == stats_key:
            p = row.get("payload")
            if isinstance(p, dict):
                return p
    return {}


def _funnel_summary(run: Dict, stats_rows: List[Dict], summary: Dict) -> Dict:
    return (
        run.get("funnel_summary")
        or summary.get("funnel_summary")
        or _stats_payload(stats_rows, "funnel_summary")
        or {}
    )


def _reject_summary(run: Dict, stats_rows: List[Dict], summary: Dict) -> Dict:
    return (
        run.get("reject_summary")
        or summary.get("reject_summary")
        or _stats_payload(stats_rows, "reject_summary")
        or {}
    )


# ─────────────────────────────────────────────────────────────────────────────
# Grouping helpers
# ─────────────────────────────────────────────────────────────────────────────

def _group(trades: Iterable[Dict], key: str) -> Dict[str, Dict]:
    grouped: Dict[str, Dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
    for t in trades:
        name = str(t.get(key) or "unknown")
        bucket = grouped[name]
        bucket["trades"] += 1
        bucket["wins"]   += 1 if t.get("final_result") != "LOSS" else 0
        bucket["losses"] += 1 if t.get("final_result") == "LOSS" else 0
        bucket["net_pips"] += _to_float(t.get("final_pips"))
    for bucket in grouped.values():
        n = bucket["trades"]
        bucket["win_rate"] = round((bucket["wins"] / n) * 100, 1) if n else 0.0
        bucket["net_pips"] = round(bucket["net_pips"], 2)
        bucket["avg_pips"] = round(bucket["net_pips"] / n, 2) if n else 0.0
    return dict(grouped)


def _group_bias_alignment(trades: Iterable[Dict]) -> Dict[str, Dict]:
    grouped: Dict[str, Dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "net_pips": 0.0})
    for t in trades:
        aligned = (
            (t.get("direction") == "BUY"  and t.get("dominant_bias") == "bullish")
            or (t.get("direction") == "SELL" and t.get("dominant_bias") == "bearish")
        )
        name = "aligned" if aligned else "counter_bias"
        bucket = grouped[name]
        bucket["trades"] += 1
        bucket["wins"]   += 1 if t.get("final_result") != "LOSS" else 0
        bucket["losses"] += 1 if t.get("final_result") == "LOSS" else 0
        bucket["net_pips"] += _to_float(t.get("final_pips"))
    for bucket in grouped.values():
        n = bucket["trades"]
        bucket["win_rate"] = round((bucket["wins"] / n) * 100, 1) if n else 0.0
        bucket["net_pips"] = round(bucket["net_pips"], 2)
        bucket["avg_pips"] = round(bucket["net_pips"] / n, 2) if n else 0.0
    return dict(grouped)


# ─────────────────────────────────────────────────────────────────────────────
# Formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _format_breakdown(title: str, data: Dict[str, Dict]) -> str:
    if not data:
        return f"{title}: no data"
    lines = [f"{title}:"]
    for name, item in sorted(data.items(), key=lambda p: p[1].get("trades", 0), reverse=True):
        lines.append(
            f"  {name:20s} trades={item['trades']:4d}  wins={item['wins']:4d}  "
            f"losses={item['losses']:4d}  WR={item['win_rate']:5.1f}%  "
            f"net={item['net_pips']:8.1f}p  avg={item.get('avg_pips', 0.0):6.1f}p"
        )
    return "\n".join(lines)


def _format_counts(counts: Counter) -> str:
    return (
        "\n".join(f"  {name}: {count}" for name, count in counts.most_common())
        if counts else "  no data"
    )


def _to_float(value) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _fmt_float(value) -> str:
    try:
        return f"{float(value):.2f}"
    except Exception:
        return str(value or "?")
