import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query

import api.state as state

router = APIRouter()
logger = logging.getLogger("alphapulse.api.analytics")


WIN_RESULTS = {"WIN", "STRONG_WIN", "BREAKEVEN_WIN", "PARTIAL_WIN"}
LOSS_RESULTS = {"LOSS", "STOP_LOSS_HIT"}


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value) if value is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _result_label(row: dict) -> str:
    result = str(row.get("final_result") or row.get("result") or "").upper()
    status = str(row.get("status") or "").upper()
    if result in {"WIN", "STRONG_WIN", "TP3_HIT"} or status in {"TP3_HIT", "COMPLETED"}:
        return "win"
    if result in {"BREAKEVEN", "BREAKEVEN_WIN"} or row.get("breakeven_exit"):
        return "breakeven"
    if result in LOSS_RESULTS or status == "STOP_LOSS_HIT":
        return "loss"
    if result == "PARTIAL_WIN" or status == "TP2_HIT":
        return "partial"
    return "open"


def _period_label(ts: str, mode: str = "week") -> str:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return "Unknown"
    return f"W{dt.isocalendar().week}" if mode == "week" else dt.strftime("%b %Y")


def _empty() -> dict:
    return {
        "db_ready": False,
        "sources": [],
        "metrics": {
            "total_trades": 0,
            "win_rate": 0.0,
            "tp1_hit_rate": 0.0,
            "net_pips": 0.0,
            "avg_pips_per_trade": 0.0,
        },
        "charts": {
            "cumulative_pips": [],
            "session_performance": [],
            "micro_confirmation_performance": [],
            "win_loss_distribution": [],
            "performance_by_bias": [],
            "performance_by_period": [],
        },
        "breakdowns": {
            "session": [],
            "setup_type": [],
            "micro_confirmation": [],
            "bias_gate": [],
            "outcome_mix": [],
        },
    }


def _normalize_session(row: dict) -> str:
    candidates = [
        ("session_name", row.get("session_name")),
        ("session", row.get("session")),
        ("market_session", row.get("market_session")),
    ]
    hb = state.read_heartbeat() or {}
    candidates.append(("heartbeat.market_session", hb.get("market_session") or hb.get("session_name") or hb.get("current_session")))
    for source, value in candidates:
        if not value:
            continue
        normalized = str(value).strip().lower().replace(" ", "_")
        mapping = {
            "london": "london",
            "new_york": "new_york",
            "new york": "new_york",
            "asia": "asia",
            "overlap": "overlap",
            "off_session": "off_session",
            "quiet_session": "quiet_session",
            "off-session": "off_session",
        }
        if normalized in mapping:
            logger.info("ANALYTICS SESSION NORMALIZED: source=%s value=%s", source, mapping[normalized])
            return mapping[normalized]
    row_time = str(row.get("timestamp") or row.get("closed_at") or row.get("created_at") or "")
    try:
        dt = datetime.fromisoformat(row_time.replace("Z", "+00:00"))
        hour = dt.hour
        if 0 <= hour < 7:
            return "asia"
        if 7 <= hour < 13:
            return "london"
        if 13 <= hour < 17:
            return "overlap"
        if 17 <= hour < 22:
            return "new_york"
    except Exception:
        pass
    return "unknown"


def _normalize_micro(row: dict) -> str:
    candidates = [
        ("micro_confirmation_type", row.get("micro_confirmation_type")),
        ("confirmation_type", row.get("confirmation_type")),
        ("confirmation_pattern", row.get("confirmation_pattern")),
        ("setup_confirmation", row.get("setup_confirmation")),
        ("manual_confirmation_type", row.get("manual_confirmation_type")),
    ]
    for source, value in candidates:
        if not value:
            continue
        text = str(value).strip().lower().replace(" ", "_")
        if "liquidity_sweep_reclaim" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=liquidity_sweep_reclaim", source)
            return "liquidity_sweep_reclaim"
        if "engulf" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=engulfing_rejection", source)
            return "engulfing_rejection"
        if "wick_rejection" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=wick_rejection", source)
            return "wick_rejection"
        if "close_rejection" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=close_rejection", source)
            return "close_rejection"
        if "momentum_rejection" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=momentum_rejection", source)
            return "momentum_rejection"
        if "micro_sweep" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=micro_sweep", source)
            return "micro_sweep"
        if "combined" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=combined", source)
            return "combined"
        if "manual" in text:
            logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=manual_setup", source)
            return "manual_setup"
        logger.info("ANALYTICS MICRO NORMALIZED: source=%s value=%s", source, text)
        return text
    return "unknown"


