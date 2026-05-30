"""Spencer Core Strategy Engine historical replay.

Usage:
    python -m historical_replay.run_core_strategy_replay --symbol XAUUSD --months 6

Replays the last N months of XAUUSD candles by walking M15 bars forward one at
a time, running the Core Strategy Engine on each window, and tracking what
would have happened to each candidate's TP1/2/3/SL.

Output: prints a summary to stdout AND writes a JSON evaluation file.

This evaluates ONLY the three core strategies — legacy strategy results are not
mixed in.
"""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

os.environ.setdefault("ALPHAPULSE_REPLAY_MODE", "1")

import pandas as pd

from data.mt5_client import MT5Client
from strategies.core_strategy_engine import CoreStrategyEngine, StrategySetup

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
    result: str = "open"  # "tp1" | "tp2" | "tp3" | "sl" | "open"
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
    rejected_reasons: dict[str, int] = field(default_factory=dict)

    def add_strategy(self, st: str, key: str, val: float = 1.0) -> None:
        if st not in self.by_strategy:
            self.by_strategy[st] = {"candidates": 0, "activated": 0, "wins": 0, "tp1_hits": 0, "net_pips": 0.0}
        self.by_strategy[st][key] = self.by_strategy[st].get(key, 0.0) + val

    def add_session(self, sess: str, key: str, val: float = 1.0) -> None:
        if sess not in self.by_session:
            self.by_session[sess] = {"activated": 0, "wins": 0, "net_pips": 0.0}
        self.by_session[sess][key] = self.by_session[sess].get(key, 0.0) + val


def _resolve_outcome(
    setup: StrategySetup, forward: pd.DataFrame
) -> TradeOutcome:
    """Walk forward bars after entry; first TP/SL touch wins.

    Conservative: assumes worst-case ordering inside a single bar — if both SL
    and TP are touched on the same bar, SL wins.
    """
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
            if hi >= setup.tp3:
                outcome.result = "tp3"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.tp3 - setup.entry) / pip, 1)
                return outcome
            if hi >= setup.tp2:
                outcome.result = "tp2"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.tp2 - setup.entry) / pip, 1)
                return outcome
            if hi >= setup.tp1:
                outcome.result = "tp1"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.tp1 - setup.entry) / pip, 1)
                return outcome
        else:  # SELL
            if hi >= setup.sl:
                outcome.result = "sl"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.entry - setup.sl) / pip, 1)
                return outcome
            if lo <= setup.tp3:
                outcome.result = "tp3"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.entry - setup.tp3) / pip, 1)
                return outcome
            if lo <= setup.tp2:
                outcome.result = "tp2"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.entry - setup.tp2) / pip, 1)
                return outcome
            if lo <= setup.tp1:
                outcome.result = "tp1"
                outcome.closed_at = str(ts)
                outcome.realized_pips = round((setup.entry - setup.tp1) / pip, 1)
                return outcome
    return outcome  # still open at end of replay


def run_replay(symbol: str = "XAUUSD", months: int = 6) -> dict[str, Any]:
    print(f"[core-replay] symbol={symbol} months={months}")
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=months * 30)
    mt5 = MT5Client()
    if not mt5.ensure_connected():
        print("[core-replay] WARNING: MT5 not connected — running on synthetic data")
    m15 = mt5.get_ohlcv_range("M15", start, end)
    h1 = mt5.get_ohlcv_range("H1", start, end)
    m5 = mt5.get_ohlcv_range("M5", start, end)
    if m15 is None or len(m15) < 200:
        print(f"[core-replay] Not enough M15 data ({len(m15) if m15 is not None else 0} bars)")
        return {"error": "insufficient_data"}
    print(f"[core-replay] Loaded M15={len(m15)} H1={len(h1)} M5={len(m5)}")

    engine = CoreStrategyEngine()
    metrics = ReplayMetrics()
    outcomes: list[TradeOutcome] = []
    seen_fingerprints: set[str] = set()

    # Walk M15 bars forward — for each step, run the engine on the data slice
    # ending at this bar, then resolve any new candidate's outcome on the
    # forward window.
    stride = 4  # check every 4 bars (= 1 hour) for speed
    forward_window_bars = 96  # 24 hours of M15 to resolve TP/SL
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
        data = {"M15": m15_slice, "H1": h1_slice, "M5": m5_slice}
        current_price = float(m15_slice.iloc[-1]["close"])
        result = engine.run(data, current_price=current_price, ctx=None, plan=None, symbol=symbol)
        metrics.candidates += len(result.candidates)
        metrics.rejected += len(result.rejected)
        for rej in result.rejected:
            metrics.rejected_reasons[rej.reason] = metrics.rejected_reasons.get(rej.reason, 0) + 1
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
                if outcome.result == "tp2":
                    metrics.tp2_hits += 1
                if outcome.result == "tp3":
                    metrics.tp3_hits += 1
            elif outcome.result == "sl":
                metrics.losses += 1
            metrics.net_pips += outcome.realized_pips
            metrics.add_strategy(cand.strategy_type, "net_pips", outcome.realized_pips)
            metrics.add_session(sess, "net_pips", outcome.realized_pips)
        if step % 50 == 0:
            print(f"[core-replay] step {step}/{total_steps} candidates={metrics.candidates} activated={metrics.activated}")

    summary = _build_summary(symbol, months, metrics, outcomes)
    out_path = OUTPUT_DIR / f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "latest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[core-replay] Saved: {out_path}")
    print(json.dumps({k: v for k, v in summary.items() if k != "sample_trades"}, indent=2))
    return summary


def _build_summary(symbol: str, months: int, metrics: ReplayMetrics, outcomes: list[TradeOutcome]) -> dict[str, Any]:
    win_rate = (metrics.wins / metrics.activated * 100.0) if metrics.activated else 0.0
    tp1_rate = (metrics.tp1_hits / metrics.activated * 100.0) if metrics.activated else 0.0
    tp2_rate = (metrics.tp2_hits / metrics.activated * 100.0) if metrics.activated else 0.0
    tp3_rate = (metrics.tp3_hits / metrics.activated * 100.0) if metrics.activated else 0.0
    avg_pips = (metrics.net_pips / metrics.activated) if metrics.activated else 0.0
    return {
        "symbol":      symbol,
        "months":      months,
        "candidates":  metrics.candidates,
        "activated":   metrics.activated,
        "wins":        metrics.wins,
        "losses":      metrics.losses,
        "win_rate":    round(win_rate, 1),
        "tp1_rate":    round(tp1_rate, 1),
        "tp2_rate":    round(tp2_rate, 1),
        "tp3_rate":    round(tp3_rate, 1),
        "net_pips":    round(metrics.net_pips, 1),
        "avg_pips":    round(avg_pips, 1),
        "by_strategy": metrics.by_strategy,
        "by_session":  metrics.by_session,
        "rejected_reasons": dict(sorted(metrics.rejected_reasons.items(), key=lambda kv: kv[1], reverse=True)[:20]),
        "sample_trades": [o.to_dict() for o in outcomes[:50]],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description="Spencer Core Strategy Engine replay.")
    parser.add_argument("--symbol", type=str, default="XAUUSD")
    parser.add_argument("--months", type=int, default=6)
    args = parser.parse_args()
    run_replay(symbol=args.symbol, months=args.months)


if __name__ == "__main__":
    main()
