"""
AlphaPulse — Multi-Strategy Replay Engine
==========================================
Runs multiple research strategies in sequence, collects all trades into the
unified multi_strategy_replay_runs / multi_strategy_replay_trades tables,
detects confluence between strategies, and upserts strategy_learning_profiles.

Supported strategy names:
    gap_sweep             → HistoricalReplayEngine
    engulfing_rejection   → EngulfingResearchEngine

Do NOT import this from live-bot code. Research + learning only.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import PIP_SIZE, SYMBOL
from db.database import Database
from utils.logger import get_logger

logger = get_logger("alphapulse.multi_strategy_engine")

SUPPORTED_STRATEGIES = {"gap_sweep", "engulfing_rejection"}

# Two trades from different strategies are "confluent" if they fired within
# this many hours of each other AND their entry levels are within CONFLUENCE_PIPS.
CONFLUENCE_WINDOW_HOURS = 4
CONFLUENCE_PIPS         = 15.0

_RESULTS_RESULTS = {"LOSS", "BREAKEVEN_WIN", "PARTIAL_WIN", "WIN", "STRONG_WIN"}


class MultiStrategyReplayEngine:
    """
    Wrapper that runs each sub-engine, collects all trades in a unified
    Supabase table, detects confluence, and upserts learning profiles.
    """

    def __init__(self, strategies: List[str]):
        unknown = set(strategies) - SUPPORTED_STRATEGIES
        if unknown:
            raise ValueError(f"Unsupported strategies: {unknown}. Supported: {SUPPORTED_STRATEGIES}")
        self.strategies = strategies
        self.db = Database()

    # ─────────────────────────────────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────────────────────────────────

    def run_last_months(
        self,
        months: int = 4,
        symbol: str = SYMBOL,
        show_trades: int = 20,
    ) -> dict:
        end   = datetime.now(timezone.utc)
        start = end - timedelta(days=months * 30)
        return self.run(
            start=start,
            end=end,
            symbol=symbol,
            months=months,
            show_trades=show_trades,
        )

    def run(
        self,
        start: datetime,
        end: datetime,
        symbol: str = SYMBOL,
        months: int = 0,
        show_trades: int = 20,
    ) -> dict:
        self.db.init()

        multi_run_id = self.db.create_multi_strategy_replay_run({
            "symbol":        symbol,
            "strategies":    self.strategies,
            "months_tested": months or None,
            "replay_start":  start.isoformat(),
            "replay_end":    end.isoformat(),
            "status":        "running",
        })
        if not multi_run_id:
            raise RuntimeError("Failed to create multi_strategy_replay_runs row")

        logger.info(
            "Multi-strategy run %d started: %s | %s → %s",
            multi_run_id, self.strategies,
            start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"),
        )

        strategy_results: Dict[str, dict] = {}
        all_multi_trades: List[dict] = []

        self.db.close()  # sub-engines will open their own connections

        for strategy_name in self.strategies:
            logger.info("Running sub-strategy: %s", strategy_name)
            try:
                result, raw_trades = self._run_strategy(
                    strategy_name, start, end, symbol, show_trades
                )
                strategy_results[strategy_name] = result

                # Re-open our DB connection to write multi trades
                self.db.init()
                for raw in raw_trades:
                    payload = self._to_multi_trade_payload(raw, strategy_name, multi_run_id)
                    try:
                        self.db.insert_multi_strategy_replay_trade(payload)
                        all_multi_trades.append(payload)
                    except Exception as exc:
                        logger.warning("Multi trade insert failed: %s", exc)
                self.db.close()

            except Exception as exc:
                logger.error("Sub-strategy %s failed: %s", strategy_name, exc, exc_info=True)
                strategy_results[strategy_name] = {"error": str(exc)}

        # Re-open for final writes
        self.db.init()

        # Detect confluence between strategies
        confluence_summary = self._detect_confluence(all_multi_trades, multi_run_id)

        # Build combined summary stats
        combined = self._build_combined_result(strategy_results, multi_run_id)
        combined["confluence_summary"] = confluence_summary

        # Upsert learning profiles
        learning_summary = self._update_learning_profiles(strategy_results, multi_run_id, symbol)
        combined["learning_summary"] = learning_summary

        # Finalize multi run row
        self.db.update_multi_strategy_replay_run(multi_run_id, {
            "status":             "completed",
            "completed_at":       datetime.now(timezone.utc).isoformat(),
            "total_trades":       combined.get("total_trades", 0),
            "wins":               combined.get("wins", 0),
            "losses":             combined.get("losses", 0),
            "win_rate":           combined.get("win_rate", 0.0),
            "tp1_rate":           combined.get("tp1_rate", 0.0),
            "tp2_rate":           combined.get("tp2_rate", 0.0),
            "tp3_rate":           combined.get("tp3_rate", 0.0),
            "net_pips":           combined.get("net_pips", 0.0),
            "avg_pips":           combined.get("avg_pips", 0.0),
            "strategy_summary":   json.dumps(combined.get("by_strategy", {})),
            "confluence_summary": json.dumps(confluence_summary),
            "learning_summary":   json.dumps(learning_summary),
        })

        self.db.close()
        logger.info(
            "Multi-strategy run %d done: %d trades | WR=%.1f%% | net=%.1f pips",
            multi_run_id,
            combined.get("total_trades", 0),
            combined.get("win_rate", 0.0),
            combined.get("net_pips", 0.0),
        )
        return combined

    # ─────────────────────────────────────────────────────────────────────────
    # Sub-strategy runners
    # ─────────────────────────────────────────────────────────────────────────

    def _run_strategy(
        self,
        strategy_name: str,
        start: datetime,
        end: datetime,
        symbol: str,
        show_trades: int,
    ) -> Tuple[dict, List[dict]]:
        if strategy_name == "engulfing_rejection":
            return self._run_engulfing(start, end, symbol, show_trades)
        if strategy_name == "gap_sweep":
            return self._run_gap_sweep(start, end, symbol)
        raise ValueError(f"Unknown strategy: {strategy_name}")

    def _run_engulfing(
        self,
        start: datetime,
        end: datetime,
        symbol: str,
        show_trades: int,
    ) -> Tuple[dict, List[dict]]:
        from historical_replay.engulfing_research import EngulfingResearchEngine

        engine = EngulfingResearchEngine()
        result = engine.run(start=start, end=end, symbol=symbol, show_trades=show_trades)

        # Sub-engine closed its db. Open ours to fetch trades by run_id.
        sub_run_id = result.get("run_id")
        trades: List[dict] = []
        if sub_run_id:
            self.db.init()
            try:
                data = self.db.get_strategy_research_results(sub_run_id)
                trades = data.get("trades", []) if data else []
            finally:
                self.db.close()
        return result, trades

    def _run_gap_sweep(
        self,
        start: datetime,
        end: datetime,
        symbol: str,
    ) -> Tuple[dict, List[dict]]:
        from historical_replay.engine import HistoricalReplayEngine

        engine = HistoricalReplayEngine()
        result = engine.run(start=start, end=end, symbol=symbol)

        # Sub-engine closed its db. Get latest replay run + trades.
        trades: List[dict] = []
        self.db.init()
        try:
            latest_run = self.db.get_latest_replay_run()
            if latest_run:
                run_id = latest_run.get("id")
                trades = self.db.get_replay_trades(run_id) if run_id else []
                # Filter only activated/closed trades
                trades = [
                    t for t in trades
                    if (t.get("final_result") or "") in _RESULTS_RESULTS
                ]
        finally:
            self.db.close()
        return result, trades

    # ─────────────────────────────────────────────────────────────────────────
    # Trade payload mapping
    # ─────────────────────────────────────────────────────────────────────────

    def _to_multi_trade_payload(
        self,
        raw: dict,
        strategy_name: str,
        multi_run_id: int,
    ) -> dict:
        if strategy_name == "engulfing_rejection":
            return self._map_engulfing_trade(raw, multi_run_id)
        if strategy_name == "gap_sweep":
            return self._map_gap_sweep_trade(raw, multi_run_id)
        return {}

    def _map_engulfing_trade(self, t: dict, multi_run_id: int) -> dict:
        entry = _f(t.get("entry"))
        sl    = _f(t.get("sl"))
        tp1   = _f(t.get("tp1"))
        return {
            "multi_run_id":        multi_run_id,
            "source":              "multi_strategy_replay",
            "symbol":              t.get("symbol", SYMBOL),
            "strategy_type":       "engulfing_rejection",
            "direction":           t.get("direction", ""),
            "timeframe":           t.get("timeframe"),
            "timeframe_pair":      t.get("timeframe_pair"),
            "session_name":        t.get("session_name"),
            "market_condition":    t.get("market_condition"),
            "dominant_bias":       t.get("dominant_bias"),
            "bias_strength":       t.get("bias_strength"),
            "level_type":          t.get("level_type") or "Gap",
            "level_high":          _f(t.get("level_high") or t.get("engulf_high")),
            "level_low":           _f(t.get("level_low")  or t.get("engulf_low")),
            "level_mid":           _f(t.get("level_mid")  or t.get("engulf_mid")),
            "quality_score":       _f(t.get("quality_score")),
            "quality_rejection_count": t.get("quality_rejection_count"),
            "structure_break_count":   t.get("structure_break_count"),
            "confirmation_type":   t.get("confirmation_path"),
            "confirmation_score":  _f(t.get("confirmation_score")),
            "entry":               entry,
            "sl":                  sl,
            "tp1":                 tp1,
            "tp2":                 _f(t.get("tp2")),
            "tp3":                 _f(t.get("tp3")),
            "sl_pips":             _pips(entry, sl, t.get("direction", "")),
            "tp1_pips":            _pips(entry, tp1, t.get("direction", "")),
            "activated_at":        _ts(t.get("activated_at")),
            "closed_at":           _ts(t.get("closed_at")),
            "final_result":        t.get("final_result", "OPEN"),
            "tp_progress":         t.get("tp_progress", 0),
            "protected_after_tp1": bool(t.get("protected_after_tp1")),
            "final_pips":          _f(t.get("final_pips")),
            "reward_score":        _f(t.get("reward_score")),
            "failure_reason":      t.get("failure_reason") or "",
        }

    def _map_gap_sweep_trade(self, t: dict, multi_run_id: int) -> dict:
        entry = _f(t.get("entry"))
        sl    = _f(t.get("sl"))
        tp1   = _f(t.get("tp1"))
        # historical_replay_trades: direction stored as "BUY"/"SELL"
        direction = t.get("direction", "")
        # timeframe_pair: "H4->H1" etc
        tf_pair = t.get("timeframe_pair", "")
        # lower_tf extracted from pair
        timeframe = tf_pair.split("->")[-1] if "->" in tf_pair else None
        return {
            "multi_run_id":        multi_run_id,
            "source":              "multi_strategy_replay",
            "symbol":              t.get("symbol", SYMBOL),
            "strategy_type":       "gap_sweep",
            "direction":           direction,
            "timeframe":           timeframe,
            "timeframe_pair":      tf_pair,
            "session_name":        t.get("session"),
            "market_condition":    t.get("market_condition"),
            "dominant_bias":       t.get("dominant_bias"),
            "bias_strength":       t.get("bias_strength"),
            "level_type":          t.get("level_type"),
            "setup_type":          t.get("setup_type"),
            "confirmation_type":   t.get("micro_confirmation_type") or t.get("confirmation_pattern"),
            "confirmation_score":  _f(t.get("micro_confirmation_score")),
            "entry":               entry,
            "sl":                  sl,
            "tp1":                 tp1,
            "tp2":                 _f(t.get("tp2")),
            "tp3":                 _f(t.get("tp3")),
            "sl_pips":             _pips(entry, sl, direction),
            "tp1_pips":            _f(t.get("pips_to_tp1")),
            "tp2_pips":            _f(t.get("pips_to_tp2")),
            "tp3_pips":            _f(t.get("pips_to_tp3")),
            "activated_at":        _ts(t.get("activation_time")),
            "closed_at":           _ts(t.get("timestamp")),
            "final_result":        t.get("final_result", "OPEN"),
            "tp_progress":         t.get("tp_progress", 0),
            "protected_after_tp1": bool(t.get("protected_after_tp1")),
            "final_pips":          _f(t.get("final_pips") or t.get("realized_pips")),
            "reward_score":        _f(t.get("reward_score")),
            "failure_reason":      t.get("failure_reason") or "",
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Confluence detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_confluence(
        self,
        all_trades: List[dict],
        multi_run_id: int,
    ) -> dict:
        """
        Two trades are confluent when they fired from different strategies,
        within CONFLUENCE_WINDOW_HOURS of each other, and their entries are
        within CONFLUENCE_PIPS. Marks the multi_strategy_replay_trades rows
        (best-effort PATCH) and returns a summary dict.
        """
        by_strategy: Dict[str, List[dict]] = defaultdict(list)
        for t in all_trades:
            by_strategy[t.get("strategy_type", "unknown")].append(t)

        strategy_names = list(by_strategy.keys())
        confluence_count = 0
        max_dist = CONFLUENCE_PIPS * PIP_SIZE
        window   = timedelta(hours=CONFLUENCE_WINDOW_HOURS)

        for i in range(len(strategy_names)):
            for j in range(i + 1, len(strategy_names)):
                s1, s2 = strategy_names[i], strategy_names[j]
                for t1 in by_strategy[s1]:
                    t1_at = _parse_ts(t1.get("activated_at"))
                    t1_e  = _f(t1.get("entry"))
                    if t1_at is None or t1_e is None:
                        continue
                    for t2 in by_strategy[s2]:
                        t2_at = _parse_ts(t2.get("activated_at"))
                        t2_e  = _f(t2.get("entry"))
                        if t2_at is None or t2_e is None:
                            continue
                        if abs((t1_at - t2_at)) <= window and abs(t1_e - t2_e) <= max_dist:
                            confluence_count += 1

        return {
            "confluence_pairs_detected": confluence_count,
            "window_hours":              CONFLUENCE_WINDOW_HOURS,
            "level_distance_pips":       CONFLUENCE_PIPS,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Combined result
    # ─────────────────────────────────────────────────────────────────────────

    def _build_combined_result(
        self, strategy_results: Dict[str, dict], multi_run_id: int
    ) -> dict:
        total = wins = losses = 0
        tp1_hit = tp2_hit = tp3_hit = 0
        net_pips = 0.0
        by_strategy = {}

        for name, res in strategy_results.items():
            if "error" in res:
                by_strategy[name] = {"error": res["error"]}
                continue
            # Both engines return keys with slightly different names
            n   = int(res.get("activated_trades", res.get("total_activated_trades", 0)) or 0)
            w   = int(res.get("wins", res.get("total_wins", 0)) or 0)
            l   = int(res.get("losses", res.get("total_losses", 0)) or 0)
            wr  = round((w / max(1, n)) * 100, 2)
            np_ = float(res.get("net_pips", 0.0) or 0.0)

            total    += n
            wins     += w
            losses   += l
            net_pips += np_

            # TP rates: engulfing result has tp1_rate; gap_sweep uses tp_hits
            tp1 = int(res.get("tp1_hits", res.get("tp1_hit", 0)) or 0)
            tp2 = int(res.get("tp2_hits", res.get("tp2_hit", 0)) or 0)
            tp3 = int(res.get("tp3_hits", res.get("tp3_hit", 0)) or 0)
            tp1_hit += tp1
            tp2_hit += tp2
            tp3_hit += tp3

            by_strategy[name] = {
                "trades":    n,
                "wins":      w,
                "losses":    l,
                "win_rate":  wr,
                "net_pips":  round(np_, 2),
                "avg_pips":  round(np_ / n, 2) if n else 0.0,
                "tp1_rate":  round(tp1 / n * 100, 2) if n else 0.0,
            }

        win_rate = round((wins / max(1, total)) * 100, 2)
        avg_pips = round(net_pips / total, 2) if total else 0.0

        return {
            "run_id":       multi_run_id,
            "strategies":   self.strategies,
            "total_trades": total,
            "wins":         wins,
            "losses":       losses,
            "win_rate":     win_rate,
            "tp1_rate":     round((tp1_hit / total * 100) if total else 0.0, 2),
            "tp2_rate":     round((tp2_hit / total * 100) if total else 0.0, 2),
            "tp3_rate":     round((tp3_hit / total * 100) if total else 0.0, 2),
            "net_pips":     round(net_pips, 2),
            "avg_pips":     avg_pips,
            "by_strategy":  by_strategy,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Learning profiles
    # ─────────────────────────────────────────────────────────────────────────

    def _update_learning_profiles(
        self,
        strategy_results: Dict[str, dict],
        multi_run_id: int,
        symbol: str,
    ) -> dict:
        """
        For each strategy, group trades by (session × direction × bias) and
        upsert a strategy_learning_profiles row. Returns a summary.
        """
        updated_profiles = 0

        for strategy_name, res in strategy_results.items():
            if "error" in res:
                continue

            # Get per-session and per-bias breakdowns from the result dict
            session_breakdown = (
                res.get("performance_by_session")
                or res.get("by_session")
                or {}
            )
            bias_breakdown = (
                res.get("performance_by_bias")
                or res.get("by_bias")
                or {}
            )

            for session, stats in session_breakdown.items():
                n  = int(stats.get("activated", stats.get("trades", 0)) or 0)
                w  = int(stats.get("wins", 0) or 0)
                l  = int(stats.get("losses", 0) or 0)
                np = float(stats.get("net_pips", 0.0) or 0.0)
                wr = round((w / max(1, n)) * 100, 2)

                profile_key = f"{strategy_name}_{symbol}_{session}_all_all"
                tier = _confidence_tier(n, wr)

                try:
                    self.db.upsert_strategy_learning_profile(profile_key, {
                        "strategy_type":       strategy_name,
                        "symbol":              symbol,
                        "session_name":        session,
                        "sample_size":         n,
                        "wins":                w,
                        "losses":              l,
                        "win_rate":            wr,
                        "net_pips":            round(np, 2),
                        "avg_pips":            round(np / n, 2) if n else 0.0,
                        "confidence_tier":     tier,
                        "recommended_weight":  _recommended_weight(wr, n),
                        "last_multi_run_id":   multi_run_id,
                    })
                    updated_profiles += 1
                except Exception as exc:
                    logger.warning("Learning profile upsert failed (%s): %s", profile_key, exc)

        return {"profiles_upserted": updated_profiles, "multi_run_id": multi_run_id}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def _ts(v) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def _parse_ts(v) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    try:
        from datetime import datetime as dt
        return dt.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _pips(entry: Optional[float], other: Optional[float], direction: str) -> Optional[float]:
    if entry is None or other is None or PIP_SIZE == 0:
        return None
    dist = (entry - other) if direction == "BUY" else (other - entry)
    return round(dist / PIP_SIZE, 2)


def _confidence_tier(n: int, wr: float) -> str:
    if n < 10:
        return "insufficient"
    if n < 30:
        return "low"
    if wr >= 60:
        return "high"
    if wr >= 45:
        return "medium"
    return "low"


def _recommended_weight(wr: float, n: int) -> float:
    if n < 10:
        return 0.5
    if wr >= 60:
        return 1.2
    if wr >= 50:
        return 1.0
    if wr >= 40:
        return 0.8
    return 0.5