def _normalize_source(row: dict) -> str:
    source = str(row.get("source") or "").lower()
    if source == "live_bot":
        return "live_bot"
    if source == "manual":
        return "manual_setups"
    if source == "strategy_research":
        return "research"
    if source == "historical_replay":
        return "replay"
    return "legacy_mixed_data"


def _normalize_strategy_type(row: dict) -> str:
    candidates = [
        row.get("strategy_type"),
        row.get("source_strategy_type"),
        row.get("setup_type"),
        row.get("level_type"),
    ]
    for value in candidates:
        if not value:
            continue
        normalized = str(value).strip().lower().replace(" ", "_")
        if normalized in {"gap", "gap_sweep", "gap_liquidity_sweep_reclaim", "gap_reclaim"}:
            return "gap_liquidity_sweep_reclaim"
        if "engulf" in normalized:
            return "engulfing_rejection"
        if "manual" in normalized:
            return "manual_setup"
        return normalized
    source = _normalize_source(row)
    if source == "replay":
        return "gap_liquidity_sweep_reclaim"
    if source == "manual_setups":
        return "manual_setup"
    return "unknown"


@router.get("/analytics")
def get_analytics(
    session: str = Query("all"),
    confirmation_type: str = Query("all"),
    symbol: str = Query("all"),
    source: str = Query("all"),
):
    if not state.db_ready:
        return _empty()

    try:
        replay_run = state.db.get_latest_replay_run() if hasattr(state.db, "get_latest_replay_run") else None
        rows = []
        if replay_run:
            rows = state.db.get_replay_trades(replay_run["id"], limit=10000)
        live_rows = state.db.get_all_closed_trades()
        combined_rows = []
        seen = set()
        for row in rows + live_rows:
            key = (
                row.get("id"),
                row.get("trade_uuid") or row.get("uuid"),
                row.get("source"),
                row.get("closed_at") or row.get("timestamp") or row.get("created_at"),
            )
            if key in seen:
                continue
            seen.add(key)
            combined_rows.append(row)
        rows = combined_rows

        filtered = []
        for row in rows:
            row["strategy_type"] = row.get("strategy_type") or _normalize_strategy_type(row)
            if not row.get("strategy_type") or row.get("strategy_type") == "unknown":
                continue
            outcome = _result_label(row)
            if outcome == "open":
                continue
            if row.get("final_pips") is None and row.get("realized_pips") is None:
                continue
            row_symbol = row.get("pair") or row.get("symbol") or "XAUUSD"
            row_session = _normalize_session(row)
            row_micro = _normalize_micro(row)
            row_source = _normalize_source(row)
            if symbol != "all" and row_symbol != symbol:
                continue
            if session != "all" and row_session != session:
                continue
            if confirmation_type != "all" and row_micro != confirmation_type:
                continue
            if source != "all" and row_source != source:
                continue
            row["_normalized_outcome"] = outcome
            row["_normalized_session"] = row_session
            row["_normalized_micro"] = row_micro
            row["_normalized_source"] = row_source
            filtered.append(row)

        total = len(filtered)
        wins = 0
        tp1_hits = 0
        total_pips = 0.0
        cumulative = []
        running_pips = 0.0

        session_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "tp1": 0, "net_pips": 0.0})
        setup_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pips": 0.0})
        micro_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "tp1": 0, "net_pips": 0.0})
        bias_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pips": 0.0})
        outcome_stats: dict[str, int] = defaultdict(int)
        period_stats: dict[str, dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pips": 0.0})

        for row in filtered:
            outcome = row.get("_normalized_outcome") or _result_label(row)
            row_pips = _safe_float(row.get("final_pips"), _safe_float(row.get("realized_pips")))
            row_session = row.get("_normalized_session") or _normalize_session(row)
            row_setup = row.get("setup_type") or row.get("level_type") or "Gap"
            row_micro = row.get("_normalized_micro") or _normalize_micro(row)
            row_bias = row.get("bias_gate_result") or row.get("h4_bias") or row.get("bias") or "unknown"
            row_time = str(row.get("timestamp") or row.get("closed_at") or row.get("created_at") or "")

            is_win = outcome in {"win", "partial"}
            if is_win:
                wins += 1
            if (row.get("tp_progress_reached") or 0) >= 1:
                tp1_hits += 1
            total_pips += row_pips
            running_pips += row_pips
            cumulative.append({"label": row_time[:10] if row_time else "Unknown", "pips": round(running_pips, 1)})

            session_stats[row_session]["trades"] += 1
            session_stats[row_session]["wins"] += 1 if is_win else 0
            session_stats[row_session]["tp1"] += 1 if (row.get("tp_progress_reached") or 0) >= 1 else 0
            session_stats[row_session]["net_pips"] += row_pips

            setup_stats[row_setup]["trades"] += 1
            setup_stats[row_setup]["wins"] += 1 if is_win else 0
            setup_stats[row_setup]["net_pips"] += row_pips

            micro_stats[row_micro]["trades"] += 1
            micro_stats[row_micro]["wins"] += 1 if is_win else 0
            micro_stats[row_micro]["tp1"] += 1 if (row.get("tp_progress_reached") or 0) >= 1 else 0
            micro_stats[row_micro]["net_pips"] += row_pips

            bias_stats[row_bias]["trades"] += 1
            bias_stats[row_bias]["wins"] += 1 if is_win else 0
            bias_stats[row_bias]["net_pips"] += row_pips

            outcome_stats[outcome] += 1

            period = _period_label(row_time, "week")
            period_stats[period]["trades"] += 1
            period_stats[period]["wins"] += 1 if is_win else 0
            period_stats[period]["net_pips"] += row_pips

        def _rate(w: int, t: int) -> float:
            return round((w / t) * 100, 1) if t else 0.0

        metrics = {
            "total_trades": total,
            "win_rate": _rate(wins, total),
            "tp1_hit_rate": _rate(tp1_hits, total),
            "net_pips": round(total_pips, 1),
            "avg_pips_per_trade": round(total_pips / total, 1) if total else 0.0,
        }

        # Build sources list from what actually contributed trades
        source_counts: dict = {}
        for row in filtered:
            s = row.get("_normalized_source", "legacy_mixed_data")
            source_counts[s] = source_counts.get(s, 0) + 1
        _source_meta = {
            "live_bot":        {"label": "Live Bot",              "tone": "buy"},
            "replay":          {"label": "Latest Clean Replay",   "tone": "gold"},
            "manual_setups":   {"label": "Manual Setups",         "tone": "purple"},
            "research":        {"label": "Research Replay",        "tone": "outline"},
            "legacy_mixed_data": {"label": "Legacy Mixed Data",   "tone": "warn"},
        }
        active_sources = [
            {"key": k, "label": _source_meta[k]["label"], "tone": _source_meta[k]["tone"]}
            for k in ("replay", "live_bot", "manual_setups", "research", "legacy_mixed_data")
            if source_counts.get(k, 0) > 0
        ]
        if not active_sources and total == 0:
            active_sources = [{"key": "replay", "label": "Latest Clean Replay", "tone": "gold"}]

        return {
            "db_ready": True,
            "sources": active_sources,
            "metrics": metrics,
            "charts": {
                "cumulative_pips": cumulative,
                "session_performance": [
                    {
                        "name": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                        "tp1_rate": _rate(value["tp1"], value["trades"]),
                    }
                    for key, value in session_stats.items()
                ],
                "micro_confirmation_performance": [
                    {
                        "name": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in micro_stats.items()
                ],
                "win_loss_distribution": [
                    {"name": "Wins", "value": outcome_stats.get("win", 0), "color": "#10B981"},
                    {"name": "Breakeven", "value": outcome_stats.get("breakeven", 0), "color": "#D4AF37"},
                    {"name": "Losses", "value": outcome_stats.get("loss", 0), "color": "#EF4444"},
                ],
                "performance_by_bias": [
                    {
                        "name": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in bias_stats.items()
                ],
                "performance_by_period": [
                    {
                        "label": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in sorted(period_stats.items())
                ],
            },
            "breakdowns": {
                "session": [
                    {
                        "session": key,
                        "trades": value["trades"],
                        "wins": value["wins"],
                        "tp1": value["tp1"],
                        "net_pips": round(value["net_pips"], 1),
                        "avg_pips": round(value["net_pips"] / value["trades"], 1) if value["trades"] else 0.0,
                    }
                    for key, value in session_stats.items()
                ],
                "setup_type": [
                    {
                        "setup_type": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in setup_stats.items()
                ],
                "micro_confirmation": [
                    {
                        "micro": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "tp1_rate": _rate(value["tp1"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in micro_stats.items()
                ],
                "bias_gate": [
                    {
                        "bias_gate": key,
                        "trades": value["trades"],
                        "win_rate": _rate(value["wins"], value["trades"]),
                        "net_pips": round(value["net_pips"], 1),
                    }
                    for key, value in bias_stats.items()
                ],
                "outcome_mix": [
                    {"outcome": "Full Win", "trades": outcome_stats.get("win", 0), "color": "text-buy"},
                    {"outcome": "Partial Win", "trades": outcome_stats.get("partial", 0), "color": "text-gold-400"},
                    {"outcome": "Breakeven", "trades": outcome_stats.get("breakeven", 0), "color": "text-muted-foreground"},
                    {"outcome": "Loss", "trades": outcome_stats.get("loss", 0), "color": "text-sell"},
                ],
            },
        }
    except Exception as exc:
        return {**_empty(), "error": str(exc)}
