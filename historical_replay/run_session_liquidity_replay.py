"""Session Liquidity historical replay (advisory).

Walks the M15 candle stream forward and, at each step, asks the
``SessionLiquidityEngine`` what setups it would have produced. Rows are
written to ``data/session_liquidity_replay/<run_id>.csv`` for the
``evaluate_session_liquidity`` script to summarise.

This replay never enters trades and never modifies any other engine — it is
purely an advisory backtest of the liquidity layer.
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from analysis.session_liquidity import SessionLiquidityEngine
from data.mt5_client import MT5Client
from utils.logger import get_logger


logger = get_logger("historical_replay.session_liquidity")
OUTPUT_DIR = Path("data/session_liquidity_replay")


def run(symbol: str, months: int, output: Path | None = None) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    run_id = int(time.time())
    output = output or (OUTPUT_DIR / f"{run_id}.csv")

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=max(1, months) * 30)

    mt5 = MT5Client()
    mt5.connect()
    m15 = mt5.get_ohlcv_range("M15", start - timedelta(days=2), end)
    if m15 is None or m15.empty:
        return {"run_id": run_id, "rows": 0, "error": "no_m15_data"}

    engine = SessionLiquidityEngine()
    engine.log_config()

    fields = [
        "candle_time",
        "current_price",
        "setup_type",
        "direction",
        "swept_level",
        "swept_level_type",
        "trap_quality_score",
        "displacement",
        "targets",
        "invalidation_level",
        "liquidity_bias",
        "expected_play",
    ]
    rows_written = 0
    setups_found = 0
    sweep_seen: set[str] = set()
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        live = m15[m15["time"] >= start].reset_index(drop=True)
        for idx in range(len(live)):
            window = m15[m15["time"] <= live.iloc[idx]["time"]].reset_index(drop=True)
            if len(window) < 30:
                continue
            current_price = float(window.iloc[-1]["close"])
            summary = engine.build_market_plan_summary(window, current_price=current_price)
            for setup in summary.get("setups", []):
                key = f"{setup.get('setup_type')}|{setup.get('swept_level')}|{(setup.get('sweep_event') or {}).get('sweep_candle_time')}"
                if key in sweep_seen:
                    continue
                sweep_seen.add(key)
                sweep = setup.get("sweep_event") or {}
                writer.writerow({
                    "candle_time": str(live.iloc[idx]["time"]),
                    "current_price": current_price,
                    "setup_type": setup.get("setup_type"),
                    "direction": setup.get("direction"),
                    "swept_level": setup.get("swept_level"),
                    "swept_level_type": setup.get("swept_level_type"),
                    "trap_quality_score": sweep.get("trap_quality_score"),
                    "displacement": sweep.get("displacement_after_sweep"),
                    "targets": "/".join(f"{float(t):.2f}" for t in setup.get("target_levels", [])),
                    "invalidation_level": setup.get("invalidation_level"),
                    "liquidity_bias": summary.get("liquidity_bias"),
                    "expected_play": summary.get("expected_play"),
                })
                setups_found += 1
                rows_written += 1
            if idx and idx % 500 == 0:
                logger.info("SESSION LIQUIDITY REPLAY: progress=%d/%d setups=%d", idx, len(live), setups_found)

    return {
        "run_id": run_id,
        "rows": rows_written,
        "setups_found": setups_found,
        "output": str(output),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Spencer Session Liquidity advisory replay.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    print(run(symbol=args.symbol, months=args.months, output=args.output))


if __name__ == "__main__":
    main()
