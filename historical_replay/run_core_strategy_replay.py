"""Spencer Core Strategy Engine historical replay.

Usage:
    python -m historical_replay.run_core_strategy_replay --symbol XAUUSD --months 6
    python -m historical_replay.run_core_strategy_replay --symbol XAUUSD --months 6 --require-real-data
    python -m historical_replay.run_core_strategy_replay --symbol XAUUSD --months 6 --export-rejections 50
    python -m historical_replay.run_core_strategy_replay --symbol XAUUSD --months 6 --profile research

Walks M15 bars forward, runs the Core Strategy Engine on each window, resolves
outcomes (TP1/2/3/SL) on a 24h forward window, and writes a JSON summary with:
  * candidate funnel counts per strategy
  * rejected_reasons_by_strategy (granular)
  * data_source (mt5_live | synthetic_demo)  ← explicit, no silent fallback
  * per-strategy + per-session performance breakdowns
  * optional rejection_samples export

Evaluates ONLY the three core strategies. Legacy strategy results are not mixed.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

import pandas as pd

from data.mt5_client import MT5Client
from strategies.core_strategy_engine import (
    ALL_REJECTION_REASONS,
    CoreStrategyEngine,
    FunnelMetrics,
    StrategySetup,
)

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT_DIR / "data" / "core_strategy_replays"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class TradeOutcome:
    strategy_type: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    tp3: float
    opened_at: str
    closed_at: str = ""
    result: str = "open"
    realized_pips: float = 0.0
    session_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_type":  self.strategy_type,
            "direction":      self.direction,
            "entry":          self.entry,
            "sl":             self.sl,
            "tp1":            self.tp1,
            "tp2":            self.tp2,
            "tp3":            self.tp3,
            "opened_at":      self.opened_at,
            "closed_at":      self.closed_at,
            "result":         self.result,
            "realized_pips":  self.realized_pips,
            "session_name":   self.session_name,
        }


@dataclass
class ReplayMetrics:
    candidates: int = 0
    rejected: int = 0
    activated: int = 0
    wins: int = 0
    losses: int = 0
    tp1_hits: int = 0
    tp2_hits: int = 0
    tp3_hits: int = 0
    net_pips: float = 0.0
    by_strategy: dict[str, dict[str, float]] = field(default_factory=dict)
    by_session: dict[str, dict[str, float]] = field(default_factory=dict)
    rejected_reasons_by_strategy: dict[str, dict[str, int]] = field(default_factory=dict)
    funnels: dict[str, FunnelMetrics] = field(default_factory=dict)

    def init_strategies(self) -> None:
        # Pre-init rejected_reasons_by_strategy with all known reason keys so the
        # output always shows "this reason fired 0 times" for unfired buckets.
        for strat, reasons in ALL_REJECTION_REASONS.items():
            self.rejected_reasons_by_strategy.setdefault(strat, {r: 0 for r in reasons})
            self.funnels.setdefault(strat, FunnelMetrics(strategy_type=strat))

    def add_strategy(self, st: str, key: str, val: float = 1.0) -> None:
        if st not in self.by_strategy:
            self.by_strategy[st] = {"candidates": 0, "activated": 0, "wins": 0, "tp1_hits": 0, "net_pips": 0.0}
        self.by_strategy[st][key] = self.by_strategy[st].get(key, 0.0) + val

    def add_session(self, sess: str, key: str, val: float = 1.0) -> None:
        if sess not in self.by_session:
            self.by_session[sess] = {"activated": 0, "wins": 0, "net_pips": 0.0}
        self.by_session[sess][key] = self.by_session[sess].get(key, 0.0) + val

    def add_rejection(self, strategy: str, reason: str) -> None:
        bucket = self.rejected_reasons_by_strategy.setdefault(strategy, {})
        bucket[reason] = bucket.get(reason, 0) + 1


def _resolve_outcome(setup: StrategySetup, forward: pd.DataFrame) -> TradeOutcome:
    pip = 0.1
    direction = setup.direction.upper()
    outcome = TradeOutcome(
        strategy_type=setup.strategy_type,
        direction=direction,
        entry=setup.entry,
        sl=setup.sl,
        tp1=setup.tp1,
        tp2=setup.tp2,
        tp3=setup.tp3,
        opened_at=(
            setup.confirmation_candle_time.isoformat()
            if setup.confirmation_candle_time else ""
        ),
        session_name=setup.session_name,
    )
    if forward is None or len(forward) == 0:
        return outcome
    for _, bar in forward.iterrows():
        hi = float(bar["high"])
        lo = float(bar["low"])
        ts = bar["time"]
        if direction == "BUY":
            if lo <= setup.sl:
                outcome.result = "sl"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.sl - setup.entry) / pip, 1)
                return outcome
            for tp_lvl, tp_name in ((setup.tp3, "tp3"), (setup.tp2, "tp2"), (setup.tp1, "tp1")):
                if hi >= tp_lvl:
                    outcome.result = tp_name
                    outcome.closed_at = str(ts)
                    outcome.realized_pips = round((tp_lvl - setup.entry) / pip, 1)
                    return outcome
        else:
            if hi >= setup.sl:
                outcome.result = "sl"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.entry - setup.sl) / pip, 1)
                return outcome
            for tp_lvl, tp_name in ((setup.tp3, "tp3"), (setup.tp2, "tp2"), (setup.tp1, "tp1")):
                if lo <= tp_lvl:
                    outcome.result = tp_name
                    outcome.closed_at = str(ts)
                    outcome.realized_pips = round((setup.entry - tp_lvl) / pip, 1)
                    return outcome
    return outcome


def _load_data(
    mt5: MT5Client, start: datetime, end: datetime, *, require_real_data: bool
) -> tuple[dict[str, pd.DataFrame], str]:
    """Load M5/M15/H1 between start and end.

    Returns (data_dict, data_source). data_source is "mt5_live" or
    "synthetic_demo". When require_real_data is True and MT5 isn't usable,
    raises SystemExit instead of returning synthetic data.
    """
    mt5_ok = mt5.ensure_connected()
    using_demo = bool(getattr(mt5, "_demo_mode", False))
    if (not mt5_ok or using_demo) and require_real_data:
        print(
            "REPLAY ABORT: --require-real-data was set but MT5 is not available "
            f"(connected={mt5_ok}, demo={using_demo}). Refusing to use synthetic data."
        )
        raise SystemExit(2)
    if not mt5_ok or using_demo:
        print(
            "\nREPLAY WARNING: Synthetic demo data used. Results are not valid for "
            "strategy performance.\n"
        )
    m15 = mt5.get_ohlcv_range("M15", start, end)
    h1 = mt5.get_ohlcv_range("H1", start, end)
    m5 = mt5.get_ohlcv_range("M5", start, end)
    data_source = "mt5_live" if (mt5_ok and not using_demo) else "synthetic_demo"
    return {"M15": m15, "H1": h1, "M5": m5}, data_source


def run_replay(
    *,
    symbol: str = "XAUUSD",
    months: int = 6,
    require_real_data: bool = False,
    export_rejections: int = 0,
    profile_override: str | None = None,
) -> dict[str, Any]:
    if profile_override:
        os.environ["CORE_STRATEGY_PROFILE"] = profile_override
        # Re-import settings is overkill — profile resolver reads from config
        # at import time. For a runner like this, re-importing is cleanest:
        import importlib
        import config.settings as _settings
        importlib.reload(_settings)
        import strategies.core_strategy_engine as _cse
        importlib.reload(_cse)

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=months * 30)
    print(f"[core-replay] symbol={symbol} months={months} profile={profile_override or 'env-default'}")

    mt5 = MT5Client()
    data, data_source = _load_data(mt5, start, end, require_real_data=require_real_data)
    m15 = data.get("M15")
    h1 = data.get("H1")
    m5 = data.get("M5")
    if m15 is None or len(m15) < 200:
        print(f"[core-replay] Not enough M15 data ({len(m15) if m15 is not None else 0} bars)")
        return {"error": "insufficient_data", "data_source": data_source}
    print(f"[core-replay] Loaded data_source={data_source} M15={len(m15)} H1={len(h1) if h1 is not None else 0} M5={len(m5) if m5 is not None else 0}")

    engine = CoreStrategyEngine()
    metrics = ReplayMetrics()
    metrics.init_strategies()
    outcomes: list[TradeOutcome] = []
    seen_fingerprints: set[str] = set()
    rejection_samples: dict[str, list[dict[str, Any]]] = {s: [] for s in ALL_REJECTION_REASONS.keys()}

    stride = 4
    forward_window_bars = 96
    total_steps = max(0, (len(m15) - 200 - forward_window_bars) // stride)
    for step, i in enumerate(range(200, len(m15) - forward_window_bars, stride)):
        m15_slice = m15.iloc[: i + 1].copy()
        end_ts = m15_slice.iloc[-1]["time"]
        try:
            end_ts_utc = pd.Timestamp(end_ts)
            if end_ts_utc.tz is None:
                end_ts_utc = end_ts_utc.tz_localize("UTC")
        except Exception:
            continue
        h1_slice = h1[h1["time"] <= end_ts_utc] if h1 is not None and len(h1) > 0 else pd.DataFrame()
        m5_slice = m5[m5["time"] <= end_ts_utc] if m5 is not None and len(m5) > 0 else pd.DataFrame()
        scan_data = {"M15": m15_slice, "H1": h1_slice, "M5": m5_slice}
        current_price = float(m15_slice.iloc[-1]["close"])
        result = engine.run(scan_data, current_price=current_price, ctx=None, plan=None, symbol=symbol)
        metrics.candidates += len(result.candidates)
        metrics.rejected += len(result.rejected)

        # Per-strategy rejection histogram
        for rej in result.rejected:
            metrics.add_rejection(rej.strategy_type, rej.reason)
            # Capture up to N sample rejections per strategy for later export
            samples = rejection_samples.get(rej.strategy_type)
            if samples is not None and export_rejections > 0 and len(samples) < export_rejections:
                if random.random() < min(1.0, (export_rejections * 1.5) / max(metrics.rejected, 1)):
                    last_bar = m15_slice.iloc[-1]
                    samples.append({
                        "timestamp":        str(end_ts_utc),
                        "strategy":         rej.strategy_type,
                        "direction":        rej.direction,
                        "attempted_level":  rej.level,
                        "current_price":    current_price,
                        "rejection_reason": rej.reason,
                        "candle":           {
                            "open":   float(last_bar["open"]),
                            "high":   float(last_bar["high"]),
                            "low":    float(last_bar["low"]),
                            "close":  float(last_bar["close"]),
                        },
                        "detail":           rej.detail,
                    })

        # Funnel rollup
        for st, fm in (result.funnel or {}).items():
            metrics.funnels[st].merge(fm)

        for cand in result.candidates:
            fp = cand.fingerprint()
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            forward = m15.iloc[i + 1 : i + 1 + forward_window_bars]
            outcome = _resolve_outcome(cand, forward)
            outcomes.append(outcome)
            metrics.activated += 1
            metrics.add_strategy(cand.strategy_type, "activated", 1)
            metrics.add_strategy(cand.strategy_type, "candidates", 1)
            sess = cand.session_name or "unknown"
            metrics.add_session(sess, "activated", 1)
            if outcome.result in ("tp1", "tp2", "tp3"):
                metrics.wins += 1
                metrics.add_strategy(cand.strategy_type, "wins", 1)
                metrics.add_session(sess, "wins", 1)
                if outcome.result == "tp1":
                    metrics.tp1_hits += 1
                    metrics.add_strategy(cand.strategy_type, "tp1_hits", 1)
                elif outcome.result == "tp2":
                    metrics.tp2_hits += 1
                elif outcome.result == "tp3":
                    metrics.tp3_hits += 1
            elif outcome.result == "sl":
                metrics.losses += 1
            metrics.net_pips += outcome.realized_pips
            metrics.add_strategy(cand.strategy_type, "net_pips", outcome.realized_pips)
            metrics.add_session(sess, "net_pips", outcome.realized_pips)

        if step % 50 == 0:
            print(f"[core-replay] step {step}/{total_steps} candidates={metrics.candidates} activated={metrics.activated}")

    summary = _build_summary(symbol, months, metrics, outcomes, data_source, profile_override)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out_path = OUTPUT_DIR / f"{run_id}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if export_rejections > 0:
        sample_path = OUTPUT_DIR / f"rejection_samples_{run_id}.json"
        sample_path.write_text(
            json.dumps({k: v for k, v in rejection_samples.items() if v}, indent=2),
            encoding="utf-8",
        )
        print(f"[core-replay] Rejection samples saved: {sample_path}")

    print(f"\n[core-replay] Saved: {out_path}")
    # Compact display: drop sample_trades from the printed summary
    display = {k: v for k, v in summary.items() if k != "sample_trades"}
    print(json.dumps(display, indent=2))
    return summary


def _build_summary(
    symbol: str,
    months: int,
    metrics: ReplayMetrics,
    outcomes: list[TradeOutcome],
    data_source: str,
    profile: str | None,
) -> dict[str, Any]:
    win_rate = (metrics.wins / metrics.activated * 100.0) if metrics.activated else 0.0
    tp1_rate = (metrics.tp1_hits / metrics.activated * 100.0) if metrics.activated else 0.0
    tp2_rate = (metrics.tp2_hits / metrics.activated * 100.0) if metrics.activated else 0.0
    tp3_rate = (metrics.tp3_hits / metrics.activated * 100.0) if metrics.activated else 0.0
    avg_pips = (metrics.net_pips / metrics.activated) if metrics.activated else 0.0
    return {
        "symbol":         symbol,
        "months":         months,
        "data_source":    data_source,
        "data_source_note": (
            "REPLAY WARNING: Synthetic demo data used. Results are not valid for "
            "strategy performance."
            if data_source == "synthetic_demo" else
            "MT5 live historical data."
        ),
        "profile":        profile or os.getenv("CORE_STRATEGY_PROFILE", "balanced"),
        "candidates":     metrics.candidates,
        "activated":      metrics.activated,
        "wins":           metrics.wins,
        "losses":         metrics.losses,
        "win_rate":       round(win_rate, 1),
        "tp1_rate":       round(tp1_rate, 1),
        "tp2_rate":       round(tp2_rate, 1),
        "tp3_rate":       round(tp3_rate, 1),
        "net_pips":       round(metrics.net_pips, 1),
        "avg_pips":       round(avg_pips, 1),
        "by_strategy":    metrics.by_strategy,
        "by_session":     metrics.by_session,
        "rejected_reasons_by_strategy": metrics.rejected_reasons_by_strategy,
        "funnel":         {k: v.to_dict() for k, v in metrics.funnels.items()},
        "sample_trades":  [o.to_dict() for o in outcomes[:50]],
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Spencer Core Strategy Engine replay.")
    parser.add_argument("--symbol", type=str, default="XAUUSD")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument(
        "--require-real-data",
        action="store_true",
        help="Abort if MT5 is unavailable instead of falling back to synthetic.",
    )
    parser.add_argument(
        "--export-rejections",
        type=int,
        default=0,
        metavar="N",
        help="Sample up to N rejected candidates per strategy to "
             "rejection_samples_<run_id>.json for tuning.",
    )
    parser.add_argument(
        "--profile",
        type=str,
        choices=["strict", "balanced", "research"],
        default=None,
        help="Override CORE_STRATEGY_PROFILE for this run.",
    )
    args = parser.parse_args()
    run_replay(
        symbol=args.symbol,
        months=args.months,
        require_real_data=args.require_real_data,
        export_rejections=args.export_rejections,
        profile_override=args.profile,
    )


if __name__ == "__main__":
    main()
