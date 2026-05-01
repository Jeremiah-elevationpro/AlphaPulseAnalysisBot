from __future__ import annotations

import argparse
from collections import defaultdict
from typing import Any

from db.database import Database


def build_report(run: dict[str, Any], scenarios: list[dict[str, Any]], confirmations: list[dict[str, Any]], reviews: list[dict[str, Any]], show_trades: int = 20) -> dict[str, Any]:
    by_confirmation: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "net_pips": 0.0})
    by_session: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "net_pips": 0.0})
    by_scenario: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "net_pips": 0.0})
    by_confirmation_filtered: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "net_pips": 0.0})
    by_session_filtered: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "net_pips": 0.0})
    primary_secondary_filtered: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "wins": 0, "net_pips": 0.0})
    block_reasons: dict[str, int] = defaultdict(int)
    before_filters = len(confirmations)
    raw_entry_candidates = int((run.get("summary") or {}).get("raw_entry_candidates", 0) or run.get("raw_entry_candidates", 0) or before_filters)
    approved_entries = sum(1 for row in confirmations if str(row.get("decision") or "") == "send_entry_alert")
    blocked_entries = 0
    entries_per_day: dict[str, int] = defaultdict(int)
    confirmation_by_key = {
        str(row.get("confirmation_key") or ""): row
        for row in confirmations
        if str(row.get("confirmation_key") or "")
    }
    rank_buckets: dict[str, dict[str, Any]] = {
        "90+": {"count": 0, "wins": 0, "net_pips": 0.0},
        "80-89": {"count": 0, "wins": 0, "net_pips": 0.0},
        "70-79": {"count": 0, "wins": 0, "net_pips": 0.0},
        "below_70": {"count": 0, "wins": 0, "net_pips": 0.0},
    }
    candidate_scores: list[float] = []
    candidate_days: dict[str, int] = defaultdict(int)
    sl_distance_buckets: dict[str, dict[str, Any]] = {
        "0-15": {"count": 0, "wins": 0, "net_pips": 0.0},
        "15-25": {"count": 0, "wins": 0, "net_pips": 0.0},
        "25+": {"count": 0, "wins": 0, "net_pips": 0.0},
    }
    target_type_buckets: dict[str, float] = defaultdict(float)
    tp_source_counts: dict[str, int] = defaultdict(int)
    reaction_level_count = 0
    confirmation_risk_buckets: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "wins": 0, "net_pips": 0.0}))
    _WIN_RESULTS = {"WIN", "STRONG_WIN", "BREAKEVEN_WIN"}
    _ALL_OUTCOME_TYPES = [
        "STRONG_WIN", "WIN", "BREAKEVEN_WIN", "LOSS",
        "EXPIRED_BEFORE_TP", "EXPIRED_AFTER_TP1", "EXPIRED_INVALIDATED", "MISSED_MOVE",
        "NO_TRIGGER",
    ]
    outcome_mix: dict[str, int] = {k: 0 for k in _ALL_OUTCOME_TYPES}
    for review in reviews:
        result = str(review.get("result") or "")
        win_like = result in _WIN_RESULTS
        # Map legacy EXPIRED → EXPIRED_BEFORE_TP for backward compat
        if result == "EXPIRED":
            result = "EXPIRED_BEFORE_TP"
        if result in outcome_mix:
            outcome_mix[result] += 1
        pips = float(review.get("pips_result") or 0.0)
        for key, bucket in (
            (str(review.get("confirmation_type") or "unknown"), by_confirmation),
            (str(review.get("session_name") or "unknown"), by_session),
            (str(review.get("scenario_type") or "unknown"), by_scenario),
        ):
            bucket[key]["count"] += 1
            bucket[key]["wins"] += 1 if win_like else 0
            bucket[key]["net_pips"] += pips
        scenario_bucket = str(review.get("scenario_type") or "unknown")
        primary_secondary_filtered[scenario_bucket]["count"] += 1
        primary_secondary_filtered[scenario_bucket]["wins"] += 1 if win_like else 0
        primary_secondary_filtered[scenario_bucket]["net_pips"] += pips
        created_at = str(review.get("created_at") or "")
        date_key = created_at.split("T", 1)[0] if "T" in created_at else "unknown"
        entries_per_day[date_key] += 1
        risk_pips = float(review.get("risk_pips") or abs(float(review.get("entry") or 0.0) - float(review.get("sl") or 0.0)))
        if risk_pips < 15:
            risk_bucket_name = "0-15"
            sl_bucket = sl_distance_buckets["0-15"]
        elif risk_pips < 25:
            risk_bucket_name = "15-25"
            sl_bucket = sl_distance_buckets["15-25"]
        else:
            risk_bucket_name = "25+"
            sl_bucket = sl_distance_buckets["25+"]
        sl_bucket["count"] += 1
        sl_bucket["wins"] += 1 if win_like else 0
        sl_bucket["net_pips"] += pips
        if float(review.get("reaction_level") or 0.0):
            reaction_level_count += 1
        tp_source = str(review.get("tp_source") or "unknown")
        target_type_buckets[tp_source] += pips
        tp_source_counts[tp_source] += 1
        confirmation = confirmation_by_key.get(str(review.get("setup_id") or ""))
        if confirmation:
            confirmation_type = str(confirmation.get("confirmation_type") or "unknown")
            session_name = str(confirmation.get("session_name") or "unknown")
            by_confirmation_filtered[confirmation_type]["count"] += 1
            by_confirmation_filtered[confirmation_type]["wins"] += 1 if win_like else 0
            by_confirmation_filtered[confirmation_type]["net_pips"] += pips
            confirmation_risk_buckets[confirmation_type][risk_bucket_name]["count"] += 1
            confirmation_risk_buckets[confirmation_type][risk_bucket_name]["wins"] += 1 if win_like else 0
            confirmation_risk_buckets[confirmation_type][risk_bucket_name]["net_pips"] += pips
            by_session_filtered[session_name]["count"] += 1
            by_session_filtered[session_name]["wins"] += 1 if win_like else 0
            by_session_filtered[session_name]["net_pips"] += pips
            score = float(confirmation.get("candidate_rank_score") or review.get("candidate_rank_score") or 0.0)
            candidate_scores.append(score)
            if score >= 90.0:
                bucket = rank_buckets["90+"]
            elif score >= 80.0:
                bucket = rank_buckets["80-89"]
            elif score >= 70.0:
                bucket = rank_buckets["70-79"]
            else:
                bucket = rank_buckets["below_70"]
            bucket["count"] += 1
            bucket["wins"] += 1 if win_like else 0
            bucket["net_pips"] += pips
    for row in confirmations:
        decision = str(row.get("decision") or "")
        created_at = str(row.get("created_at") or "")
        date_key = created_at.split("T", 1)[0] if "T" in created_at else "unknown"
        candidate_days[date_key] += 1
        if decision != "send_entry_alert":
            blocked_entries += 1
            block_reasons[str(row.get("rejection_reason") or decision or "unknown")] += 1
    tp_rr_buckets = {
        "below_1.0R": {"count": 0, "tp1_hits": 0},
        "1.0-1.49R": {"count": 0, "tp1_hits": 0},
        "1.5R+": {"count": 0, "tp1_hits": 0},
    }
    for review in reviews:
        tp1_rr = float(review.get("tp1_rr") or 0.0)
        if tp1_rr < 1.0:
            bucket = tp_rr_buckets["below_1.0R"]
        elif tp1_rr < 1.5:
            bucket = tp_rr_buckets["1.0-1.49R"]
        else:
            bucket = tp_rr_buckets["1.5R+"]
        bucket["count"] += 1
        bucket["tp1_hits"] += 1 if review.get("tp1_hit") else 0
    win_rate_by_rank_bucket = {
        bucket_name: {
            **bucket,
            "win_rate": round((bucket["wins"] / max(bucket["count"], 1)) * 100.0, 2) if bucket["count"] else 0.0,
        }
        for bucket_name, bucket in rank_buckets.items()
    }
    tp1_hit_rate_by_rr_bucket = {
        name: {
            **bucket,
            "tp1_hit_rate": round((bucket["tp1_hits"] / max(bucket["count"], 1)) * 100.0, 2) if bucket["count"] else 0.0,
        }
        for name, bucket in tp_rr_buckets.items()
    }
    wide_sl_candidates = 0
    wide_sl_approved = 0
    very_wide_sl_candidates = 0
    very_wide_sl_approved = 0
    for row in confirmations:
        risk_pips = float(row.get("risk_pips") or abs(float(row.get("entry") or 0.0) - float(row.get("sl") or 0.0)))
        if risk_pips > 25:
            wide_sl_candidates += 1
            if str(row.get("decision") or "") == "send_entry_alert":
                wide_sl_approved += 1
        if risk_pips > 35:
            very_wide_sl_candidates += 1
            if str(row.get("decision") or "") == "send_entry_alert":
                very_wide_sl_approved += 1
    entries_activated = len(reviews)
    no_trigger_count = blocked_entries  # confirmations that were confirmed but blocked by quality gate
    expired_before_tp_count = outcome_mix.get("EXPIRED_BEFORE_TP", 0) + sum(
        1 for row in reviews if str(row.get("result") or "") == "EXPIRED"
    )
    expired_after_tp1_count = outcome_mix.get("EXPIRED_AFTER_TP1", 0)
    protected_after_tp1_count = sum(1 for row in reviews if row.get("protected_after_tp1"))

    return {
        "run": run,
        "total_scenarios_generated": len(scenarios),
        "total_confirmations": len(confirmations),
        "candidate_count": raw_entry_candidates,
        "before_filters": {
            "raw_confirmations": before_filters,
            "raw_entry_candidates": raw_entry_candidates,
        },
        "after_quality_gate": {
            "approved_entries": approved_entries,
            "blocked_entries": blocked_entries,
            "block_reasons": dict(block_reasons),
        },
        "total_reviews": len(reviews),
        "net_pips": round(sum(float(row.get("pips_result") or 0.0) for row in reviews), 2),
        "tp1_rate": round((sum(1 for row in reviews if row.get("tp1_hit")) / max(len(reviews), 1)) * 100.0, 2) if reviews else 0.0,
        "tp2_rate": round((sum(1 for row in reviews if row.get("tp2_hit")) / max(len(reviews), 1)) * 100.0, 2) if reviews else 0.0,
        "tp3_rate": round((sum(1 for row in reviews if row.get("tp3_hit")) / max(len(reviews), 1)) * 100.0, 2) if reviews else 0.0,
        "outcome_mix": outcome_mix,
        "activation_summary": {
            "entries_activated": entries_activated,
            "entries_expired_before_tp": expired_before_tp_count,
            "entries_expired_after_tp1": expired_after_tp1_count,
            "no_trigger_setups": no_trigger_count,
            "protected_after_tp1_count": protected_after_tp1_count,
        },
        "confirmation_performance": dict(by_confirmation),
        "confirmation_performance_after_v2_filters": dict(by_confirmation_filtered),
        "session_performance": dict(by_session),
        "session_performance_after_v2_filters": dict(by_session_filtered),
        "scenario_performance": dict(by_scenario),
        "primary_vs_secondary_after_v2_filters": dict(primary_secondary_filtered),
        "average_candidates_per_day": round(sum(candidate_days.values()) / max(len(candidate_days), 1), 2) if candidate_days else 0.0,
        "entries_per_day_average": round(sum(entries_per_day.values()) / max(len(entries_per_day), 1), 2) if entries_per_day else 0.0,
        "approved_entries_per_day": round(sum(entries_per_day.values()) / max(len(entries_per_day), 1), 2) if entries_per_day else 0.0,
        "average_candidate_rank_score": round(sum(candidate_scores) / max(len(candidate_scores), 1), 2) if candidate_scores else 0.0,
        "win_rate_by_rank_bucket": win_rate_by_rank_bucket,
        "tp_sl_intelligence_summary": {
            "avg_risk_pips": round(sum(float(row.get("risk_pips") or abs(float(row.get("entry") or 0.0) - float(row.get("sl") or 0.0))) for row in reviews) / max(len(reviews), 1), 2) if reviews else 0.0,
            "avg_tp1_reward": round(sum(float(row.get("tp1_reward_pips") or abs(float(row.get("tp1") or 0.0) - float(row.get("entry") or 0.0))) for row in reviews) / max(len(reviews), 1), 2) if reviews else 0.0,
            "avg_tp1_rr": round(sum(float(row.get("tp1_rr") or 0.0) for row in reviews) / max(len(reviews), 1), 2) if reviews else 0.0,
            "reaction_level_count": reaction_level_count,
            "tp1_from_source": dict(tp_source_counts),
            "micro_tp_blocked_count": int(block_reasons.get("micro_tp_blocked", 0)),
            "invalid_sl_count": int(block_reasons.get("invalid_sl_direction", 0)),
            "old_tp_sl_source_blocked_count": int(block_reasons.get("old_tp_sl_source_blocked", 0)),
            "setups_recalculated": int(block_reasons.get("recalculated_tp_sl", 0)),
            "setups_rejected_due_to_tp_sl": int(sum(count for reason, count in block_reasons.items() if reason in {"invalid_sl_direction", "old_tp_sl_source_blocked", "invalid_buy_ladder", "invalid_sell_ladder", "zero_risk"})),
            "tp1_hit_rate_by_rr_bucket": tp1_hit_rate_by_rr_bucket,
            "win_rate_by_sl_distance_bucket": {
                name: {
                    **bucket,
                    "win_rate": round((bucket["wins"] / max(bucket["count"], 1)) * 100.0, 2) if bucket["count"] else 0.0,
                }
                for name, bucket in sl_distance_buckets.items()
            },
            "net_pips_by_target_type": dict(target_type_buckets),
        },
        "risk_quality_summary": {
            "wide_sl_candidates": wide_sl_candidates,
            "wide_sl_approved": wide_sl_approved,
            "wide_sl_blocked": max(wide_sl_candidates - wide_sl_approved, 0),
            "very_wide_sl_candidates": very_wide_sl_candidates,
            "very_wide_sl_approved": very_wide_sl_approved,
            "very_wide_sl_blocked": max(very_wide_sl_candidates - very_wide_sl_approved, 0),
            "blocked_reasons": {
                "wide_sl_quality_block": int(block_reasons.get("wide_sl_quality_block", 0)),
                "very_wide_sl_quality_block": int(block_reasons.get("very_wide_sl_quality_block", 0)),
                "sweep_wide_sl_blocked": int(block_reasons.get("sweep_wide_sl_blocked", 0)),
                "failed_retest_wide_sl_blocked": int(block_reasons.get("failed_retest_wide_sl_blocked", 0)),
            },
            "performance_by_risk_bucket": {
                name: {
                    **bucket,
                    "win_rate": round((bucket["wins"] / max(bucket["count"], 1)) * 100.0, 2) if bucket["count"] else 0.0,
                }
                for name, bucket in sl_distance_buckets.items()
            },
            "performance_by_confirmation_and_risk_bucket": {
                confirmation_type: {
                    bucket_name: {
                        **bucket,
                        "win_rate": round((bucket["wins"] / max(bucket["count"], 1)) * 100.0, 2) if bucket["count"] else 0.0,
                    }
                    for bucket_name, bucket in buckets.items()
                }
                for confirmation_type, buckets in confirmation_risk_buckets.items()
            },
        },
        "show_trades": reviews[:show_trades],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Spencer analyst replay run.")
    parser.add_argument("--run-id", type=str, default="latest")
    parser.add_argument("--show-trades", type=int, default=20)
    args = parser.parse_args()

    db = Database()
    db.init()
    run = db.get_latest_analyst_replay_run() if args.run_id == "latest" else db.get_analyst_replay_run(int(args.run_id))
    if not run:
        print({"error": "analyst replay run not found"})
        return
    run_id = int(run.get("id"))
    scenarios = db.get_analyst_scenario_rows(run_id=run_id)
    confirmations = db.get_analyst_confirmation_rows(run_id=run_id)
    reviews = db.get_analyst_trade_reviews(run_id=run_id)
    print(build_report(run, scenarios, confirmations, reviews, show_trades=args.show_trades))


if __name__ == "__main__":
    main()
