"""
AlphaPulse — Multi-Strategy Replay Engine
==========================================
Runs multiple strategies in a shared candle-by-candle replay window so every
strategy is evaluated on the same market path without suppressing the others.

Supported strategy names:
    gap_sweep             → HistoricalReplayEngine gap path
    engulfing_rejection   → StrategyManager live-forward-test engulfing path

Do NOT import this from live-bot code. Research + learning only.
"""

from __future__ import annotations

import json
import os
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

from config.settings import PIP_SIZE, SYMBOL
from db.database import Database
from utils.logger import get_logger

logger = get_logger("alphapulse.multi_strategy_engine")

SUPPORTED_STRATEGIES = {"gap_sweep", "engulfing_rejection"}

CONFLUENCE_WINDOW_HOURS = 4
CONFLUENCE_PIPS         = 15.0

_CLOSED_RESULTS = {"LOSS", "BREAKEVEN_WIN", "PARTIAL_WIN", "WIN", "STRONG_WIN"}

_ERROR_LOG = Path("logs/multi_strategy_replay_error.log")


class MultiStrategyReplayEngine:
    """Shared replay wrapper for strict per-candle multi-strategy execution."""

    def __init__(self, strategies: List[str]):
        unknown = set(strategies) - SUPPORTED_STRATEGIES
        if unknown:
            raise ValueError(
                f"Unsupported strategies: {unknown}. Supported: {SUPPORTED_STRATEGIES}"
            )
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

        self.db.close()

        # ── Step 1: run shared replay ─────────────────────────────────────────
        try:
            result, raw_trades = self._run_shared_replay(start, end, symbol)
        except Exception as exc:
            tb = traceback.format_exc()
            _write_error_log(tb)
            logger.error("Shared replay failed: %s", exc, exc_info=True)
            self._mark_failed(multi_run_id, exc)
            print(
                f"\nMULTI STRATEGY REPLAY FAILED during replay: {exc}\n"
                f"See {_ERROR_LOG}"
            )
            raise

        # ── Step 2: save trades ───────────────────────────────────────────────
        self.db.init()
        all_multi_trades: List[dict] = []
        missing_pips_by_strategy: Dict[str, int] = {s: 0 for s in self.strategies}
        rows_inserted = 0

        for raw in raw_trades:
            payload = self._map_replay_trade(raw, multi_run_id)
            if not payload:
                continue
            strat = payload.get("strategy_type", "")
            fp    = payload.get("final_pips")
            if (fp is None or fp == 0.0) and (payload.get("final_result") or "") in _CLOSED_RESULTS:
                if strat in missing_pips_by_strategy:
                    missing_pips_by_strategy[strat] += 1
                    logger.warning(
                        "GAP MULTI REPLAY WARNING: missing final_pips for %s trade; "
                        "learning blocked for this trade",
                        strat,
                    )
            try:
                self.db.insert_multi_strategy_replay_trade(payload)
                all_multi_trades.append(payload)
                rows_inserted += 1
                logger.info(
                    "MULTI TRADE STORED: strategy=%s | result=%s | pips=%s",
                    strat,
                    payload.get("final_result", "?"),
                    f"{fp:.2f}" if fp is not None else "None",
                )
            except Exception as exc:
                logger.warning("Multi trade insert failed (strategy=%s result=%s): %s",
                               strat, payload.get("final_result", "?"), exc)

        # Aggregation mismatch guard — detect silent trade loss early
        _sb_balance = result.get("strategy_scan_balance", {})
        total_scan_closed = sum(
            int((v or {}).get("closed_trades", 0) or 0)
            for v in _sb_balance.values()
            if isinstance(v, dict)
        )
        if total_scan_closed > 0 and len(all_multi_trades) == 0:
            logger.error(
                "MULTI AGGREGATION ERROR: scan balance reports %d closed trades but "
                "normalized trade list is empty — check _run_shared_replay() return path "
                "and _map_replay_trade() field mapping",
                total_scan_closed,
            )

        # ── Step 3: detect confluence ─────────────────────────────────────────
        try:
            confluence_summary = self._detect_confluence(all_multi_trades, multi_run_id)
        except Exception as exc:
            logger.warning("Confluence detection failed (non-fatal): %s", exc)
            confluence_summary = {"confluence_pairs_detected": 0, "error": str(exc)}

        # ── Step 4: build combined stats ──────────────────────────────────────
        combined = self._build_combined_result(
            trades=all_multi_trades,
            multi_run_id=multi_run_id,
            replay_result=result,
            missing_pips_by_strategy=missing_pips_by_strategy,
        )
        combined["confluence_summary"] = confluence_summary

        # ── Step 5: upsert learning profiles (optional — never crashes run) ───
        try:
            learning_summary = self._update_learning_profiles(
                trades=all_multi_trades,
                multi_run_id=multi_run_id,
                symbol=symbol,
                replay_result=result,
                missing_pips_by_strategy=missing_pips_by_strategy,
            )
        except Exception as exc:
            logger.warning(
                "MULTI STRATEGY WARNING: learning profile update failed, "
                "replay results still saved: %s",
                exc,
            )
            learning_summary = {"profiles_upserted": 0, "error": str(exc)}
        combined["learning_summary"] = learning_summary

        # ── Step 6: finalize multi run row ────────────────────────────────────
        try:
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
                "strategy_summary":   safe_json(combined.get("by_strategy", {})),
                "confluence_summary": safe_json(confluence_summary),
                "learning_summary":   safe_json(learning_summary),
            })
        except Exception as exc:
            logger.warning("Run summary update failed, retrying minimal: %s", exc)
            try:
                self.db.update_multi_strategy_replay_run(multi_run_id, {
                    "status":        "completed",
                    "completed_at":  datetime.now(timezone.utc).isoformat(),
                    "total_trades":  combined.get("total_trades", 0),
                    "win_rate":      combined.get("win_rate", 0.0),
                    "net_pips":      combined.get("net_pips", 0.0),
                })
            except Exception as exc2:
                logger.warning("Minimal run update also failed: %s", exc2)

        self.db.close()

        logger.info(
            "Multi-strategy run %d done: %d trades | WR=%.1f%% | net=%.1f pips",
            multi_run_id,
            combined.get("total_trades", 0),
            combined.get("win_rate", 0.0),
            combined.get("net_pips", 0.0),
        )

        # ── Aggregation debug output ──────────────────────────────────────────
        sb = combined.get("strategy_scan_balance", {})
        print("\nMULTI AGGREGATION CHECK:")
        for name in self.strategies:
            s_bucket    = (sb.get(name) or {})
            scan_closed = int(s_bucket.get("closed_trades", 0) or 0)
            norm_closed = sum(1 for t in all_multi_trades if t.get("strategy_type") == name)
            print(f"  {name}: scan_closed={scan_closed} normalized_closed={norm_closed}")
            if scan_closed != norm_closed:
                print(
                    f"  !! WARNING: {name} scan_closed({scan_closed}) != "
                    f"normalized_closed({norm_closed})"
                )
        print(f"  all_closed_trades={len(all_multi_trades)}")
        print(f"  rows_inserted={rows_inserted}")

        return combined

    # ─────────────────────────────────────────────────────────────────────────
    # Shared replay runner
    # ─────────────────────────────────────────────────────────────────────────

    def _run_shared_replay(
        self,
        start: datetime,
        end: datetime,
        symbol: str,
    ) -> tuple[dict, List[dict]]:
        from historical_replay.engine import HistoricalReplayEngine
        from signals.signal_generator import SignalGenerator
        from strategies.strategy_manager import StrategyManager

        strategy_manager = StrategyManager(
            learning_engine=None,
            enabled_strategies=self.strategies,
            merge_confluence=False,
        )
        engine = HistoricalReplayEngine(
            strategy_manager=strategy_manager,
            signal_generator=SignalGenerator(learning_engine=None),
        )
        result = engine.run(start=start, end=end, symbol=symbol)

        # PRIMARY: use in-memory closed trade payloads that the engine embedded in result.
        # This avoids the Supabase round-trip that silently returns 0 rows when
        # historical_replay_trades has schema mismatches or when _store_replay_trade()
        # skipped a trade due to an optional-column insert error.
        in_memory_trades: Optional[List[dict]] = result.pop("_closed_replay_trades", None)
        if in_memory_trades is not None:
            logger.info(
                "MULTI STRATEGY: %d in-memory closed trades extracted from replay result "
                "(replay_run_id=%s) — skipping Supabase round-trip",
                len(in_memory_trades),
                result.get("replay_run_id"),
            )
            return result, in_memory_trades

        # FALLBACK: fetch from Supabase (used only when engine doesn't embed trades,
        # e.g. older engine versions or when _closed_replay_trades key is absent).
        logger.warning(
            "MULTI STRATEGY: _closed_replay_trades absent from replay result — "
            "falling back to Supabase fetch (may return 0 rows if schema has issues)"
        )
        trades: List[dict] = []
        self.db.init()
        try:
            run_id = result.get("replay_run_id")
            if run_id:
                trades = self.db.get_replay_trades(run_id)
                trades = [
                    t for t in trades
                    if (t.get("final_result") or "") in _CLOSED_RESULTS
                ]
                logger.info(
                    "MULTI STRATEGY: Supabase fallback fetched %d closed trades "
                    "for replay_run_id=%s",
                    len(trades), run_id,
                )
            else:
                logger.warning("replay_run_id not in result; Supabase trade fetch skipped")
        finally:
            self.db.close()
        return result, trades

    # ─────────────────────────────────────────────────────────────────────────
    # Trade payload mapping
    # ─────────────────────────────────────────────────────────────────────────

    def _map_replay_trade(self, t: dict, multi_run_id: int) -> dict:
        entry     = _f(t.get("entry"))
        sl        = _f(t.get("sl"))
        tp1       = _f(t.get("tp1"))
        direction = t.get("direction", "")
        tf_pair   = t.get("timeframe_pair", "")
        timeframe = (
            tf_pair.split("->")[-1] if "->" in tf_pair
            else tf_pair.split("-")[-1] if "-" in tf_pair
            else None
        )
        # TEXT[] must be a Python list, not a comma-joined string
        confluence_with: List[str] = _normalise_confluence(t.get("confluence_with"))

        return {
            "multi_run_id":            multi_run_id,
            "source":                  "multi_strategy_replay",
            "symbol":                  t.get("symbol", SYMBOL),
            "strategy_type":           t.get("strategy_type", "gap_sweep"),
            "direction":               direction,
            "timeframe":               timeframe,
            "timeframe_pair":          tf_pair,
            "session_name":            t.get("session_name") or t.get("session"),
            "market_condition":        t.get("market_condition"),
            "dominant_bias":           t.get("dominant_bias"),
            "bias_strength":           t.get("bias_strength"),
            "level_type":              t.get("level_type"),
            "setup_type":              t.get("setup_type"),
            "confirmation_type":       (
                t.get("confirmation_path")
                or t.get("micro_confirmation_type")
                or t.get("confirmation_pattern")
            ),
            "confirmation_score":      _f(
                t.get("confirmation_score") or t.get("micro_confirmation_score")
            ),
            "quality_score":           _f(t.get("quality_score")),
            "quality_rejection_count": t.get("quality_rejection_count"),
            "structure_break_count":   t.get("structure_break_count"),
            "entry":                   entry,
            "sl":                      sl,
            "tp1":                     tp1,
            "tp2":                     _f(t.get("tp2")),
            "tp3":                     _f(t.get("tp3")),
            "sl_pips":                 _pips(entry, sl, direction),
            "tp1_pips":                _f(t.get("pips_to_tp1")),
            "tp2_pips":                _f(t.get("pips_to_tp2")),
            "tp3_pips":                _f(t.get("pips_to_tp3")),
            "activated_at":            _ts(t.get("activation_time")),
            "closed_at":               _ts(t.get("closed_time") or t.get("timestamp")),
            "final_result":            t.get("final_result", "OPEN"),
            "tp_progress":             t.get("tp_progress", 0),
            "protected_after_tp1":     bool(t.get("protected_after_tp1")),
            "final_pips":              _f(t.get("final_pips") or t.get("realized_pips")),
            "reward_score":            _f(t.get("reward_score")),
            "failure_reason":          t.get("failure_reason") or "",
            # TEXT[] — must be a Python list for Supabase PostgREST
            "confluence":              bool(confluence_with),
            "confluence_strategy_types": confluence_with,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Confluence detection
    # ─────────────────────────────────────────────────────────────────────────

    def _detect_confluence(
        self,
        all_trades: List[dict],
        multi_run_id: int,
    ) -> dict:
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
                        if abs(t1_at - t2_at) <= window and abs(t1_e - t2_e) <= max_dist:
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
        self,
        *,
        trades: List[dict],
        multi_run_id: int,
        replay_result: dict,
        missing_pips_by_strategy: Dict[str, int],
    ) -> dict:
        total    = len(trades)
        wins     = sum(1 for t in trades if t.get("final_result") != "LOSS")
        losses   = sum(1 for t in trades if t.get("final_result") == "LOSS")
        tp1_hit  = sum(1 for t in trades if int(t.get("tp_progress") or 0) >= 1)
        tp2_hit  = sum(1 for t in trades if int(t.get("tp_progress") or 0) >= 2)
        tp3_hit  = sum(1 for t in trades if int(t.get("tp_progress") or 0) >= 3)
        net_pips = sum(_f(t.get("final_pips")) or 0.0 for t in trades)

        strategy_balance = replay_result.get("strategy_scan_balance", {})
        by_strategy: Dict[str, dict] = {}

        for name in self.strategies:
            grp  = [t for t in trades if t.get("strategy_type") == name]
            n    = len(grp)
            w    = sum(1 for t in grp if t.get("final_result") != "LOSS")
            l    = sum(1 for t in grp if t.get("final_result") == "LOSS")
            np_  = sum(_f(t.get("final_pips")) or 0.0 for t in grp)
            tp1  = sum(1 for t in grp if int(t.get("tp_progress") or 0) >= 1)
            mpips = missing_pips_by_strategy.get(name, 0)
            bucket = dict(strategy_balance.get(name, {}))
            sb_closed = int(bucket.get("closed_trades", 0) or 0)
            if sb_closed > 0 and n == 0:
                logger.warning(
                    "BY-STRATEGY WARNING: %s scan_balance shows %d closed trades "
                    "but 0 normalized trade records — trade mapping or extraction failed",
                    name, sb_closed,
                )
                bucket["_warning"] = "scan_balance_closed_but_no_normalized_trades"
            bucket.update({
                "trades":              n,
                "wins":                w,
                "losses":              l,
                "win_rate":            round((w / max(1, n)) * 100, 2),
                "net_pips":            round(np_, 2),
                "avg_pips":            round(np_ / n, 2) if n else 0.0,
                "tp1_rate":            round((tp1 / n) * 100, 2) if n else 0.0,
                "missing_pips_count":  mpips,
            })
            by_strategy[name] = bucket

        win_rate = round((wins / max(1, total)) * 100, 2)
        avg_pips = round(net_pips / total, 2) if total else 0.0

        return {
            "run_id":                multi_run_id,
            "strategies":            self.strategies,
            "total_trades":          total,
            "wins":                  wins,
            "losses":                losses,
            "win_rate":              win_rate,
            "tp1_rate":              round((tp1_hit / total * 100) if total else 0.0, 2),
            "tp2_rate":              round((tp2_hit / total * 100) if total else 0.0, 2),
            "tp3_rate":              round((tp3_hit / total * 100) if total else 0.0, 2),
            "net_pips":              round(net_pips, 2),
            "avg_pips":              avg_pips,
            "by_strategy":           by_strategy,
            "strategy_scan_balance": strategy_balance,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Learning profiles
    # ─────────────────────────────────────────────────────────────────────────

    def _update_learning_profiles(
        self,
        *,
        trades: List[dict],
        multi_run_id: int,
        symbol: str,
        replay_result: dict,
        missing_pips_by_strategy: Dict[str, int],
    ) -> dict:
        updated_profiles = 0
        skipped: List[str] = []
        strategy_balance = replay_result.get("strategy_scan_balance", {})

        for strategy_name in self.strategies:
            balance   = strategy_balance.get(strategy_name, {})
            scans_run = int(balance.get("scans_run", 0) or 0)
            closed    = int(balance.get("closed_trades", 0) or 0)
            mpips     = missing_pips_by_strategy.get(strategy_name, 0)

            strategy_trades = [t for t in trades if t.get("strategy_type") == strategy_name]
            n_trades = len(strategy_trades)

            if scans_run <= 0 and n_trades == 0:
                reason = "no_closed_trades"
                logger.info(
                    "LEARNING PROFILE SKIPPED: strategy=%s | reason=%s",
                    strategy_name, reason,
                )
                skipped.append(f"{strategy_name}:{reason}")
                continue

            if mpips > 0 and n_trades > 0 and mpips >= n_trades:
                reason = "all_trades_missing_final_pips"
                logger.info(
                    "LEARNING PROFILE SKIPPED: strategy=%s | reason=%s",
                    strategy_name, reason,
                )
                skipped.append(f"{strategy_name}:{reason}")
                continue

            session_breakdown = _group_for_learning(strategy_trades, "session_name")

            for session, stats in session_breakdown.items():
                n  = int(stats.get("trades", 0) or 0)
                w  = int(stats.get("wins", 0) or 0)
                l  = int(stats.get("losses", 0) or 0)
                np = float(stats.get("net_pips", 0.0) or 0.0)
                wr = round((w / max(1, n)) * 100, 2)

                profile_key = f"{strategy_name}_{symbol}_{session}_all_all"
                tier = _confidence_tier(n, wr)

                try:
                    self.db.upsert_strategy_learning_profile(profile_key, {
                        "strategy_type":      strategy_name,
                        "symbol":             symbol,
                        "session_name":       session,
                        "sample_size":        n,
                        "wins":               w,
                        "losses":             l,
                        "win_rate":           wr,
                        "net_pips":           round(np, 2),
                        "avg_pips":           round(np / n, 2) if n else 0.0,
                        "confidence_tier":    tier,
                        "recommended_weight": _recommended_weight(wr, n),
                        "last_multi_run_id":  multi_run_id,
                    })
                    updated_profiles += 1
                    logger.info(
                        "LEARNING PROFILE UPDATED: strategy=%s | session=%s | sample=%d | "
                        "WR=%.1f%% | net=%.1fp",
                        strategy_name, session, n, wr, round(np, 2),
                    )
                except Exception as exc:
                    logger.warning("Learning profile upsert failed (%s): %s", profile_key, exc)

        return {
            "profiles_upserted": updated_profiles,
            "skipped":           skipped,
            "multi_run_id":      multi_run_id,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _mark_failed(self, multi_run_id: int, exc: Exception) -> None:
        try:
            self.db.init()
            self.db.update_multi_strategy_replay_run(multi_run_id, {
                "status":        "failed",
                "error_message": str(exc)[:500],
                "completed_at":  datetime.now(timezone.utc).isoformat(),
            })
        except Exception as inner:
            logger.warning("Could not mark run %d as failed: %s", multi_run_id, inner)
        finally:
            try:
                self.db.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def safe_json(obj: Any) -> str:
    """Convert obj to a JSON string, coercing non-serializable types safely."""
    def _default(v):
        if isinstance(v, datetime):
            return v.isoformat()
        if hasattr(v, "item"):          # numpy scalar
            return v.item()
        if hasattr(v, "__float__"):     # Decimal etc.
            return float(v)
        return str(v)
    try:
        return json.dumps(obj, default=_default)
    except Exception:
        return "{}"


def _write_error_log(tb: str) -> None:
    try:
        _ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{'='*60}\n{datetime.now().isoformat()}\n{tb}\n")
    except Exception:
        pass


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
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
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


def _group_for_learning(trades: List[dict], key: str) -> Dict[str, Dict[str, float]]:
    grouped: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"trades": 0, "wins": 0, "losses": 0, "net_pips": 0.0}
    )
    for trade in trades:
        name = str(trade.get(key) or "unknown")
        bucket = grouped[name]
        bucket["trades"]   += 1
        bucket["wins"]     += 1 if trade.get("final_result") != "LOSS" else 0
        bucket["losses"]   += 1 if trade.get("final_result") == "LOSS" else 0
        bucket["net_pips"] += _f(trade.get("final_pips")) or 0.0
    return dict(grouped)


def _normalise_confluence(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v)]
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    return [str(value)]
